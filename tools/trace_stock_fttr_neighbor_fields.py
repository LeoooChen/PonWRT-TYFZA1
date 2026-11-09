#!/usr/bin/env python3
"""Execute the target manager's neighbor JSON conversion after its file scanner."""
import argparse
import json
from pathlib import Path
import struct

from trace_stock_fttr_wifi_fields import WifiFieldsOriginal
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP


class NeighborFieldsOriginal(WifiFieldsOriginal):
    def handler(self, name, a):
        if name == 'strchr':
            text = self.string(a[0])
            index = text.find(chr(a[1]))
            return a[0] + index if index >= 0 else 0
        if name == 'snprintf' and self.string(a[2]) == '-%s':
            text = ('-' + self.string(a[3])).encode()
            self.oracle.uc.mem_write(a[0], text[:a[1]-1] + b'\0')
            return len(text)
        return super().handler(name, a)

    def convert(self, values):
        # Input pointers are the actual destinations of the target's sscanf
        # at0x45092c. The parser boundary is modeled, not executed here.
        o = self.oracle
        sp = o.scratch + 0x1fd000
        o.uc.mem_write(sp, bytes(4096))
        o.uc.reg_write(UC_ARM64_REG_SP, sp)
        result = self.object([])
        o.uc.mem_write(sp+728, struct.pack('<Q', result))
        o.uc.mem_write(sp+776, struct.pack('<Q', self.text_pointer(self.peer.hex().upper())))
        for location, field in ((0x4a64b8,1),(sp+0x128,2),(sp+0x108,3),
                                (sp+0xf8,4),(sp+0xf0,5),(sp+0xe8,6),(sp+0xe0,7)):
            o.uc.mem_write(location, values[field].encode()+b'\0')
        o.uc.emu_start(0x450a24,0x450b7c,count=10000)
        assert o.uc.reg_read(UC_ARM64_REG_PC)==0x450b7c
        assert len(self.objects[result])==1
        return self.objects[result][0]


def verify(manager, mib):
    stock = NeighborFieldsOriginal(manager,mib)
    rows = []
    for band in ('2.4G','5G'):
        for rssi in ('55','-55','0','00','-0'):
            values=['1',band,'Synthetic AP','020000000001','11ax','36',rssi,'80']
            value=stock.convert(values)
            assert value==dict(MAC=stock.peer.hex().upper(),RadioType=band,SSIDName=values[2],
                               BSSID=values[3],NetworkType=values[4],Channel=values[5],
                               RSSI=('-'+rssi if '-' not in rssi and not rssi.startswith('0') else rssi),
                               Standard=values[7])
            rows.append(dict(line='row:'+','.join(values)+'\n',fields=value))
    return dict(cases=rows,scope='Actual target JSON field conversion and RSSI prefix branch; sscanf/file data destinations are modeled. No scan completion, overlong input safety or physical RF measurement claim.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('manager','mib','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(verify(a.manager,a.mib),indent=2)+'\n')
    print('Original neighbor fields: two bands and five RSSI forms through actual JSON conversion PASS')
