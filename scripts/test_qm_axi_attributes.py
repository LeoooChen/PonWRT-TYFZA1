#!/usr/bin/env python3
"""Compare native EQ/EPP attribute writes with this board's factory init."""
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_X21

stock = root / 'analysis/private/live-files'
config = (stock / 'etc/default_scfg.txt').read_text()


def values(name):
    body = re.search(r'\b' + name + r'\s*=\s*\{([^}]+)\}', config)[1]
    return [int(value.strip(), 0) for value in body.split(',')]


calls = []


def boundary(name, args):
    if name == 'aal_l3qm_set_epp_axi_attrib':
        calls.append((args[0], args[1]))
        return 0
    if name == 'printk':
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(stock / 'lib/modules/5.10.70/extra/ca-ne.ko',
                      ['aal_l3qm_init_empty_buffer'], boundary)
cfg, flags = 0x6000000, 0x6010000
for address in (cfg, flags):
    oracle.uc.mem_map(address, 0x10000)
oracle.uc.mem_write(oracle.symbols['ca_aal_debug'][0], bytes(4))
eq = values('CFG_ID_L3QM_AXI_ATTRIB_EQ_VALUES')
epp = values('CFG_ID_L3QM_AXI_ATTRIB_CPU_EPP_VALUES')
oracle.uc.mem_write(cfg + 0x168, struct.pack('<16I', *eq))
oracle.uc.mem_write(cfg + 0x1a8, struct.pack('<8I', *epp))
oracle.uc.mem_write(flags + 13, bytes([1]))
stack = oracle.scratch + 0xfe00
oracle.word(stack + 0x68, flags)
oracle.uc.reg_write(UC_ARM64_REG_SP, stack)
oracle.uc.reg_write(UC_ARM64_REG_X21, cfg)
start = oracle.functions['aal_l3qm_init_empty_buffer']
# Execute the actual two attribute loops; allocation/startup are outside scope.
oracle.uc.emu_start(start + 0x578, start + 0x654, count=20000)
expected = dict(calls)
assert len(calls) == 24

drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net'
core = (drivers / 'ethernet/realtek/rtl9617c-qm.c').read_text()
radio = (drivers / 'wireless/realtek/rtl_ax_ap/ni-test.h').read_text()
begin = core.index('\tret = rtl_qm_attribute(', core.index('int rtl_qm_rx_start('))
if core.rfind('\tfor (i =', 0, begin) > core.rfind('\twritel(', 0, begin):
    begin = core.rfind('\tfor (i =', 0, begin)
core_writes = core[begin:core.index('\tvalue = readl(ni + 0x6000)', begin)]
prepare = radio[radio.index('static int ni_test_prepare('):]
attrs = prepare[prepare.index('\tstatic const u32'):prepare.index('\tunsigned int i;')]
begin = prepare.index('\tret = attr_write(')
radio_writes = prepare[begin:prepare.index('\tfor (i = 0; i < 8;', begin)]
fixture = r'''
#include <stdio.h>
typedef unsigned int u32;
#define EQ_ID 4
#define RTL_QM_PE_EQ 6
#define RTL_QM_PE_POOLS 2
#define RTL_DQ_EQ_PRIVATE 2
#define RTL_DQ_EQ_SHARED 3
struct rtl_qm_rx { int unused; };
struct rtl_ax_tx { struct { unsigned int eq, cpu; } ni; };
static int rtl_qm_attribute(struct rtl_qm_rx *rx, unsigned int entry, u32 value)
{ (void)rx; printf("core %u %u\n", entry, value); return 0; }
static int attr_write(struct rtl_ax_tx *tx, unsigned int entry, const u32 *value)
{ (void)tx; printf("radio %u %u\n", entry, value[0]); return value[1] != 0; }
static int core_init(struct rtl_qm_rx *rx) { unsigned int i; int ret;
'''
fixture += core_writes + 'return 0;}\n'
fixture += 'static int radio_init(struct rtl_ax_tx *tx) { int ret;\n'
fixture += attrs + radio_writes + 'return 0;}\n'
fixture += '''int main(void) {
struct rtl_qm_rx rx = {0}; struct rtl_ax_tx tx = {{6, 3}};
if (core_init(&rx) || radio_init(&tx)) return 1;
tx.ni.eq = 7; tx.ni.cpu = 2; return radio_init(&tx);
}
'''
with tempfile.TemporaryDirectory(prefix='qm-axi-') as temp:
    temp = Path(temp)
    (temp / 'test.c').write_text(fixture)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(temp / 'test.c'),
                    '-o', str(temp / 'test')], check=True)
    output = subprocess.check_output([str(temp / 'test')], text=True)
    entries = {'core': [], 'radio': []}
    for line in output.splitlines():
        stage, entry, actual = line.split()
        entry, actual = int(entry), int(actual)
        entries[stage].append(entry)
        assert actual == expected[entry], (stage, entry, hex(actual), hex(expected[entry]))
    assert sorted(entries['core']) == list(range(16)) + [48]
    assert sorted(entries['radio']) == [6, 7, 50, 51]
print('Native core and both radio EQ/EPP writes match actual factory init with board startup arrays.')
