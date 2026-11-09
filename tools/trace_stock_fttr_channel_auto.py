#!/usr/bin/env python3
"""Execute target automatic-channel request, peer selection and pending ACK."""
import argparse
import json
from pathlib import Path
import struct
from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal


class ChannelOriginal(SettingsOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols.update(channel=0x412160, dispatch=0x4138c4)
        self.peers=self.packet+0x10000

    def handler(self,name,a):
        if name=='peer_lookup':return self.peers+(a[0]&15)*512
        if name=='memcmp':
            x=bytes(self.oracle.uc.mem_read(a[0],a[2]));y=bytes(self.oracle.uc.mem_read(a[1],a[2]))
            return (x>y)-(x<y)
        return super().handler(name,a)

    def query(self):
        p=self.object({});assert self.oracle.call('channel',p)==1
        return self.objects[p]

    def dispatch(self, selected=None):
        u=self.oracle.uc;self.outputs=[]
        for i in range(16):
            row=bytearray(512);row[:6]=bytes([2,0,0,0,0,i+1]);row[7]=i
            struct.pack_into('<I',row,464,int(i in (0,5,15)))
            u.mem_write(self.peers+i*512,bytes(row))
        target=self.peers+0x3000
        if selected is not None:u.mem_write(target,bytes([2,0,0,0,0,selected+1]))
        assert self.oracle.call('dispatch',23,target if selected is not None else 0,0,0)==1
        pending=[struct.unpack('<II',u.mem_read(self.peers+i*512+468,8))[0] for i in range(16)]
        return list(self.outputs),pending


def verify(binary):
    stock=ChannelOriginal(binary);wire=SyncOriginal(binary);request=stock.query()
    assert request=={'RPCMethod':'ChannelAutoSelect'}
    dispatch=[]
    for selected in (None,0,5,15,7):
        sent,pending=stock.dispatch(selected)
        slots=[0,5,15] if selected is None else [selected] if selected in (0,5,15) else []
        assert sent==[dict(slot=i,request=True,json=request) for i in slots]
        assert [i for i,v in enumerate(pending) if v==2]==slots
        dispatch.append(dict(selected=selected,slots=slots))
    # A separate original instance retains the existing reply fixture mapping.
    ack=SettingsOriginal(binary);responses=[]
    for value in ({'Result':1},{'Result':0},{'RPCMethod':'ChannelAutoSelect','Result':1}):
        state,files=ack.reply(value,pending=23)
        assert state==(0,2,0) and not files
        responses.append(dict(reply=value,state=state))
    return dict(request=request,frame=wire.encode(request,ethertype=0x88aa).hex(),dispatch=dispatch,acks=responses,
                scope='Actual target builder, online-peer dispatch loop and pending23 ACK. Registered peers and JSON callbacks modeled. No channel scan or RF change executed; Result0 also clears original pending state.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(verify(a.controller),indent=2)+'\n')
    print('Original ChannelAutoSelect: exact request, all/selected/absent peer dispatch and three pending23 replies PASS')
