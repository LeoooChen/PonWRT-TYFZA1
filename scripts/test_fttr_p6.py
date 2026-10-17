#!/usr/bin/env python3
"""Compare P6 configuration writes to original ARM64 execution; no live MMIO."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from trace_stock_fttr_p6 import trace
from prepare_fttr_serdes import convert


def function(text, name):
    start = re.search(r'^(?:static )?(?:int|void|u16|bool) ' + name + r'\(', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


shim = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32; typedef uint16_t u16; typedef uint8_t u8;
#define __iomem
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
struct device { int unused; }; struct reset_control { int unused; };
struct firmware { size_t size; const u8 *data; };
#include "rtl9617c-fttr-p6.h"
unsigned int events[2048][3],count;
static u32 ni[0x4000], qx[64], glb[64];
static struct firmware profile;
static int firmware_error, reset_error;
static u32 get_unaligned_le32(const void *p) { u32 v;memcpy(&v,p,4);return v; }
static u32 readl(void *p) { return *(u32 *)p; }
static void writel(u32 value,void *p) {
 uintptr_t address=(uintptr_t)p;u32 region,offset;
 if(address>=(uintptr_t)ni && address<(uintptr_t)(ni+0x4000)) {
  region=2;offset=address-(uintptr_t)ni;
 } else if(address>=(uintptr_t)qx && address<(uintptr_t)(qx+64)) {
  region=1;offset=address-(uintptr_t)qx;
 } else {region=0;offset=address-(uintptr_t)glb;}
 events[count][0]=region;events[count][1]=offset;events[count++][2]=value;
 *(u32 *)p=value;
}
static void udelay(unsigned int us) { (void)us; }
static void msleep(unsigned int ms) { (void)ms; }
static void usleep_range(unsigned int lo,unsigned int hi) {(void)lo;(void)hi;}
static int reset_control_assert(struct reset_control *reset) {
 (void)reset;if(reset_error)return reset_error;
 writel(readl(&glb[0x98/4])|BIT(31),&glb[0x98/4]);return 0;
}
static int reset_control_deassert(struct reset_control *reset) {
 (void)reset;writel(readl(&glb[0x98/4])&~BIT(31),&glb[0x98/4]);return 0;
}
static int request_firmware_direct(const struct firmware **fw,const char *name,struct device *dev) {
 (void)name;(void)dev;*fw=&profile;return firmware_error;
}
static void release_firmware(const struct firmware *fw) {(void)fw;}
#define readl_poll_timeout(addr,value,condition,delay,timeout) \
 ((value)=readl(addr),(condition)?0:-ETIMEDOUT)
'''

wrapper = r'''
void set_profile(const u8 *data,unsigned int size) {profile.data=data;profile.size=size;}
int configured;
int run(unsigned int mode,int nway,unsigned int seed,int no_file,int no_clock,int bad_reset) {
 struct rtl_fttr_p6 p6={.ni=ni,.qx=qx};int ret;
 for(unsigned int i=0;i<0x4000;i++)ni[i]=seed;
 for(unsigned int i=0;i<64;i++)qx[i]=glb[i]=seed;
 qx[0x58/4]=no_clock ? 0 : seed|0xe000;
 qx[0x54/4]=0x111;
 firmware_error=no_file ? -ENOENT : 0;
 reset_error=bad_reset ? -EIO : 0;count=0;
 ret=rtl_fttr_p6_configure(&p6,mode,nway);
 configured=p6.configured;
 return ret;
}
int link_status(unsigned int mode,int configured,int clock,unsigned int pcs) {
 struct rtl_fttr_p6 p6={.ni=ni,.qx=qx,.mode=mode,.configured=configured};
 count=0;
 qx[0x58/4]=clock ? BIT(13) : 0;
 /* Model the status read independently of the configuration input seed. */
 qx[0x54/4]=pcs;
 return rtl_fttr_p6_link(&p6);
}
unsigned int link_register(void) { return qx[0x4c/4]&0xffff; }
'''


def config_writes(records):
    # Clock status also reads diagnostic indirect registers in stock. Exclude
    # read-address commands, retaining every register data/configuration write.
    return [row for row in records if not (row[0] == 1 and row[1] == 0x4c and not row[2] & (1 << 30))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    text = (headers / 'rtl9617c-fttr-p6.c').read_text()
    names = ['p6_update', 'p6_ind_read', 'p6_ind_write', 'p6_profile_validate',
        'p6_profile_load', 'p6_mac_configure', 'rtl_fttr_p6_stop', 'rtl_fttr_p6_link',
        'rtl_fttr_p6_configure']
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text(shim + '\n'.join(function(text, n) for n in names) + wrapper)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-I', str(headers),
                        str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        library.set_profile.argtypes = [ctypes.c_char_p, ctypes.c_uint]
        library.run.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int]

        def records():
            count = ctypes.c_uint.in_dll(library, 'count').value
            array = (ctypes.c_uint * 3 * 2048).in_dll(library, 'events')
            return [list(array[i]) for i in range(count)]

        for mode in (5, 9):
            for seed in (0, 0xffffffff):
                expected, source = trace(args.stock, args.profiles, mode, True, seed)
                firmware = convert(source)
                library.set_profile(firmware, len(firmware))
                assert library.run(mode, True, seed, 0, 0, 0) == 0
                actual = records()
                # Native additionally quiesces P6 before touching reset/profile.
                assert actual[:3] == [[2, 0x2300, seed & ~(1 << 6)],
                    [2, 0x6108, seed & ~(1 << 14)], [1, 0, seed | 1]]
                a, b = config_writes(actual[3:]), config_writes(expected)
                assert a == b, (mode, hex(seed), next(((i,x,y) for i,(x,y) in enumerate(zip(a,b)) if x!=y), ('length',len(a),len(b))))
                cases.append({'mode': mode, 'seed': hex(seed), 'configuration_writes': len(a),
                    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'stock_native_match': True})
        for no_file, no_clock, bad_reset, expected in [(1,0,0,-2),(0,1,0,-110),(0,0,1,-5)]:
            assert library.run(9, True, 0, no_file, no_clock, bad_reset) == expected
            assert not ctypes.c_int.in_dll(library, 'configured').value
            assert not any(row[0]==2 and 0xb200<=row[1]<=0xb2a8 for row in records())
        bad = b'broken'
        library.set_profile(bad,len(bad))
        assert library.run(9, True, 0, 0, 0, 0) == -22
        for mode, good, wrong, reg in [(5,0x10,0x1000,0x3e),(9,0x1000,0x10,0xa0)]:
            for configured, clock, pcs, expected in [(1,1,good,1),(1,1,wrong,0),
                                                     (1,0,good,0),(0,1,good,0)]:
                assert library.link_status(mode,configured,clock,pcs) == expected
                if configured and clock:
                    assert library.link_register() == reg
                    assert ctypes.c_uint.in_dll(library,'count').value == 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'cases': cases, 'missing_or_invalid_profile_stops': True, 'clock_failure_stops_before_mac': True,
        'reset_error_propagated': True, 'hardware_verified': False,
        'scope': 'configuration writes; stock diagnostic read-address commands omitted; timing/clock modeled'}, indent=2)+'\n')
    print('P6: 4 stock/native configuration sequences match, including profile/MAC/NWAY; startup failures stop')


if __name__ == '__main__':
    main()
