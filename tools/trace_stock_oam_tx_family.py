#!/usr/bin/env python3
"""Execute original OAM transport to establish its unchanged NI wire contract."""
from pathlib import Path
import hashlib, json, struct
from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-rtk.ko'
records = []
for mode in range(6):
    submitted = []

    def callback(name, args):
        if name == '__netdev_alloc_skb':
            skb, data = oracle.scratch + 0x1000, oracle.scratch + 0x2000
            oracle.uc.mem_write(skb, bytes(256))
            oracle.word(skb + 200, data)
            return skb
        if name == 'aal_mpcp_reg_sw_index_offset_get':
            oracle.uc.mem_write(args[1], b'\0')
            return 0
        if name == 'memcpy':
            oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
            return args[0]
        if name == '__rtk_ni_start_xmit':
            skb = args[0]
            length = oracle.unpack(skb + 112, 'I')[0]
            data = oracle.unpack(skb + 200, 'Q')[0]
            submitted.append(dict(header=hex(oracle.unpack(args[1], 'Q')[0]), length=length,
                                  data=bytes(oracle.uc.mem_read(data, length)).hex()))
            return 0
        if name == 'ca_printf':
            return 0
        raise RuntimeError((name, args))

    oracle = ModuleOracle(module, ['dal_rt_rtl8277c_epon_oam_tx'], callback)
    oracle.uc.mem_write(oracle.symbols['CA_PON_MODE'][0], bytes([mode]))
    payload = bytearray(60)
    payload[12:15] = b'\x88\x09\x03'
    oracle.uc.mem_write(oracle.scratch, bytes(payload))
    ret = oracle.call('dal_rt_rtl8277c_epon_oam_tx', 60, oracle.scratch, 7)
    assert ret == 0 and len(submitted) == 1
    item = submitted[0]
    header = int(item['header'], 16)
    assert header & 0xffffffff == 1 << 13
    assert header >> 32 == (76 << 15) | 0x2000227f
    assert item['length'] == 76 and bytes.fromhex(item['data'])[16:] == payload
    records.append(dict(mode=mode, result=ret, submitted=submitted))

result = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              actual_function='dal_rt_rtl8277c_epon_oam_tx@0x22230', records=records,
              scope='Actual original framing/Header A and unconditional TX entry under six CA_PON_MODE values; allocator, memcpy, MPCP offset and NI submission modeled. This helper itself does not enforce family or carrier eligibility; no optical traffic.')
(root / 'logs/stock-oam-tx-family.json').write_text(json.dumps(result, indent=2) + '\n')
print('Original OAM transport: identical control Header A under6 modes; no per-call family/carrier eligibility check in this helper')
