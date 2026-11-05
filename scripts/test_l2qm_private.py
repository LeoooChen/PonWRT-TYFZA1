#!/usr/bin/env python3
"""Compare the actual native init prefix with this board's ARM64 L2QM init."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

relative = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c'
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/ca-ne.ko'
source = (root / relative).read_text()
old = subprocess.check_output(['git', 'show', '71ae24a:' + relative], cwd=root, text=True)


def native_program(text, directory, name):
    start = text.index('if (priv->dma_tx) {', text.index('static int rtl_engine_start('))
    body = text[start + len('if (priv->dma_tx) {'):text.index('/* Port15 uses scheduler', start)]
    program = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
struct rtl_xram { unsigned char *ni; };
static u32 readl(void *p) { u32 v; memcpy(&v,p,4); return v; }
static void writel(u32 v,void *p) { memcpy(p,&v,4); }
static void rtl_update(struct rtl_xram *priv,unsigned int reg,u32 mask,u32 value)
{ writel((readl(priv->ni+reg)&~mask)|value,priv->ni+reg); }
int main(int argc,char **argv) {
 unsigned char memory[0x10000]={0};struct rtl_xram value={memory},*priv=&value;
 if(argc!=4)return 1;
 writel(strtoul(argv[1],0,16),memory+0x2208);
 writel(strtoul(argv[2],0,16),memory+0x2218);
 writel(strtoul(argv[3],0,16),memory+0x2210);
''' + body + r'''
 printf("%08x %08x %08x\n",readl(memory+0x2208),readl(memory+0x2218),readl(memory+0x2210));
}
'''
    path = directory / (name + '.c')
    path.write_text(program)
    binary = directory / name
    subprocess.run(['cc', '-std=gnu11', '-O2', str(path), '-o', str(binary)], check=True)
    return binary


def factory(initial):
    def boundary(name, args):
        if name == 'aal_scfg_read':
            # Hold the established non-congestion setting constant. The
            # missing private reservation is independent of startup config.
            oracle.uc.mem_write(args[2], struct.pack('<I', 2048))
            return 0
        if name == 'ca_printf':
            return 0
        raise AssertionError((name, args))
    oracle = ModuleOracle(module, ['aal_l2_qm_init', 'aal_l2_qm_eq_cfg_set',
                                  'aal_l2_qm_buff_cfg_set',
                                  'aal_l2_qm_port_prvt_profile_set.part.0'], boundary)
    oracle.uc.mem_map(0x6000000, 0x10000)
    oracle.word(oracle.symbols['g_ne_reg_iobase'][0], 0x6000000)
    for offset, value in zip((0x2208, 0x2218, 0x2210), initial):
        oracle.uc.mem_write(0x6000000 + offset, struct.pack('<I', value))
    assert oracle.call('aal_l2_qm_init', 0) == 0
    return tuple(oracle.unpack(0x6000000 + offset, 'I')[0]
                 for offset in (0x2208, 0x2218, 0x2210))


cases = [(0x80040008, 0x00100000, 0x08008021),
         (0, 0, 0), (0xffffffff, 0xffffffff, 0),
         (0x40abfeed, 0xdead8000, 0x08008021)]
rows = []
with tempfile.TemporaryDirectory() as directory:
    directory = Path(directory)
    current = native_program(source, directory, 'current')
    previous = native_program(old, directory, 'previous')
    for values in cases:
        expected = factory(values)
        actual = tuple(int(x, 16) for x in subprocess.check_output(
            [str(current), *(f'{x:x}' for x in values)], text=True).split())
        before = tuple(int(x, 16) for x in subprocess.check_output(
            [str(previous), *(f'{x:x}' for x in values)], text=True).split())
        assert actual == expected, (values, expected, actual)
        assert before != expected, (values, before)
        rows.append({'initial': [hex(x) for x in values],
                     'original': [hex(x) for x in expected],
                     'current': [hex(x) for x in actual],
                     'old_counterexample': [hex(x) for x in before]})
print(json.dumps({'scope': 'Native init statements compiled from the driver, compared with actual original ARM64 init/setters. No RF, traffic timing or whole-driver execution claimed.',
                  'cases': rows}, indent=2))
