#!/usr/bin/env python3
"""Compare native STF DLE/HFC writes with the TYFZA1 C/XB binary tables."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_module_oracle import ModuleOracle

driver = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
module = ROOT / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
source = r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include "mac-runtime.h"
static unsigned int regs[0x10000 / 4];
static unsigned int read32(void *ctx, unsigned int reg) { return regs[reg / 4]; }
static void write32(void *ctx, unsigned int reg, unsigned int value) {
 regs[reg / 4] = value;
 if (reg != 0x8400 && reg != 0x8404) printf("%x %x\n", reg, value);
}
static void delay(void *ctx, unsigned int us) {}
static int poll(void *ctx, unsigned int reg, unsigned int mask, unsigned int val,
                unsigned int us, unsigned int count, unsigned int *last) {
 assert(reg == 0x8d00 || reg == 0x9100); *last = val; return 0;
}
int main(int argc, char **argv) {
 for (unsigned int i = 0; i < sizeof(regs) / sizeof(*regs); i++) regs[i] = 0xa5a5a5a5;
 struct rtl_ax_io io = {.read32=read32, .write32=write32, .poll32=poll, .delay_us=delay};
 assert(!rtl_ax_dle_scc(&io)); rtl_ax_hfc_scc(&io, strtoul(argv[1], NULL, 0));
}
'''
rows = []
with tempfile.TemporaryDirectory() as temp:
    cfile, binary = Path(temp) / 'native.c', Path(temp) / 'native'
    cfile.write_text(source)
    subprocess.run(['cc', '-O2', '-I', str(driver), str(cfile), '-o', str(binary)], check=True)
    for chip, chip_id in [('8852c', 0x52), ('8192xb', 0x53)]:
        operations = ['dle_mix_cfg', 'wde_quota_cfg', 'ple_quota_cfg',
                      'set_fc_page_ctrl_reg', 'set_fc_pubpg', 'set_fc_mix_cfg', 'set_fc_func_en']
        names = [name + '_' + chip for name in operations]
        writes, regs = [], {}

        def io(name, args):
            if name == 'read32':
                return regs.get(args[1], 0xa5a5a5a5)
            assert name == 'write32', (name, args)
            regs[args[1]] = args[2]
            writes.append((args[1], args[2]))
            return 0

        oracle = ModuleOracle(module, names, io)
        adapter, ops, interface, hfc = [oracle.scratch + n for n in (0, 0x1000, 0x2000, 0x3000)]
        oracle.word(adapter, interface)
        oracle.word(interface, ops)
        oracle.word(ops + 0x20, oracle.callback('read32'))
        oracle.word(ops + 0x28, oracle.callback('write32'))
        oracle.word(adapter + 0x1d0, hfc)
        oracle.uc.mem_write(hfc + 2, b'\x01')  # MAC_AX_HCIFC_STF
        for offset, name in [(8, 'hfc_chcfg_pcie_scc_stf_'),
                             (0x18, 'hfc_pubcfg_pcie_scc_stf_'), (0x28, 'hfc_preccfg_pcie_stf_')]:
            oracle.word(hfc + offset, oracle.symbols[name + chip][0])
        table, size = oracle.symbols['dle_mem_pcie_' + chip]
        record = next(table + off for off in range(0, size, 56)
                      if oracle.unpack(table + off, 'I')[0] == 2)  # SCC_STF
        _, _, wmin, wmax, pmin, pmax = oracle.unpack(record + 8, '6Q')
        assert oracle.call(names[0], adapter, record) == 0
        assert oracle.call(names[1], adapter, wmin, wmax) == 0
        assert oracle.call(names[2], adapter, pmin, pmax) == 0
        assert oracle.call(names[6], adapter, 0, 0) == 0
        for channel in range(12):
            if chip == '8192xb' and (0x0cf0 >> channel) & 1:
                continue
            assert oracle.call(names[3], adapter, channel) == 0
        assert oracle.call(names[4], adapter) == 0
        assert oracle.call(names[5], adapter) == 0
        assert oracle.call(names[6], adapter, 1, 1) == 0
        actual = [tuple(int(v, 16) for v in line.split()) for line in
                  subprocess.check_output([str(binary), str(chip_id)], text=True).splitlines()]
        assert actual == writes, (chip, actual, writes)
        prec = oracle.symbols['hfc_preccfg_pcie_stf_' + chip][0]
        wp = oracle.unpack(prec + 4, 'H')[0] * 64
        assert wp == 3072
        rows.append({'chip': chip, 'exact_MMIO_writes': len(writes), 'payload_pre_cost_bytes': wp})
print(json.dumps(rows, indent=2))
