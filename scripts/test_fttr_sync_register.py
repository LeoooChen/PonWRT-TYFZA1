#!/usr/bin/env python3
"""Compare original Register reply with native pinned handshake over AF_PACKET."""
import argparse
import copy
import errno
import json
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from trace_stock_fttr_registration import RegistrationOriginal
from trace_stock_fttr_sync import SyncOriginal
from trace_stock_fttr_fragments import fragments


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
    stock=RegistrationOriginal(args.controller)
    encoder=SyncOriginal(args.controller)
    group=bytes.fromhex('0110ec000002');master=bytes.fromhex('0110ec000001')
    policy={'OUI':'020304','SN':'synthetic-host-serial','Ready':0,'ExpectedSN':'synthetic-peer-serial',
            'ExpectedGPONSN':'ABCD12345678'}
    registration={'RPCMethod':'Register','DevName':'synthetic-peer','LOID':'do-not-print-loid',
                  'Manufacturer':'synthetic-vendor','ModelName':'synthetic-model','OUI':'0A0B0C',
                  'SN':'synthetic-peer-serial','GPONSN':'ABCD12345678'}
    with tempfile.TemporaryDirectory() as name:
        directory=Path(name);src=ROOT/'port/packages/tyfza1-fttr/src';binary=directory/'fttr-sync'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                        *[str(src/n) for n in ('fttr-sync.c','sync-client.c','sync-controller.c','sync-store.c','sync-wire.c','sync-peer.c','sync-wifi.c','sync-json.c',
                                               'sync-register.c','omci-socket.c','omci-wire.c')],
                        '-ljson-c','-o',str(binary)],check=True)
        subprocess.run(['ip','link','add','register-host','type','veth','peer','name','register-peer'],check=True)
        for interface,mac in (('register-host',stock.mac),('register-peer',stock.peer)):
            subprocess.run(['ip','link','set',interface,'address',mac.hex(':'),'mtu','1982','up'],check=True)
        peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa))
        peer.bind(('register-peer',0x88aa));peer.setblocking(False)
        config=directory/'policy.json'
        cases=[]

        def run(case):
            desired=copy.deepcopy(policy)
            value=copy.deepcopy(registration)
            if case=='ready':desired['Ready']=1
            if case=='no-gpon-pin':del desired['ExpectedGPONSN'];del value['GPONSN']
            if case=='wrong-sn':value['SN']='different'
            if case=='wrong-gpon':value['GPONSN']='different'
            if case=='missing-sn':del value['SN']
            if case=='empty-sn':value['SN']=''
            if case=='empty-loid':value['LOID']=''
            if case=='overflow':value['DevName']='x'*128
            if case=='nul':value['SN']='synthetic-peer-serial\0bad'
            if case=='wrong-type':value['ModelName']=3
            if case=='policy-ready':desired['Ready']=4294967297
            if case=='policy-pin':del desired['ExpectedSN']
            if case=='policy-extra':desired['WebAdminPassword']='do-not-print-password'
            config.write_text(json.dumps(desired));config.chmod(0o600)
            if case=='permissions':config.chmod(0o644)
            command=[str(binary),'register-host',stock.peer.hex(':'),'auto' if case=='auto' else '5',
                     '0x88aa','register',str(config)]
            proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+9;beats=replies=0

            def send(message,source=stock.peer):
                encoded=encoder.encode(message,15,case!='response-type')
                peer.send(master+source+encoded[12:])

            try:
                while proc.poll() is None:
                    assert time.monotonic()<deadline,case
                    ready,_,_=select.select([peer],[],[],0.02)
                    if not ready:continue
                    frame,address=peer.recvfrom(4096)
                    if address[2]==socket.PACKET_OUTGOING:continue
                    assert frame[:12]==group+stock.mac
                    if frame[14]&15==1:
                        beats+=1
                        if case=='timeout':continue
                        peer.send(master+stock.peer+b'\x88\xaa\x01\0')
                        if case=='auto' and beats==1:continue
                        if case=='unrelated':
                            send(value,bytes.fromhex('020000000099'))
                            send({'RPCMethod':'SetAllSetting','Flag':1,'STATUS':0})
                        if case=='fragment':
                            body=json.dumps(value,separators=(',',':')).encode()
                            body=len(body).to_bytes(4,'big')+body
                            for part,flags in reversed(fragments(body,3,2)):
                                peer.send(master+stock.peer+b'\x88\xaa\xf6'+bytes([flags])+part)
                        else:send(value)
                    else:
                        replies+=1
                        assert frame[14]==(0x07 if case=='auto' else 0x57)
                        stock.ready=desired['Ready']
                        expected=stock.register(value)['reply'][0]['json']
                        original=encoder.encode(expected,0 if case=='auto' else 5,False)
                        assert frame==group+original[6:],(case,json.loads(frame[20:]),expected)
                output,error=proc.communicate(timeout=1)
            finally:
                if proc.poll() is None:proc.kill();proc.communicate()
            assert 'do-not-print' not in output+error
            invalid=case.startswith('policy-') or case=='permissions'
            errors={'wrong-sn':errno.ESTALE,'wrong-gpon':errno.ESTALE,'missing-sn':errno.EBADMSG,
                    'empty-sn':errno.EBADMSG,'overflow':errno.EBADMSG,'nul':errno.EBADMSG,
                    'wrong-type':errno.EBADMSG,'timeout':errno.ETIMEDOUT}
            if invalid or case in errors:
                assert proc.returncode==1 and not output and replies==0,(case,error,output)
                if invalid:assert beats==0
                else:assert str(-errors[case]) in error
            else:
                assert proc.returncode==0 and replies==1,(case,error,output,replies)
                result=json.loads(output)
                assert result['identity_pin_match'] and not result['identity_authenticated']
                assert not result['peer_acceptance_verified'] and result['advertised_ready']==desired['Ready']
                assert result['slot']==(0 if case=='auto' else 5)
            cases.append(case)

        for case in ('normal','ready','auto','no-gpon-pin','empty-loid','unrelated','fragment','response-type',
                     'wrong-sn','wrong-gpon','missing-sn','empty-sn','overflow','nul','wrong-type','timeout',
                     'policy-ready','policy-pin','policy-extra','permissions'):
            run(case)
        peer.close()
    result={'packet_cases':cases,'checks':['native reply bytes match original baseline Register for explicit master identity/readiness',
            'explicit serial pins reject replacement/missing identifiers before sending any reply',
            'both JSON frame types, group routing, fragments and automatic heartbeat discovery',
            'invalid policy never sends packets; sensitive received fields are not printed'],
            'scope':'Actual original baseline Register producer vs native AF_PACKET/veth handshake with modeled slave; no real slave acceptance, optical readiness or cryptographic authentication.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
