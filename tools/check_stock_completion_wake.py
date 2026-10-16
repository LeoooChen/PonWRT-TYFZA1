#!/usr/bin/env python3
"""Execute original per-packet net-queue wake and packet-free ordering."""
from pathlib import Path
import hashlib
import json
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
adapter, skb, netdev, queues = 0x6000000, 0x6010000, 0x6020000, 0x6030000
calls = []


def callback(name, args):
    if name == 'netif_tx_wake_queue':
        assert args[0] == queues + 2 * 0x140
        calls.append('wake_packet_net_queue')
        return 0
    if name == 'dbg_rtw_skb_free':
        assert args[0] == skb
        calls.append('free_completed_packet')
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['rtw_os_pkt_complete'], callback)
oracle.uc.mem_map(adapter, 0x40000)
oracle.word(adapter + 0xb4a8, netdev)
oracle.word(netdev + 0x380, queues)
oracle.uc.mem_write(skb + 0x7c, struct.pack('<H', 2))
oracle.call('rtw_os_pkt_complete', adapter, skb)
assert calls == ['wake_packet_net_queue', 'free_completed_packet']
print(json.dumps(dict(factory_module_sha256=digest, actual_function='rtw_os_pkt_complete@0x16980',
                     calls=calls, scope='Actual original packet completion branch with uncongested-local-queue input. Net-queue wake and skb free are callbacks; no global WP-zero premise is supplied, and no full scheduler or IRQ timing is executed.'), indent=2))
