#!/usr/bin/env python3
"""Execute original GPON coalesced assignment/state handling."""
from pathlib import Path
import hashlib, json, struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
records = []
for previous, flags, onu_id in ((4, (1 << 30) | (1 << 31), 18),
                                (4, (1 << 30) | (1 << 31), 17),
                                (5, 1 << 31, 17), (4, 1 << 30, 17)):
    calls = []

    def callback(name, args):
        if name not in ('ca_printf', 'ca_event_send', 'aal_gpon_datapath_reset', 'aal_gpon_omcc_tcont_enable'):
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
    oracle.call('__intr_handler', flags, 4, onu_id)
    reset = sum(call['function'] == 'aal_gpon_datapath_reset' for call in calls)
    link = oracle.unpack(oracle.symbols['linkState.5'][0], 'I')[0]
    coalesced = previous == 4 and bool(flags & (1 << 31))
    assert bool(reset) == coalesced and link == int(not coalesced)
    if flags & (1 << 30):
        assert any(call['function'] == 'aal_gpon_omcc_tcont_enable' and call['arguments'][1] == onu_id for call in calls)
    records.append(dict(previous=previous, sampled_state=4, flags=hex(flags), onu_id=onu_id,
                        reset_calls=reset, link=link, modeled_calls=calls))

result = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              actual_function='__intr_handler@0xfb990', records=records,
              scope='Actual GPON IRQ control flow. Cached state supplied; OMCC T-CONT enable, datapath reset, events and logging bodies modeled. No optical or FIFO proof.')
(root / 'logs/stock-gpon-coalesced-identity.json').write_text(json.dumps(result, indent=2) + '\n')
print('Original GPON: coalesced assignment/state event sampled in O5 resets the old link; O6 return and assignment repeat retain it')
