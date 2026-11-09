#!/usr/bin/env python3
"""Execute original ONU Start Download replacement of partial receive state."""
import argparse
import json
from pathlib import Path
import struct
from test_fttr_vlan import Slave


class DownloadSlave(Slave):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.hooks[0x11f68] = 'download_timer_reset'
        self.events = []
        self.flags = (0, 0, 0)

    def handler(self, name, a):
        o = self.oracle
        if name == 'MIB_Get':
            assert a[0] == 7 and a[2] == 62
            row = bytearray(62); row[:2] = o.uc.mem_read(a[1], 2)
            row[17:20] = bytes(self.flags)
            o.uc.mem_write(a[1], bytes(row)); return 0
        if name == 'remove':
            self.events.append('remove-partial-file'); return 0
        if name == 'free':
            assert a[0] == self.p+0x6000
            self.events.append('free-window'); return 0
        if name == 'malloc':
            self.events.append(('allocate-window', a[0])); return self.p+0x7000
        if name == 'feature_api':
            assert a[0] == 29
            return 0x100  # The feature owns flash validity; isolate RX reset.
        if name == 'download_timer_reset':
            self.events.append('timer-reset'); return 0
        return super().handler(name, a)

    def restart(self, window, flags):
        o=self.oracle; ctrl=o.symbols['gOmciSwDlCtrl'];self.flags=flags;self.events=[]
        o.uc.mem_write(o.symbols['MIB_TABLE_SWIMAGE_INDEX'],struct.pack('<I',7))
        prior=bytearray(b'\x5a'*96);prior[40:48]=struct.pack('<Q',self.p+0x6000)
        o.uc.mem_write(ctrl,bytes(prior))
        request=bytearray(44);request[:8]=bytes.fromhex('8123530a00070001');request[8]=window-1
        request[9:13]=struct.pack('>I',12345)
        o.uc.mem_write(self.p,bytes(request).ljust(48,b'\0'));o.uc.mem_write(self.p+256,bytes(1992))
        assert o.call('OMCI_NormalizeMsg',self.p,self.p+256)==0
        o.call('OMCI_OnStartSWDL',self.p+256)
        o.uc.mem_write(self.p+4096,self.normalized);o.call('serialize',self.p+4096)
        result=bytes(o.uc.mem_read(ctrl,96))
        if flags[0] or flags[1]:
            assert self.wire[8]==3 and not self.events and result==prior
        else:
            assert self.wire[8]==0 and self.wire[9]==window-1
            assert self.events==['remove-partial-file','free-window',('allocate-window',window*31),'timer-reset']
            assert result[0]==window-1 and struct.unpack_from('<I',result,4)[0]==12345
            assert result[8:40]==bytes(32) and result[48:90]==bytes(42)
            assert struct.unpack_from('<Q',result,40)[0]==self.p+0x7000
        return dict(window=window,flags=flags,response=self.wire.hex(),events=self.events)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--slave',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();s=DownloadSlave(a.slave)
    cases=[s.restart(w,flags) for w in (1,16,31,64) for flags in ((0,0,0),(0,0,1),(1,0,1),(0,1,1))]
    a.output.write_text(json.dumps(dict(cases=cases,scope='Actual original ONU normalizer/Start handler/serializer; MIB, allocations, file removal, timer and validity feature are modeled. New Start resets partial receive state; no cross-process offset resume or flash-in-progress safety proven.'),indent=2)+'\n')
    print('Original ONU restart:16 window/slot-flag cases, old file/window reset and inactive-slot refusal PASS')
