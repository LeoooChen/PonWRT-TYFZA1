#!/usr/bin/env python3
"""Execute original GetAllSetting producer and pending reply persistence."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from trace_stock_fttr_sync import SyncOriginal


class SettingsOriginal(SyncOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols['get_settings'] = 0x411a10
        self.oracle.symbols['settings_rpc'] = 0x411e78
        self.oracle.symbols['json_ingress'] = 0x403e24
        self.files = {}
        self.opened = {}
        self.next_file = 1
        self.outputs = []
        self.oracle.hooks[0x405320] = 'capture_json_send'
        self.oracle.hooks[0x4127f4] = 'read_request_file'
        self.request_content = {'act': 'get'}
        self.flags_cleared = []
        self.oracle.hooks[0x40a158] = 'known_peer'
        self.oracle.hooks[0x409e7c] = 'receive_statistics'
        self.oracle.hooks[0x40745c] = 'queue_json'
        self.queued = []

    def handler(self, name, a):
        if name == 'known_peer':
            assert bytes(self.oracle.uc.mem_read(a[0], 6)) == self.peer
            return 1
        if name == 'receive_statistics':
            return 0
        if name == 'queue_json':
            self.queued.append(a[0])
            return 0
        if name == 'mib_get_s' and a[0] == 736:
            assert a[2] == 4
            self.oracle.uc.mem_write(a[1], struct.pack('<I', 2))
            return 1
        if name == 'read_request_file':
            path = self.string(a[0])
            if path == '/tmp/syncserv_bssc':
                return 0
            assert path == '/tmp/syncserv_bssc_' + self.peer.hex().upper()
            self.oracle.uc.mem_write(self.text + 0x6000, json.dumps(self.request_content).encode() + b'\0')
            return self.text + 0x6000
        if name == 'free':
            assert a[0] == self.text + 0x6000
            return 0
        if name == 'snprintf' and self.string(a[2]).count('%') == 6:
            from unicorn.arm64_const import UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_SP
            u = self.oracle.uc
            values = a[3:] + [u.reg_read(UC_ARM64_REG_X6), u.reg_read(UC_ARM64_REG_X7),
                              struct.unpack('<I', u.mem_read(u.reg_read(UC_ARM64_REG_SP), 4))[0]]
            text = (self.string(a[2]) % tuple(values)).encode()
            assert len(text) < a[1]
            u.mem_write(a[0], text + b'\0')
            return len(text)
        if name == 'strcmp':
            x, y = self.string(a[0]), self.string(a[1])
            return (x > y) - (x < y)
        if name == 'sprintf' and self.string(a[1]) in ('/var/sync_onuconfig_%s', 'Unknown method %s'):
            text = (self.string(a[1]) % self.string(a[2])).encode()
            self.oracle.uc.mem_write(a[0], text + b'\0')
            return len(text)
        if name == 'unlink':
            self.files.pop(self.string(a[0]), None)
            return 0
        if name == 'fopen64':
            assert self.string(a[1]) in ('w', 'w+')
            descriptor = self.next_file
            self.next_file += 1
            self.opened[descriptor] = self.string(a[0])
            self.files[self.opened[descriptor]] = ''
            return descriptor
        if name == 'fprintf':
            assert self.string(a[1]) == '%s\n'
            value = self.string(a[2]) + '\n'
            self.files[self.opened[a[0]]] += value
            return len(value)
        if name == 'fclose':
            del self.opened[a[0]]
            return 0
        if name == 'fputs':
            self.files[self.opened[a[1]]] += self.string(a[0])
            return 0
        if name == 'syncserv_clear_flag':
            self.flags_cleared.append(a[0])
            return 1
        if name == 'printf':
            return 0
        if name == 'capture_json_send':
            self.outputs.append({'slot': a[0], 'request': bool(a[2]), 'json': self.objects[a[1]]})
            return 0
        return super().handler(name, a)

    def query(self):
        pointer = self.object({})
        assert self.oracle.call('get_settings', pointer) == 1
        return self.objects[pointer]

    def wifi_query(self):
        u = self.oracle.uc
        u.mem_write(self.packet + 0x9000, self.peer + bytes([0, 5]) + bytes(504))
        pointer = self.object({})
        assert self.oracle.call('settings_rpc', self.packet + 0x9000, pointer) == 1
        return self.objects[pointer]

    def ingress_type(self, kind):
        u = self.oracle.uc
        frame = self.mac + self.peer + b'\x88\xaa' + bytes([kind, 0]) + b'\0\0\0\2{}'
        u.mem_write(self.buffer, frame)
        u.mem_write(self.packet, struct.pack('<QQQQ', self.buffer, self.buffer,
                                             self.buffer + len(frame), self.buffer + 2048))
        self.queued.clear()
        self.oracle.call('json_ingress', self.packet)
        return bool(self.queued)

    def reply(self, value, pending=10):
        u = self.oracle.uc
        text = json.dumps(value, separators=(',', ':')).encode()
        assert len(text) < 4096
        u.mem_write(self.text + 0x4000, len(text).to_bytes(4, 'big') + text + b'\0')
        u.mem_write(self.packet + 0x9000, bytes(512))
        u.mem_write(self.packet + 0x9000, self.peer + bytes([0, 5]))
        u.mem_write(self.packet + 0x9000 + 468, struct.pack('<III', 2 if pending else 0, 2, pending))
        u.mem_write(0x42ea84, struct.pack('<I', 1))
        u.mem_write(0x4425e0 + 5*4, struct.pack('<I', 2))
        self.files.clear()
        self.outputs.clear()
        self.oracle.call('reply', 5, self.text + 0x4000, len(text) + 4)
        state = struct.unpack('<III', u.mem_read(self.packet + 0x9000 + 468, 12))
        return state, dict(self.files)


def verify(binary):
    stock = SettingsOriginal(binary)
    query = stock.query()
    assert query == {'RPCMethod': 'GetAllSetting', 'MAC': stock.mac.hex().upper()}
    wifi = stock.wifi()
    reply = {key: value for key, value in wifi.items() if key != 'RPCMethod'}
    reply.update(Result=1, MAC=stock.peer.hex().upper())
    state, files = stock.reply(reply)
    assert state == (0, 2, 0)
    expected_path = '/var/sync_onuconfig_' + reply['MAC']
    assert set(files) == {expected_path} and json.loads(files[expected_path]) == reply
    # GetWifi consumes the same result/data object without saving its values.
    assert stock.reply(reply, pending=2) == ((0, 2, 0), {})
    assert stock.reply({'Result': 1}) == ((0, 2, 0), {})
    assert stock.reply(dict(reply, Result=0)) == ((0, 2, 0), {})
    # No RPCMethod is necessary; when supplied, the old pending dispatcher
    # does not match it before saving. Native must enforce that correlation.
    _, files = stock.reply(dict(reply, RPCMethod='GetTimeSetting'))
    assert files
    rpc = stock.wifi_query()
    assert rpc == {'RPCMethod': 'SetAllSetting', 'MAC': stock.mac.hex().upper(),
                   'DMAC': stock.peer.hex().upper(), 'Flag': 1, 'content': {'act': 'get'}}
    report = {'RPCMethod': 'SetAllSetting', 'Flag': 1, 'content': {'content': {'List': []}}, 'STATUS': 0}
    _, files = stock.reply(report, pending=11)
    assert files == {'/tmp/syncserv_bssg_' + stock.peer.hex().upper(): '{"content":{"List":[]}}'}
    assert stock.flags_cleared == [1]
    assert struct.unpack('<I', stock.oracle.uc.mem_read(0x4425e0+5*4,4))[0] == 0
    # ACK retirement is independent of completion; a later unsolicited-style
    # method report still reaches the content/STATUS branch while idle.
    assert stock.reply({'Result': 1}, pending=11) == ((0, 2, 0), {})
    _, later = stock.reply(report, pending=0)
    assert later == files and stock.flags_cleared == [1, 1]
    _, partial = stock.reply(dict(report, STATUS=1), pending=0)
    assert partial == files and stock.flags_cleared == [1, 1]
    assert stock.ingress_type(6) and stock.ingress_type(7) and not stock.ingress_type(8)
    return {'controller_sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
            'get_all_setting': query,
            'wifi_settings_query': rpc,
            'pending_method_ids': {'GetWifi': 2, 'GetAllSetting': 10},
            'checks': ['GetAllSetting producer emits method and host MAC only',
                       'positive pending GetAllSetting reply persists the entire JSON keyed by its MAC',
                       'same pending GetWifi reply discards data after Result handling',
                       'bare positive ACK retires request without saving settings; rejection does not save',
                       'original pending Result branch does not correlate an optional RPCMethod',
                       'modern Wi-Fi read is SetAllSetting Flag1 DMAC=target content={act:get}',
                       'SetAllSetting Flag1 report saves content by source peer MAC, STATUS0 clears flag',
                       'report dispatch works after ACK retirement; nonzero STATUS does not clear completion flag',
                       'original known-peer JSON ingress queues both frame types6 and7'],
            'scope': 'Actual original GetAllSetting and SetAllSetting Flag1 producer/reply/save/ingress instructions; synthetic MIB, request file and slave replies; JSON-C and filesystem modeled. No slave producer or real transport acceptance.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = verify(args.controller)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
