#!/usr/bin/env python3
"""Compare S3 speed transitions with the board's actual OEM ARM64 NI module."""
from pathlib import Path
import ctypes
import hashlib
import json
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_MEM_INVALID
from unicorn.arm64_const import UC_ARM64_REG_PC

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/ca-ne.ko'
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-sgmii.h'
common = r'''
#include <stdint.h>
#include <stddef.h>
typedef uint32_t u32;
#define __iomem
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
static u32 memory[2048];
unsigned int events[128][3], count;
static u32 readl(const void *p) { return *(const u32 *)p; }
static void writel(u32 value, void *p) {
    events[count][0]='w'; events[count][1]=(char *)p-(char *)memory;
    events[count++][2]=value; *(u32 *)p=value;
}
static void udelay(unsigned int delay) {
    events[count][0]='d'; events[count][1]=delay; events[count++][2]=0;
}
'''
wrapper = r'''
void reset(unsigned int seed) { for(unsigned i=0;i<2048;i++)memory[i]=seed; }
void run(unsigned int speed) {
    count=0;
    rtl_sgmii_link((char *)memory+0xc4,(char *)memory+0xa0,
                  (char *)memory+0xd4,(char *)memory+0x1000,speed);
}
'''


def factory(seed):
    trace = []

    def call(name, args):
        if name == 'ca_udelay':
            trace.append(['d', args[0], 0])
            return 0
        raise AssertionError((name, args))

    oracle = ModuleOracle(module, ['__p4_sds_mode_set', '__p4_sds_1g_patch'], call)
    base = 0x6000000
    oracle.uc.mem_map(base, 0x2000)
    oracle.uc.mem_write(base, seed.to_bytes(4, 'little') * 2048)
    oracle.word(oracle.symbols['ca_venus_pon_glb_reg_iobase'][0], base)
    # The OEM mapping starts at f433a000; native analog starts at f433b000.
    oracle.word(oracle.symbols['g_ne_sgmii_lp_reg_iobase'][0], base)

    def write(uc, access, address, size, value, context):
        if base <= address < base + 0x2000:
            assert size == 4
            trace.append(['w', address - base, value])

    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, write)
    def invalid(uc, access, address, size, value, context):
        raise AssertionError(('unmodeled access', hex(address), hex(uc.reg_read(UC_ARM64_REG_PC))))
    oracle.uc.hook_add(UC_HOOK_MEM_INVALID, invalid)
    return oracle, trace


# OEM __p4_sds_mode_set's speed enum, verified from its PCS programming.
speeds = {10: 6, 100: 1, 1000: 2, 2500: 3}
rows = []
with tempfile.TemporaryDirectory(prefix='tyf-sgmii-oem-') as directory:
    work = Path(directory)
    source, library = work / 'native.c', work / 'native.so'
    source.write_text(common + header.read_text() + wrapper)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-shared',
                    '-fPIC', str(source), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library))
    for seed in (0, 0xffffffff, 0xa5a5a5a5, 0x34200248):
        oracle, trace = factory(seed)
        for previous in speeds:
            for target in speeds:
                oracle.uc.mem_write(0x6000000, seed.to_bytes(4, 'little') * 2048)
                native.reset(ctypes.c_uint(seed))
                for speed in (previous, target):
                    trace.clear()
                    try:
                        result = oracle.call('__p4_sds_mode_set', 1, speeds[speed])
                    except Exception:
                        print('OEM fault', hex(seed), previous, target, speed,
                              hex(oracle.uc.reg_read(UC_ARM64_REG_PC)), flush=True)
                        raise
                    assert result == 0
                    native.run(speed)
                    count = ctypes.c_uint.in_dll(native, 'count').value
                    events = ((ctypes.c_uint * 3) * 128).in_dll(native, 'events')
                    actual = [[chr(events[i][0]), events[i][1], events[i][2]] for i in range(count)]
                    assert actual == trace, (hex(seed), previous, target, speed, actual, trace)
                rows.append({'seed': hex(seed), 'from': previous, 'to': target,
                             'writes_and_delays_match_OEM': True})
report = {'OEM_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'native_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
          'transitions': len(rows), 'cases': rows, 'hardware_test': False}
(root / 'logs/sgmii-oem-tests.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({key: value for key, value in report.items() if key != 'cases'}, indent=2))
