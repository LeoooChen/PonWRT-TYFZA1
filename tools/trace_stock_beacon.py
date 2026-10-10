#!/usr/bin/env python3
"""Extract the original TYFZA1 beacon H2C for a supplied raw 802.11 template."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('frame', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--rate', type=int, default=4)
parser.add_argument('--antenna', type=int, default=0, choices=(0,1,2,3))
args = parser.parse_args()
frame = args.frame.read_bytes()
tim = 0
offset = 36
while offset + 2 <= len(frame):
    if frame[offset] == 5:
        tim = offset - 24
        break
    offset += 2 + frame[offset + 1]
module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
adapter, platform, info, packet, payload, template = [0x6000000 + i * 0x10000 for i in range(6)]
state = {'length': 0}


def callback(name, a):
    if name == 'h2cb_alloc':
        state['buffer_class'] = a[1]
        return packet
    if name == 'h2cb_put':
        address = payload + state['length']
        state['length'] += a[1]
        return address
    if name == 'memcpy':
        oracle.uc.mem_write(a[1], bytes(oracle.uc.mem_read(a[2], a[3])))
        return 0
    if name == 'h2c_pkt_set_hdr':
        extra = [oracle.uc.reg_read(r) for r in (UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7)]
        state.update(category=a[3], command_class=extra[0], function=extra[1], receive_ack=extra[2], done_ack=extra[3])
        return 0
    if name in ('h2c_pkt_build_txd', 'h2c_end_flow', 'platform_TX'):
        return 0
    raise RuntimeError((name, a))


oracle = ModuleOracle(module, ['mac_send_bcn_h2c'], callback)
for address in (adapter, platform, info, packet, payload, template):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(adapter + 24, platform)
oracle.word(adapter + 88, oracle.symbols['mac8852c_hw_info'][0])
oracle.word(platform + 88, oracle.callback('memcpy'))
oracle.word(platform + 48, oracle.callback('platform_TX'))
oracle.uc.mem_write(template, frame)
params = bytearray(40)
params[3] = (tim | 128) if tim else 0
params[5:7] = b'\x01\x01'  # original hardware sequence select/mode
struct.pack_into('<H', params, 8, args.rate)
if args.antenna:
    params[11] = 1
    params[12] = args.antenna
    params[13] = int(args.antenna == 2)
    params[14] = int(args.antenna == 3)
struct.pack_into('<QH', params, 24, template, len(frame))
oracle.uc.mem_write(info, bytes(params))
status = oracle.call('mac_send_bcn_h2c', adapter, info)
state.update(scope='Original ELF payload construction; no hardware TX emulated',
             original_status=status, original_host_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
             frame_sha256=hashlib.sha256(frame).hexdigest(), tim_offset_after_MAC_header=tim,
             payload=bytes(oracle.uc.mem_read(payload, state['length'])).hex())
args.output.write_text(json.dumps(state, indent=2) + '\n')
print(json.dumps({k: v for k, v in state.items() if k != 'payload'}))
