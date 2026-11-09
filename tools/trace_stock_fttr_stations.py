#!/usr/bin/env python3
"""Execute target wireless station query, record consumer and Ethernet encoder."""
import argparse
import json
from pathlib import Path
import struct

from trace_stock_fttr_radio import RadioOriginal
from trace_stock_fttr_sync import SyncOriginal


class StationsOriginal(RadioOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols['station_query'] = 0x4120b8
        self.oracle.hooks[0x40cacc] = 'save_station_table'
        # The complete station consumer is gated by the FTTR+ mode word.
        self.oracle.uc.mem_write(0x42ea80, struct.pack('<I', 1))

    def handler(self, name, a):
        if name == 'save_station_table':
            return 0
        if name == 'json_object_get_array':
            self.oracle.uc.mem_write(a[0]+8, struct.pack('<Q', len(self.objects[a[0]])))
            return a[0]
        if name == 'array_list_length':
            return len(self.objects[a[0]])
        if name == 'atoi':
            return int(self.string(a[0])) & 0xffffffff
        if name == 'changeStringToMac1':
            self.oracle.uc.mem_write(a[0], bytes.fromhex(self.string(a[1]).replace(':', '')))
            return 0
        return super().handler(name, a)

    def query(self):
        p = self.object({})
        assert self.oracle.call('station_query', p) == 1
        return self.objects[p]



def verify(binary):
    stock = StationsOriginal(binary)
    wire = SyncOriginal(binary)
    request = stock.query()
    assert request == {'RPCMethod': 'GetWifiStaInfo'}
    stock.reply(dict(RPCMethod='GetWifiStaInfo', StaInfo=[]), pending=0)
    assert stock.outputs == [dict(slot=5, request=False, json={})]
    rows = []
    for i in range(64):
        rows.append(dict(OnuMAC=stock.peer.hex().upper(), OnuIntf='wlan0' if i & 1 else 'wlan1',
                         BandType='2.4G' if i & 1 else '5G', StaMAC=f'02000000{i:04X}',
                         StaRssi=str(-30-i), StaNegoUpRate=str(100+i), StaNegoDownRate=str(200+i)))
    cases = []
    for count in (0, 1, 2, 63, 64, 65):
        sample = rows[:count] if count <= 64 else rows + [rows[0]]
        if count>2:sample=[{'StaMAC':item['StaMAC']} for item in sample]
        # SettingsOriginal's transport fixture is intentionally bounded4KiB.
        # Call the actual reply routine with larger storage for full reports.
        raw = json.dumps(dict(RPCMethod='GetWifiStaInfo', StaInfo=sample), separators=(',', ':')).encode()
        u = stock.oracle.uc
        ptr = stock.oracle.scratch + 0xe0000
        u.mem_write(ptr, len(raw).to_bytes(4, 'big') + raw + b'\0')
        u.mem_write(stock.packet + 0x9000, stock.peer + bytes([0, 5]) + bytes(504))
        stock.oracle.call('reply', 5, ptr, len(raw)+4, instruction_budget=500000)
        data = bytes(u.mem_read(0x441c78, 2275))
        for i, item in enumerate(sample[:64]):
            row = data[i*35:(i+1)*35]
            assert row[0] == 1 and row[1:7] == bytes.fromhex(item['StaMAC'])
            if count<=2:
                assert row[7] == (1 if item['BandType'] == '2.4G' else 2)
                assert row[8:24].split(b'\0', 1)[0] == json.dumps(item['OnuIntf']).encode()
                assert row[24] == int(item['StaRssi']) & 255
                assert row[25:31] == stock.peer
                assert struct.unpack('<HH', row[31:35]) == (100+i, 200+i)
        assert data[min(count,64)*35:64*35] == bytes((64-min(count,64))*35)
        cases.append(dict(input_rows=count, consumed_rows=min(count,64)))
    return dict(request=request, frame=wire.encode(request, ethertype=0x88aa).hex(),
                acknowledgement=wire.encode({}, request=False, ethertype=0x88aa).hex(),
                sample=rows[:2], cases=cases,
                scope='Actual target query, full station consumer,64-row bound and Ethernet encoder; JSON/MIB mode and remote records modeled. Raw reported RSSI/rate values are retained by native code without asserting RF accuracy or units.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.write_text(json.dumps(verify(a.controller), indent=2) + '\n')
    print('Original station query: envelope and0/1/2/63/64/65-record consumer PASS')
