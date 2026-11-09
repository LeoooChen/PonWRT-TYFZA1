#!/usr/bin/env python3
"""Execute retained Reboot/Recovery builders, dispatcher and pending replies."""
import argparse
import json
from pathlib import Path
import struct
from trace_stock_fttr_channel_auto import ChannelOriginal
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal


class ControlOriginal(ChannelOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols.update(reboot=0x4121d0,recovery=0x4122ec)

    def request(self, recovery=False):
        p=self.object({});target=self.peers+0x3000
        self.oracle.uc.mem_write(target,self.peer)
        assert self.oracle.call('recovery' if recovery else 'reboot',target,p)==1
        return self.objects[p]

    def dispatch_control(self, method, selected):
        u=self.oracle.uc;self.outputs=[]
        for i in range(16):
            row=bytearray(512);row[:6]=bytes([2,0,0,0,0,i+1]);row[7]=i
            struct.pack_into('<I',row,464,int(i in (0,5,15)))
            u.mem_write(self.peers+i*512,bytes(row))
        target=self.peers+0x3000
        if selected is not None:u.mem_write(target,bytes([2,0,0,0,0,selected+1]))
        assert self.oracle.call('dispatch',method,target if selected is not None else 0,0,0)==1
        for item in self.outputs:
            assert struct.unpack('<I',u.mem_read(self.peers+item['slot']*512+476,4))[0]==method
        return list(self.outputs)


def verify(binary, recovery=False):
    stock=ControlOriginal(binary);wire=SyncOriginal(binary);ack=SettingsOriginal(binary)
    method='Recovery' if recovery else 'Reboot';pending=15 if recovery else 14
    request=stock.request(recovery)
    assert request==dict(RPCMethod=method,MAC=stock.mac.hex().upper(),TargetSlave=stock.peer.hex().upper())
    dispatch=[]
    for selected in (None,0,5,15,7):
        sent=stock.dispatch_control(pending,selected)
        slots=[0,5,15] if selected is None else [selected] if selected in (0,5,15) else []
        assert sent==[dict(slot=i,request=True,json=dict(RPCMethod=method,MAC=stock.mac.hex().upper(),
                    TargetSlave=bytes([2,0,0,0,0,i+1]).hex().upper())) for i in slots]
        dispatch.append(dict(selected=selected,slots=slots))
    replies=[]
    for value in ({'Result':1},{'Result':0},{'RPCMethod':method,'Result':1}):
        state,files=ack.reply(value,pending=pending)
        assert state==(0,2,0) and not files
        replies.append(dict(reply=value,state=state))
    return dict(request=request,frame=wire.encode(request,ethertype=0x88aa).hex(),dispatch=dispatch,acks=replies,
                scope='Actual target builders,16-slot online/selected dispatch and pending ACK consumer. No subordinate reboot, configuration erase or completion verification; JSON/MIB/peer storage modeled.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps([verify(a.controller),verify(a.controller,True)],indent=2)+'\n')
    print('Original Reboot/Recovery: target-bound requests,10 online dispatch cases and6 pending replies PASS')
