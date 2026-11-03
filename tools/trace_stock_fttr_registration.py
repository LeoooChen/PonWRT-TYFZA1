#!/usr/bin/env python3
"""Execute retained JSON Register handling and its protocol transition."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from trace_stock_fttr_settings import SettingsOriginal


class RegistrationOriginal(SettingsOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.ready = 0
        self.loids = []
        self.oracle.uc.mem_write(0x42ea80, bytes(8))

    def handler(self, name, a):
        u = self.oracle.uc
        if name == 'strncpy':
            text = self.string(a[1]).encode()
            u.mem_write(a[0], text[:a[2]].ljust(a[2], b'\0'))
            return a[0]
        if name == 'memset':
            u.mem_write(a[0], bytes([a[1] & 255])*a[2])
            return a[0]
        if name == 'mib_get_s' and a[0] in (341, 396, 2025):
            text = {341:b'',396:b'020304',2025:b'synthetic-host-serial'}[a[0]]
            u.mem_write(a[1], (text+b'\0').ljust(a[2], b'\0'))
            return 1
        if name == 'mib_chain_total':
            assert a[0] == 11601
            return 0
        if name == 'mib_chain_add':
            assert a[0] == 11601
            self.loids.append(bytes(u.mem_read(a[1],550)))
            return 1
        if name == 'mib_update':
            assert a[:2] == [8,0]
            return 1
        if name == 'isMasterReady':
            return self.ready
        if name == 'strcpy':
            u.mem_write(a[0], self.string(a[1]).encode()+b'\0')
            return a[0]
        return super().handler(name,a)

    def register(self, value, prior_protocol=0):
        u = self.oracle.uc
        row = self.packet + 0x9000
        u.mem_write(row, self.peer + bytes([0,5]) + bytes(487))
        u.mem_write(row+464,struct.pack('<IIII',1,0,prior_protocol,0))
        # Modern/province-dependent MIB persistence is outside this baseline
        # handshake. No local settings, service readiness or secret is inferred.
        u.mem_write(0x42ea80,bytes(8))
        u.mem_write(0x44255c,bytes(4))
        text = json.dumps(value,separators=(',',':')).encode()
        u.mem_write(self.text+0x4000,len(text).to_bytes(4,'big')+text+b'\0')
        self.outputs.clear();self.loids.clear()
        self.oracle.call('reply',5,self.text+0x4000,len(text)+4)
        fields = {}
        for name,offset,size in [('DevName',12,128),('LOID',140,64),('Manufacturer',204,64),
                                 ('ModelName',268,64),('OUI',332,12),('SN',344,64),('GPONSN',408,16)]:
            fields[name] = bytes(u.mem_read(row+offset,size)).split(b'\0',1)[0].decode()
        return {'fields':fields,'protocol':struct.unpack('<I',u.mem_read(row+472,4))[0],
                'reply':self.outputs[:], 'saved_rows':len(self.loids)}


def verify(binary):
    stock=RegistrationOriginal(binary)
    value={'RPCMethod':'Register','DevName':'synthetic-peer','LOID':'synthetic-loid',
           'Manufacturer':'synthetic-vendor','ModelName':'synthetic-model','OUI':'0A0B0C',
           'SN':'synthetic-peer-serial','GPONSN':'ABCD12345678'}
    result=[]
    for ready in (0,1):
        stock.ready=ready
        for prior in (0,1,2):
            case=stock.register(value,prior)
            assert case['protocol']==1
            assert case['fields']=={k:v for k,v in value.items() if k!='RPCMethod'}
            assert case['reply']==[{'slot':5,'request':False,'json':{
                'Result':1,'MAC':stock.mac.hex().upper(),'OUI':'020304',
                'SN':'synthetic-host-serial','Ready':ready}}]
            result.append({'ready':ready,'prior_protocol':prior,'result':case})
    # Register is not an identity challenge: even empty self-description is
    # accepted. Repeated registration downgrades the original protocol to1.
    assert stock.register({'RPCMethod':'Register'},2)['protocol']==1
    stock.oracle.uc.mem_write(stock.packet+0x9000+468,struct.pack('<III',2,1,11))
    text=b'{"Result":1}'
    stock.oracle.uc.mem_write(stock.text+0x4000,len(text).to_bytes(4,'big')+text+b'\0')
    stock.oracle.call('reply',5,stock.text+0x4000,len(text)+4)
    assert struct.unpack('<III',stock.oracle.uc.mem_read(stock.packet+0x9000+468,12))==(0,2,0)
    return {'controller_sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
            'cases':result,
            'findings':['Register copies7 optional peer-description strings and sets protocol1',
                        'reply Result1 MAC OUI SN Ready; Ready comes from isMasterReady',
                        'no automatically assigned LOID returned with empty saved LOID list',
                        'registration does not authenticate peer; even missing identifiers accepted by original',
                        'positive SetAllSetting ACK sets protocol2; repeat Register returns it to1'],
            'scope':'Actual baseline original Register dispatcher, description copies, LOID lookup and reply producer; JSON-C, MIB and readiness modeled. Modern/province-specific side effects disabled explicitly. No slave acceptance or identity authentication.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=verify(args.controller)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'cases':len(result['cases']),'findings':result['findings']},indent=2))


if __name__=='__main__':main()
