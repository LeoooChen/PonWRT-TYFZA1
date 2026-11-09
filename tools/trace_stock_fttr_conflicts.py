#!/usr/bin/env python3
"""Execute original AP conflict query builders and complete report consumers."""
import argparse
import json
import struct
from pathlib import Path
from trace_stock_fttr_radio import RadioOriginal
from trace_stock_fttr_sync import SyncOriginal


class ConflictsOriginal(RadioOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols.update(bssid_conflicts=0x4120f0, ssid_conflicts=0x412128)
        # Original receiver gates these diagnostic methods on the manager flag.
        self.oracle.uc.mem_write(0x42ea80, struct.pack('<I', 1))

    def handler(self, name, a):
        if name == 'json_object_get_string':
            if not a[0]: return 0
            value = self.objects[a[0]]
            return self.textptr(value if isinstance(value, str) else json.dumps(value, separators=(',', ':')))
        if name == 'system':
            assert self.string(a[0]) in ('mkdir -p /tmp/sfu_bssid_conflict/', 'mkdir -p /tmp/sfu_ssid_name_conflict/')
            return 0
        if name == 'sprintf' and self.string(a[1]).startswith('/tmp/sfu_'):
            text = (self.string(a[1]) % self.string(a[2])).encode()
            self.oracle.uc.mem_write(a[0], text + b'\0'); return len(text)
        if name == 'json_object_get_array':
            p = self.temp; self.temp += 16
            self.objects[p] = self.objects[a[0]]
            self.oracle.uc.mem_write(p, struct.pack('<QQ', 0, len(self.objects[p])))
            return p
        if name == 'fprintf' and self.string(a[1]) in ('%s,', '\n'):
            text = self.string(a[2])+',' if self.string(a[1]) == '%s,' else '\n'
            self.files[self.opened[a[0]]] += text; return len(text)
        return super().handler(name, a)

    def query(self, ssid=False):
        p = self.object({})
        assert self.oracle.call('ssid_conflicts' if ssid else 'bssid_conflicts', p) == 1
        return self.objects[p]


def verify(binary):
    stock = ConflictsOriginal(binary); wire = SyncOriginal(binary); cases = []
    row = dict(onuMac=stock.peer.hex().upper(), onuModel='synthetic-ONU', rfband='5G',
               wlanMac='020000000001', ssidIndex='1', ssid='synthetic SSID')
    for ssid in (False, True):
        method = 'CheckSSIDNameConflict' if ssid else 'CheckBSSIDConflict'
        path = ('/tmp/sfu_ssid_name_conflict/' if ssid else '/tmp/sfu_bssid_conflict/') + stock.peer.hex().upper()
        query = stock.query(ssid); assert query == {'RPCMethod': method}
        for rows in ([], [row], [row, dict(row, wlanMac='020000000002', ssidIndex='2')]):
            report = dict(RPCMethod=method, mac=stock.peer.hex().upper(), ApInfo=rows)
            _, files = stock.reply(report, pending=0)
            expected = ''.join(''.join(str(v)+',' for v in item.values())+'\n' for item in rows)
            assert files == {path: expected}, files
            assert stock.outputs == [dict(slot=5, request=False, json={})]
            cases.append(dict(query=query, report=report, stored=list(files.values()), responses=stock.outputs,
                              frame=wire.encode(query,ethertype=0x88aa).hex()))
        numeric = dict(row, rfband=2, ssidIndex=1)
        _, files = stock.reply(dict(RPCMethod=method, mac=stock.peer.hex().upper(), ApInfo=[numeric]), pending=0)
        assert files == {path: ''.join(str(v)+',' for v in numeric.values())+'\n'}
        # The original top-level loop consumes only its last visited key.
        # Native lookup by name must not depend on JSON object ordering.
        _, files = stock.reply(dict(RPCMethod=method, mac=stock.peer.hex().upper(), ApInfo=[row], IgnoredVendorField={}), pending=0)
        assert files == {path: ''}
    return dict(cases=cases,original_trailing_field_discards_ap_rows=True,
                scope='Actual query builders and report receiver/file writes; JSON, peer and filesystem callbacks modeled. No subordinate scan or RF verification.')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(verify(a.controller),indent=2)+'\n')
    print('Original BSSID/SSID conflict queries and six full/empty AP reports PASS')
