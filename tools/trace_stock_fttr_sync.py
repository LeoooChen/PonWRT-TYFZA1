#!/usr/bin/env python3
"""Execute the retained sync_controllerd Wi-Fi JSON producer and Ethernet encoder.

JSON-C allocation/serialization, source MIB storage and packet allocation are
modeled. The original ARM64 producer, pktbuf pointer operations and encoder run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_elf_oracle import ElfOracle
from unicorn.arm64_const import UC_ARM64_REG_X6, UC_ARM64_REG_X7


class SyncOriginal:
    def __init__(self, binary):
        assert hashlib.sha256(Path(binary).read_bytes()).hexdigest() == \
            'cfe3afada669992624f087aebef91e4000f361c83cdaeb15cfd6f3906f2d21e2', 'fixed-address oracle requires matching original controller'
        self.oracle = ElfOracle(binary, self.handler, ('msg',), copy_symbols={
            'SYNCSERV_USOCK_SERVER': bytes(24), 'wlan_idx': bytes(4),
            'optarg': bytes(8), 'stderr': bytes(8)})
        self.oracle.symbols.update(wifi=0x410cc4, get_wifi=0x410c60,
                                   encode=0x405320, ethernet=0x407be8, reply=0x40d198)
        self.oracle.hooks[0x403efc] = 'peer_send'
        self.oracle.hooks[0x40928c] = 'peer_lookup'
        self.objects = {}
        self.next_object = self.oracle.scratch + 0x20000
        self.packet = self.oracle.scratch + 0x40000
        self.buffer = self.packet + 0x100
        self.text = self.oracle.scratch + 0x50000
        self.mac = bytes.fromhex('020304050607')
        self.peer = bytes.fromhex('0a0b0c0d0e0f')
        self.entries = [self.entry(4), self.entry(20)]
        self.disabled = [0, 1]
        u = self.oracle.uc
        self.default_ethertype = struct.unpack('<I', u.mem_read(0x42b9f0, 4))[0]
        u.mem_write(0x42e9e0, struct.pack('<Q', self.packet + 0x8000))
        u.mem_write(self.packet + 0x8040, self.mac)
        u.mem_write(0x42e9c8, struct.pack('<Q', self.packet + 0x8100))

    @staticmethod
    def entry(security, name=b'Synthetic FTTR sync', key=b'synthetic-passphrase-1234567890'):
        row = bytearray(848)
        row[1] = security
        row[6:6+len(key)] = key
        row[219:219+len(name)] = name
        row[255] = row[256] = 2  # WPA/WPA2 AES, independently selected.
        return bytes(row)

    def string(self, address):
        value = bytearray()
        while True:
            byte = self.oracle.uc.mem_read(address + len(value), 1)[0]
            if not byte:
                return bytes(value).decode('utf-8')
            value.append(byte)
            assert len(value) < 8192

    def object(self, value):
        pointer = self.next_object
        self.next_object += 16
        self.objects[pointer] = value
        return pointer

    def handler(self, name, a):
        u = self.oracle.uc
        if name == 'json_object_new_object': return self.object({})
        if name == 'json_object_new_array': return self.object([])
        if name == 'json_object_new_string': return self.object(self.string(a[0]))
        if name == 'json_object_new_int': return self.object(a[0] & 0xffffffff)
        if name == 'json_object_object_add':
            self.objects[a[0]][self.string(a[1])] = self.objects[a[2]]
            return 0
        if name == 'json_object_array_add':
            self.objects[a[0]].append(self.objects[a[1]])
            return 0
        if name == 'json_object_to_json_string':
            text = json.dumps(self.objects[a[0]], separators=(',', ':'), ensure_ascii=False).encode()
            u.mem_write(self.text, text + b'\0')
            return self.text
        if name == 'json_tokener_parse': return self.object(json.loads(self.string(a[0])))
        if name == 'json_object_object_get':
            value = self.objects[a[0]].get(self.string(a[1]))
            return self.object(value) if value is not None else 0
        if name == 'json_object_get_int': return int(self.objects[a[0]])
        if name == 'json_object_get_string':
            if not a[0]: return 0
            u.mem_write(self.text + 0x2000, str(self.objects[a[0]]).encode() + b'\0')
            return self.text + 0x2000
        if name == 'peer_lookup': return self.packet + 0x9000
        if name == 'mib_get_s':
            if a[0] == 2002:
                assert a[2] == 6
                u.mem_write(a[1], self.mac)
            else:
                assert a[0] == 1469 and a[2] == 1
                band = struct.unpack('<I', u.mem_read(self.oracle.symbols['wlan_idx'], 4))[0]
                u.mem_write(a[1], bytes([self.disabled[band]]))
            return 1
        if name == 'wlan_getEntry':
            assert a[1] == 0
            band = struct.unpack('<I', u.mem_read(self.oracle.symbols['wlan_idx'], 4))[0]
            u.mem_write(a[0], self.entries[band])
            return 1
        if name == 'sprintf':
            fmt = self.string(a[1])
            values = a[2:] + [u.reg_read(UC_ARM64_REG_X6), u.reg_read(UC_ARM64_REG_X7)]
            assert fmt.count('%') == 6
            text = (fmt % tuple(values)).encode()
            u.mem_write(a[0], text + b'\0')
            return len(text)
        if name == 'snprintf':
            assert self.string(a[2]) == '%s'
            text = self.string(a[3]).encode()
            u.mem_write(a[0], text[:a[1]-1] + b'\0')
            return len(text)
        if name == 'strlen': return len(self.string(a[0]).encode())
        if name in ('htonl', 'ntohl'): return int.from_bytes((a[0]&0xffffffff).to_bytes(4,'little'),'big')
        if name == 'htons': return int.from_bytes((a[0]&0xffff).to_bytes(2,'little'),'big')
        if name == 'memcpy':
            u.mem_write(a[0], bytes(u.mem_read(a[1], a[2])))
            return a[0]
        if name == 'pktbuf_get':
            u.mem_write(self.buffer, bytes(8192))
            u.mem_write(self.packet, struct.pack('<QQQQ', self.buffer, self.buffer,
                                                self.buffer, self.buffer + 8192))
            return self.packet
        if name == 'peer_send':
            self.sent_slot = a[0]
            # Model registered slot -> explicit peer MAC; retain original L2 encoder.
            return 0
        if name == 'send_pkt2':
            self.frame = bytes(u.mem_read(a[1], a[2]))
            return a[2]
        if name in ('msg', 'pktbuf_free', 'json_object_put'): return 0
        raise AssertionError(('unmodeled original call', name, a))

    def wifi(self, get=False):
        pointer = self.object({})
        assert self.oracle.call('get_wifi' if get else 'wifi', pointer, 0) == 1
        return self.objects[pointer]

    def encode(self, value, slot=5, request=True, ethertype=None):
        u = self.oracle.uc
        u.mem_write(0x42b9f0, struct.pack('<I', ethertype or self.default_ethertype))
        pointer = self.object(value)
        self.oracle.call('encode', slot, pointer, int(request))
        assert self.sent_slot == slot & 15
        u.mem_write(self.text + 0x3000, self.peer)
        self.oracle.call('ethernet', self.text + 0x3000, self.packet, 0)
        return self.frame

    def reply_result(self, value):
        u = self.oracle.uc
        text = json.dumps(value, separators=(',', ':')).encode()
        u.mem_write(self.text + 0x4000, len(text).to_bytes(4, 'big') + text + b'\0')
        u.mem_write(self.packet + 0x9000, bytes(512))
        u.mem_write(self.packet + 0x9000 + 468, struct.pack('<III', 2, 2, 2))
        self.oracle.call('reply', 5, self.text + 0x4000, len(text) + 4)
        return struct.unpack('<III', u.mem_read(self.packet + 0x9000 + 468, 12))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    stock = SyncOriginal(args.controller)
    get = stock.wifi(get=True)
    ack_results = {str(result): stock.reply_result({'Result': result, 'FailReason': 'synthetic'})
                   for result in (0, 1)}
    assert all(state == (0, 2, 0) for state in ack_results.values())
    cases = []
    modes = {0:'None', 2:'WPA', 4:'WPA2', 6:'WPA/WPA2', 16:'WPA3', 20:'WPA2/WPA3'}
    for code in (0, 2, 4, 6, 16, 20):
        stock.entries = [stock.entry(code), stock.entry(code)]
        value = stock.wifi()
        assert value['RPCMethod'] == 'SetWifi' and value['2.4GEnable'] == 1 and value['5GEnable'] == 0
        assert [row['Band'] for row in value['SSID']] == ['2.4G', '5G']
        assert all(row['Index'] == 1 and row['BeaconType'] == modes[code] for row in value['SSID'])
        frame = stock.encode(value)
        assert frame[:14] == stock.peer + stock.mac + b'\x88\xaa'
        assert frame[14:16] == b'\x56\0'
        assert int.from_bytes(frame[16:20], 'big') == len(frame) - 20
        assert json.loads(frame[20:]) == value
        cases.append({'security_mib': code, 'json': value, 'frame': frame.hex()})
    stock.entries = [stock.entry(4, b'S'*32, b'K'*63)] * 2
    full = stock.wifi()
    assert all(len(row['Name']) == 32 and len(row['WPAKey']) == 63 for row in full['SSID'])
    stock.entries = [stock.entry(255)] * 2
    invalid = stock.wifi()
    assert all(row['BeaconType'] == 'None' and 'WPAKey' in row for row in invalid['SSID'])
    for cipher, expected in ((1, 'TKIP'), (2, 'AES'), (3, 'TKIP+AES')):
        row = bytearray(stock.entry(4));row[255] = row[256] = cipher
        stock.entries = [bytes(row)] * 2
        assert all(s['WPAEncMode'] == s['WPA2EncMode'] == expected for s in stock.wifi()['SSID'])
    report = {'controller_sha256': hashlib.sha256(args.controller.read_bytes()).hexdigest(),
              'default_ethertype': stock.default_ethertype, 'get_wifi': get, 'cases': cases,
              'result_only_ack_states': ack_results,
              'full_width_fields': {'ssid_bytes': 32, 'shared_key_bytes': 63},
              'invalid_security_255_fallback': 'None with WPAKey still emitted; do not use as native default',
              'scope': 'Original Wi-Fi producer, frame encoder and result-only reply dispatch; synthetic MIB, modeled JSON-C allocation/serialization and slot lookup. No live transport or subordinate execution.'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'controller_sha256': report['controller_sha256'], 'default_ethertype': hex(stock.default_ethertype),
                      'get_wifi': get, 'set_fields': list(cases[0]['json']), 'cases': len(cases)}, indent=2))


if __name__ == '__main__': main()
