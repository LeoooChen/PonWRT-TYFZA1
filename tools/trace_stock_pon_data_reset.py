#!/usr/bin/env python3
"""Execute original family deinitializers through the actual PON reset writes."""
from pathlib import Path
import hashlib, json, struct
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
names = ['aal_pon_deinit', 'aal_epon_deinit', 'aal_gpon_deinit', 'aal_xgpon_deinit', 'aal_psds_deinit']
records = []
for mode in range(6):
    calls, writes = [], []

    def callback(name, args):
        if name not in ('ca_printf', 'ca_mdelay', 'ca_timer_del', 'ca_event_deregister',
                        'aal_epon_crypto_deinit', 'aal_mpcp_deinit', 'aal_gpon_intr_deinit',
                        'aal_xgpon_intr_deinit', 'aal_xgpn_activation_fsm_deinit', '__local_bh_enable_ip'):
            raise RuntimeError((name, args))
        calls.append(dict(function=name, arguments=args))
        return 0

    oracle = ModuleOracle(module, names, callback)
    base = 0x6000000
    oracle.uc.mem_map(base, 0x10000)
    oracle.word(oracle.symbols['ca_venus_pon_glb_reg_iobase'][0], base)
    initial = {0x94: 0xa5a5030f if mode < 3 else 0xa5a5030e,
               0x74: 0x123400ef, 0x7c: 0x307, 0x22c: 0x35}
    for address, value in initial.items():
        oracle.uc.mem_write(base + address, struct.pack('<I', value))
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE,
                      lambda uc, kind, address, size, value, data: writes.append([address - base, value]),
                      begin=base, end=base + 0xffff)
    result = oracle.call('aal_pon_deinit', 0, mode)
    assert result == 0 and oracle.unpack(base + 0x94, 'I')[0] == 0
    family_reg = 0x74 if mode < 3 else 0x7c
    assert oracle.unpack(base + family_reg, 'I')[0] == 0
    assert oracle.unpack(base + (0x7c if mode < 3 else 0x74), 'I')[0] == initial[0x7c if mode < 3 else 0x74]
    assert any(call['function'] == 'ca_mdelay' and call['arguments'][0] == 100 for call in calls)
    records.append(dict(mode=mode, result=result, actual_global_writes=writes, modeled_calls=calls))

record = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              actual_functions=names, records=records,
              scope='Actual six-mode dispatch, family deinitializers, PSDS shutdown and PON/selected-MAC reset writes. IRQ/FSM/MPCP/crypto deinit bodies, delays, events and OS calls modeled. No NI DMA/FIFO completion proof.')
(root / 'logs/stock-pon-data-reset.json').write_text(json.dumps(record, indent=2) + '\n')
print('Original6 families: PON control including PUC/PDC reset to0, selected MAC reset, inactive MAC retained;100ms deinit delay')
