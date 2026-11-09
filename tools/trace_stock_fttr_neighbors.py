#!/usr/bin/env python3
"""Execute target neighbor query and NEW/REMAINING report storage/response."""
import argparse
import json
from pathlib import Path
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal


class NeighborsOriginal(SettingsOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols['neighbors'] = 0x412198
        self.saved = ''
        self.modes = []
        self.text_next = self.oracle.scratch + 0xb0000

    def handler(self, name, a):
        if name == 'json_object_get_string':
            if not a[0]: return 0
            text = str(self.objects[a[0]]).encode()
            p = self.text_next; self.text_next += len(text) + 1
            self.oracle.uc.mem_write(p, text + b'\0'); return p
        if name == 'system':
            assert self.string(a[0]) == 'mkdir -p /tmp/sfu_neighbor/'
            return 0
        if name == 'sprintf' and self.string(a[1]) == '/tmp/sfu_neighbor/%s':
            self.oracle.uc.mem_write(a[0], ('/tmp/sfu_neighbor/' + self.string(a[2])).encode() + b'\0')
            return 0
        if name == 'fopen64':
            assert self.string(a[0]) == '/tmp/sfu_neighbor/' + self.peer.hex().upper()
            mode = self.string(a[1]); assert mode in ('w', 'a')
            if mode == 'w': self.saved = ''
            self.modes.append(mode)
            return 1
        if name == 'fwrite':
            assert a[2:4] == [1, 1]
            data = bytes(self.oracle.uc.mem_read(a[0], a[1]))
            self.saved += data.decode()
            return 1
        if name == 'fclose':
            assert a[0] == 1
            return 0
        return super().handler(name, a)

    def query(self):
        p = self.object({}); assert self.oracle.call('neighbors', p) == 1
        return self.objects[p]


def verify(binary):
    stock = NeighborsOriginal(binary); wire = SyncOriginal(binary)
    query = stock.query(); assert query == {'RPCMethod': 'GetWifiNeighbor'}
    rows = []
    for flag, data, expected in [('NEW','first\n','first\n'), ('REMAINING','second\n','first\nsecond\n'),
                                 ('REMAINING','second\n','first\nsecond\nsecond\n'),
                                 ('NEW','replace\n','replace\n'), ('NEW','',''),
                                 ('UNKNOWN','ignored','')]:
        report = dict(RPCMethod='GetWifiNeighbor', MAC=stock.peer.hex().upper(), RECVFLAG=flag, scanResult=data)
        stock.reply(report, pending=0)
        assert stock.saved == expected
        assert stock.outputs == [dict(slot=5,request=False,json={'Result':0})]
        rows.append(dict(report=report,stored=expected))
    return dict(query=query,frame=wire.encode(query,ethertype=0x88aa).hex(),reports=rows,
                acknowledgement=wire.encode({'Result':0},request=False,ethertype=0x88aa).hex(),
                scope='Actual target request, flag decisions, append/replace, per-report response and wire encoding. Filesystem and remote report text modeled. No final-fragment/total/scan generation in the executed receiver; repeated continuation is appended twice.')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(verify(a.controller),indent=2)+'\n')
    print('Original neighbor query: six replace/append/duplicate/empty/unknown reports and Result0 responses PASS')
