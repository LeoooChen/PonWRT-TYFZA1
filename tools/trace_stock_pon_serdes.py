#!/usr/bin/env python3
"""Trace the board's real PON/SDS mode conversion and EPON CSR handoff."""
from pathlib import Path
import hashlib, json, re, struct
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
names = ['__pon_mode_to_sds_mode.isra.0', '__psds_mode_init', '__psds_sync', 'aal_psds_out_of_reset']
calls, writes = [], []


def callback(name, args):
    if name not in ('ca_printf', 'ca_udelay', 'ca_mdelay', '__psds_prof_load_from_file', '__psds_burst_polarity_set'):
        raise RuntimeError((name, args))
    calls.append(dict(function=name, arguments=args))
    return 0


oracle = ModuleOracle(module, names, callback)
pon, global_ = 0x6000000, 0x6100000
for name, address in [('ca_venus_pon_reg_iobase', pon), ('ca_venus_pon_glb_reg_iobase', global_)]:
    oracle.uc.mem_map(address, 0x10000)
    oracle.word(oracle.symbols[name][0], address)
oracle.uc.hook_add(UC_HOOK_MEM_WRITE,
                  lambda uc, kind, address, size, value, data: writes.append([address - global_, value]),
                  begin=global_, end=global_ + 0xffff)
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-pon-mode.h'
modes = re.findall(r'\{"([^"]+)", "[^"]+", (0x[0-9a-f]+),', header.read_text())
records = []
for mode, (name, expected) in enumerate(modes):
    oracle.call(names[0], mode, oracle.scratch)
    sds = oracle.unpack(oracle.scratch, 'I')[0]
    for status in (0x9c00, 0x9c01, 0x8c00):
        oracle.uc.mem_write(pon, bytes(0x10000))
        oracle.uc.mem_write(global_, bytes(0x10000))
        oracle.uc.mem_write(pon + 0xa060, struct.pack('<I', status))
        oracle.uc.mem_write(global_ + 0x74, struct.pack('<I', 0xa5a50001))
        calls.clear(); writes.clear()
        result = oracle.call('aal_psds_out_of_reset', 0, sds, instruction_budget=200000)
        epon = oracle.unpack(global_ + 0x74, 'I')[0]
        mode_reg = oracle.unpack(pon + 0xa02c, 'I')[0]
        assert mode_reg == int(expected, 16)
        assert result == (0 if status == 0x9c00 else 5)
        assert epon == (0xa5a50000 if mode >= 3 and not result else 0xa5a50001)
        records.append(dict(mode=name, pon_mode=mode, sds_mode=sds, mode_reg=hex(mode_reg),
                            status=hex(status), result=result, epon_csr=hex(epon),
                            epon_csr_writes=[hex(value) for offset, value in writes if offset == 0x74]))

result = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              actual_functions=names, records=records,
              scope='Actual mode conversion, mode register programming, clock predicate and out-of-reset EPON CSR writes. Profile-file load returns0, burst-polarity body and logs/delays modeled; no optical or real clock timing proof.')
(root / 'logs/stock-pon-serdes-family.json').write_text(json.dumps(result, indent=2) + '\n')
print('Original six PON modes: converted SDS/register values match; all require9c00; only synchronized ITU paths hold EPON CSR in reset')
