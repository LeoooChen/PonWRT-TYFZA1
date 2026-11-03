#!/usr/bin/env python3
"""Execute sync_controllerd heartbeat, peer allocation and ageing instructions."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from trace_stock_fttr_sync import SyncOriginal


class DiscoveryOriginal(SyncOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        del self.oracle.hooks[0x40928c]  # Use the real 16-slot lookup.
        self.oracle.symbols.update(heartbeat=0x4038d0, receive_heartbeat=0x403868,
                                   age=0x40a068, init_peers=0x413f80, ingress_slot=0x403f80)
        self.table = 0x42ea88
        self.stride = 0x131f
        self.timer_calls = []
        self.oracle.call('init_peers')

    def handler(self, name, a):
        u = self.oracle.uc
        if name == 'memset':
            u.mem_write(a[0], bytes([a[1] & 255])*a[2])
            return a[0]
        if name == 'mib_get_s' and a[0] == 660:
            assert a[2] == 4
            u.mem_write(a[1], bytes(4))
            return 1
        if name == 'clock_gettime':
            assert a[0] == 1
            u.mem_write(a[1], struct.pack('<qq', 100, 0))
            return 0
        if name == 'timerfd_settime':
            self.timer_calls.append(struct.unpack('<qqqq', u.mem_read(a[2], 32)))
            return 0
        if name == 'read':
            assert a[2] == 8
            u.mem_write(a[1], struct.pack('<Q', 1))
            return 8
        if name == 'ether_ntoa':
            u.mem_write(self.text+0x1000, bytes(u.mem_read(a[0],6)).hex(':').encode()+b'\0')
            return self.text+0x1000
        if name in ('printf', 'update_monitor_list_file', 'mib_chain_total'):
            # No saved slave rows; their user configuration is outside discovery.
            return 0
        if name == 'get_slave_upgrade_status':
            return 1  # No active upgrade bookkeeping.
        return super().handler(name, a)

    def heartbeat(self):
        self.oracle.call('heartbeat')
        return self.frame

    def receive(self, mac, flags=0, first=1):
        u = self.oracle.uc
        data = self.mac + mac + self.default_ethertype.to_bytes(2,'big') + bytes([first,flags])
        u.mem_write(self.buffer, data)
        u.mem_write(self.packet, struct.pack('<QQQQ',self.buffer,self.buffer,
                                           self.buffer+len(data),self.buffer+8192))
        self.oracle.call('receive_heartbeat', self.packet)

    def rows(self):
        result = []
        for slot in range(16):
            row = bytes(self.oracle.uc.mem_read(self.table + slot*self.stride,480))
            result.append({'mac':row[:6].hex(':'),'slot':row[7],
                           'missed':int.from_bytes(row[8:12],'little'),
                           'online':bool(int.from_bytes(row[464:468],'little'))})
        return result

    def encode_bound(self, value, slot):
        hook = self.oracle.hooks.pop(0x403efc)
        try:
            self.oracle.call('encode', slot, self.object(value), 1)
            return self.frame
        finally:
            self.oracle.hooks[0x403efc] = hook

    def ingress_slot(self, mac, header_slot):
        u = self.oracle.uc
        data = self.mac + mac + b'\x88\xaa' + bytes([header_slot << 4 | 7, 0])
        u.mem_write(self.buffer, data)
        u.mem_write(self.packet, struct.pack('<QQQQI', self.buffer, self.buffer,
                                           self.buffer+len(data),self.buffer+8192,0xffffffff))
        assert self.oracle.call('ingress_slot',self.packet)==1
        return struct.unpack('<I',u.mem_read(self.packet+32,4))[0]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    stock=DiscoveryOriginal(args.controller)
    frames=[stock.heartbeat() for _ in range(10)]
    assert [f[14:] for f in frames]==[bytes([1,0x80|(i&7)]) for i in range(10)]
    assert all(f[:6]==bytes.fromhex('0110ec000002') for f in frames)
    assert all(f[6:14]==stock.mac+b'\x88\xaa' for f in frames)
    assert all(t==(0,0,102,0) for t in stock.timer_calls)
    macs=[bytes([2,0,0,0,0,i+1]) for i in range(17)]
    stock.receive(macs[0],0x80)
    assert not any(r['online'] for r in stock.rows())
    for mac in macs[:16]:stock.receive(mac)
    assert all(r['online'] and r['mac']==mac.hex(':') and r['slot']==i
               for i,(r,mac) in enumerate(zip(stock.rows(),macs)))
    stock.receive(macs[16])
    assert all(r['mac']!=macs[16].hex(':') for r in stock.rows())
    stock.heartbeat();stock.heartbeat()
    assert all(r['online'] and r['missed']==2 for r in stock.rows())
    stock.receive(macs[5])
    stock.heartbeat()
    assert [r['slot'] for r in stock.rows() if r['online']]==[5]
    stock.receive(macs[16])
    assert stock.rows()[0]['mac']==macs[16].hex(':')
    stock.receive(macs[12])
    assert stock.rows()[12]['online']  # Exact historical MAC wins over first free.
    query = stock.encode_bound(stock.wifi(get=True), 12)
    assert query[:6] == bytes.fromhex('0110ec000002') and query[14] == 0xc6
    assert all(stock.ingress_slot(macs[12], header)==12 for header in (0,5,15))
    result={'controller_sha256':hashlib.sha256(args.controller.read_bytes()).hexdigest(),
            'heartbeat_frames':[f.hex() for f in frames], 'period_ms':2000,'age_threshold':2,
            'routed_query': query.hex(),
            'checks':['three-bit heartbeat sequence wraps; multicast 01:10:ec:00:00:02',
                      'request flag is ignored by receive; source MAC selects local 16-slot table',
                      'full table rejects new peer; third missed tick expires an online slot',
                      'refresh resets misses; first free slot reused; retained matching MAC preferred'],
            'ingress_slot': 'local source-MAC lookup overrides the received header slot',
            'scope':'Actual original heartbeat/receive/slot/age instructions; timer, packet allocation, output and empty saved-MIB side effects modeled. No live peer or authentication.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
