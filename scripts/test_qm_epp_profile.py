#!/usr/bin/env python3
"""Compare EPP profile field preservation with the board's actual stock code."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X1, UC_ARM64_REG_SP

oracle = ModuleOracle(root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',
                     ['aal_l3qm_init_cpu_epp'], lambda name, args: 0,
                     external_symbols={'vabits_actual': 0x5007000,
                                       'memstart_addr': 0x5007010, 'kimage_voffset': 0x5007020})
ni = 0x6000000
oracle.uc.mem_map(ni, 0x10000)
oracle.word(oracle.symbols['g_ne_reg_iobase'][0], ni)
oracle.uc.mem_write(oracle.symbols['l3qm_cpu_epp_per_voq'][0], struct.pack('<I', 256))
oracle.uc.mem_write(oracle.symbols['ca_ni_napi_budget'][0], struct.pack('<I', 16))
oracle.uc.mem_write(oracle.symbols['l3qm_epp_profile_cpu_id'][0], bytes([4]))
expected = {}
for upper in (0, 3, 7):
    before = upper << 29 | 0x8001
    oracle.uc.mem_write(ni + 0x658c, struct.pack('<I', before))
    oracle.uc.reg_write(UC_ARM64_REG_X1, ni)
    oracle.uc.reg_write(UC_ARM64_REG_SP, oracle.scratch + 0xfe00)
    # The saved ADRP value is a symbol page; the later LDR has its low offset.
    oracle.word(oracle.scratch + 0xfe70, oracle.symbols['g_ne_reg_iobase'][0] & ~4095)
    start = oracle.functions['aal_l3qm_init_cpu_epp']
    oracle.uc.emu_start(start + 0xcc, start + 0x15c, count=10000)
    expected[before] = struct.unpack('<I', oracle.uc.mem_read(ni + 0x658c, 4))[0]
    assert expected[before] == upper << 29 | 0x40f2

drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net'
core = (drivers / 'ethernet/realtek/rtl9617c-qm.c').read_text()
radio = (drivers / 'wireless/realtek/rtl_ax_ap/ni-test.h').read_text()
statements = []
for source, marker in ((core, 'RTL_QM_RING_BYTES / 16'),
                       (core, 'RTL9617C_WIFI_EPP_BYTES / 16'), (radio, 'RING_BYTES / 16')):
    at = source.index(marker)
    statements.append(source[source.rfind('writel(', 0, at):source.index(';', at) + 1])
fixture = r'''
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
typedef unsigned int u32;
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
#define EQ_ID 4
#define RTL_QM_RING_BYTES 1024
#define RTL9617C_WIFI_EPP_BYTES 32768
#define RING_BYTES RTL9617C_WIFI_EPP_BYTES
static u32 value;
static u32 readl(void *p) {(void)p;return value;}
static void writel(u32 v,void *p) {(void)p;value=v;}
static unsigned char registers[0x10000];
struct tx {struct {void *regs;u32 registers[14];} ni;};
int main(void) {
 void *ni=registers;unsigned profile=7;
 struct tx state={.ni={.regs=registers,.registers={[9]=0x6598}}},*tx=&state;
 for(unsigned upper=0;upper<8;upper++) {
'''
for index, statement in enumerate(statements):
    size = 0x4000 if index == 0 else 0x80000
    fixture += 'value=(upper<<29)|0x8001;\n' + statement + '\n'
    fixture += f'assert(value==((upper<<29)|{size}|0xf2));\n'
fixture += ' } puts("All three native EPP profile writes preserve upper fields"); }\n'
with tempfile.TemporaryDirectory(prefix='epp-profile-', dir=root / 'artifacts') as temp:
    temp = Path(temp)
    (temp / 'test.c').write_text(fixture)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    str(temp / 'test.c'), '-o', str(temp / 'test')], check=True)
    subprocess.run([str(temp / 'test')], check=True)
print(json.dumps({'factory_instruction_slice': '59cac..59d38',
                  'factory_results': {hex(k): hex(v) for k, v in expected.items()},
                  'runtime_throughput_claim': False}, indent=2))
