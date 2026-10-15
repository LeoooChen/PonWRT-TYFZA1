#!/usr/bin/env python3
"""Execute the original GPON IRQ's grace and timeout link handling."""
from pathlib import Path
import hashlib, json, struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
records = []
for previous, state in ((4, 5), (5, 4), (5, 1)):
    calls = []

    def callback(name, args):
        if name not in ('ca_printf', 'ca_event_send', 'aal_gpon_datapath_reset'):
            raise RuntimeError((name, args))
        entry = dict(function=name, arguments=args)
        if name == 'ca_event_send':
            entry['payload'] = oracle.unpack(args[2], 'I' * (args[3] // 4))
        calls.append(entry)
        return 0

    oracle = ModuleOracle(module, ['__intr_handler'], callback,
                          external_symbols={'ca_rtk_debug': 0x5007000})
    for name, value in [('g_gpon_pdc_dbg_timer', 1), ('lastState', previous),
                        ('g_gpon_drain_out_required', 1), ('linkState.5', 1)]:
        oracle.uc.mem_write(oracle.symbols[name][0], struct.pack('<I', value))
    oracle.call('__intr_handler', 1 << 31, state, 17)
    link = oracle.unpack(oracle.symbols['linkState.5'][0], 'I')[0]
    drain = oracle.unpack(oracle.symbols['g_gpon_drain_out_required'][0], 'I')[0]
    resets = sum(call['function'] == 'aal_gpon_datapath_reset' for call in calls)
    assert oracle.unpack(oracle.symbols['lastState'][0], 'I')[0] == state
    if state in (4, 5):
        assert link == 1 and drain == 1 and not resets
        assert not any(call['function'] == 'ca_event_send' and call['arguments'][1] == 6 for call in calls)
    else:
        assert link == 0 and drain == 0 and resets
    records.append(dict(previous=previous, state=state, link=link, drain_required=drain,
                        datapath_reset_calls=resets, modeled_calls=calls))

result = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              actual_function='__intr_handler@0xfb990', records=records,
              scope='Actual GPON state handler with seeded link/timer state; event/log callbacks and timeout datapath reset body modeled. No optical timing or OLT execution.')
(root / 'logs/stock-gpon-o6.json').write_text(json.dumps(result, indent=2) + '\n')
print('Original GPON O5/O6 grace retains link and drain state; O6 timeout publishes loss and resets the datapath')
