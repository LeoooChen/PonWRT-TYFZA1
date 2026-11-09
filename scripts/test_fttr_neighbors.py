#!/usr/bin/env python3
"""Compare native neighbor report collection with target instructions over veth."""
import argparse
import copy
import json
import os
import select
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from trace_stock_fttr_neighbors import verify

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--controller', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--stage', type=Path)
p.add_argument('--openwrt', type=Path)
a = p.parse_args()
assert bool(a.stage) == bool(a.openwrt)
assert {v['ifname'] for v in json.loads(subprocess.check_output(['ip','-j','link'], text=True))} == {'lo'}
original = verify(a.controller)
source = bytes.fromhex('020304050607')
target = bytes.fromhex('0a0b0c0d0e0f')
cases = []
with tempfile.TemporaryDirectory() as directory:
    d = Path(directory)
    src = ROOT/'port/packages/tyfza1-fttr/src'
    binary = d/'fttr-sync'
    names = ('fttr-sync','sync-client','sync-controller','sync-store','sync-wire','sync-peer',
             'sync-wifi','sync-json','sync-register','omci-socket','omci-wire')
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*[str(src/(n+'.c')) for n in names],
                    '-ljson-c','-o',str(binary)], check=True)
    (d/'fault.c').write_text(r'''#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdlib.h>
#include <sys/socket.h>
ssize_t send(int fd,const void *p,size_t n,int flags){
 ssize_t (*original)(int,const void*,size_t,int)=dlsym(RTLD_NEXT,"send");
 if(getenv("FTTR_TEST_STATION_ACK_FAIL")&&n>=20&&(((const unsigned char*)p)[14]&15)==7){errno=EIO;return -1;}
 return original(fd,p,n,flags);
}
''')
    subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(d/'fault.c'),'-ldl','-o',str(d/'fault.so')],check=True)
    command = [str(binary)]
    if a.stage:
        loader = next(a.openwrt.glob('staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/lib/libc.so'))
        libs = a.openwrt/'staging_dir/target-aarch64_cortex-a55_musl/usr/lib'
        command = [str(loader),'--library-path',str(libs)+':'+str(loader.parent),str(a.stage/'usr/sbin/fttr-sync')]
    subprocess.run(['ip','link','add','neighbor-host','type','veth','peer','name','neighbor-peer'], check=True)
    for dev, mac in (('neighbor-host',source),('neighbor-peer',target)):
        subprocess.run(['ip','link','set',dev,'address',mac.hex(':'),'mtu','1982','up'], check=True)
    remote = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa))
    remote.bind(('neighbor-peer',0))

    def send(value, foreign=False, fragments=False, missing=False, conflict=False, kind=7):
        text = value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
        body = len(text).to_bytes(4,'big')+text
        prefix = source+(bytes.fromhex('020000000099') if foreign else target)+b'\x88\xaa'+bytes([0xf0|kind])
        if not fragments and len(body)<=1980:
            remote.send(prefix+b'\0'+body)
            return
        size = 1900 if len(body)>1980 else (len(body)+1)//2
        parts = [body[i:i+size] for i in range(0,len(body),size)]
        assert 1<len(parts)<=8
        frames = [prefix+bytes([((len(parts)-1)<<5)|(i<<2)|1])+part for i,part in enumerate(parts)]
        if missing: frames = frames[1:]
        for frame in reversed(frames):
            remote.send(frame)
            if conflict:
                remote.send(frame[:-1]+bytes([frame[-1]^1]));return

    variants=('single','empty','append','replace','duplicate','auto','fragment','request-type',
              'foreign-source','wrong-method','nack','ack-only','timeout','reregister','no-method','no-data',
              'bad-mac','bad-flag','orphan','nul','bad-json','bad-result','fragment-missing','fragment-conflict',
              'pending-fragment','bad-after-valid','too-many','too-large','late','split-line','auto-orphan')
    if not a.stage: variants+=('ack-send-failure',)
    def report(flag='NEW',text='row:1,5G,Synthetic,020000000001,11ax,36,-55,80\n'):
        return dict(RPCMethod='GetWifiNeighbor',MAC=target.hex().upper(),RECVFLAG=flag,scanResult=text)
    for case in variants:
        while select.select([remote],[],[],0)[0]:remote.recv(16384)
        expected=report()['scanResult'];reports=1;restarts=0;valid_reports=1
        env=os.environ.copy()
        if case=='ack-send-failure':env.update(LD_PRELOAD=str(d/'fault.so'),FTTR_TEST_STATION_ACK_FAIL='1')
        proc=subprocess.Popen([*command,'neighbor-host',target.hex(':'),'auto' if case in ('auto','auto-orphan') else '5','0x88aa','wifi-neighbors'],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        end=time.monotonic()+7;requests=0;acknowledgements=[];start=time.monotonic();delayed=None
        try:
            while proc.poll() is None:
                assert time.monotonic()<end,case
                if delayed and time.monotonic()>=delayed:
                    send(report('REMAINING','later'));delayed=None
                if not select.select([remote],[],[],.01)[0]:continue
                frame,meta=remote.recvfrom(16384)
                if meta[2]==socket.PACKET_OUTGOING:continue
                if frame[14]&15==1:
                    assert case in ('auto','auto-orphan');remote.send(source+target+b'\x88\xaa\x01\0');continue
                if frame[14]&15==7:
                    acknowledgements.append(frame);continue
                requests+=1;assert requests==1 and frame[:12]==target+source
                assert json.loads(frame[20:])==original['query']
                if case not in ('auto','auto-orphan'):assert frame==bytes.fromhex(original['frame'])
                value=report()
                if case=='timeout':valid_reports=0;continue
                if case=='reregister':send(value);send({'RPCMethod':'Register'});continue
                if case=='nack':send({'Result':0});valid_reports=0;continue
                if case=='bad-result':send(dict(value,Result=True));valid_reports=0;continue
                if case=='ack-only':send({'Result':1});valid_reports=0;continue
                if case=='no-method':del value['RPCMethod'];valid_reports=0
                if case=='no-data':del value['scanResult'];valid_reports=0
                if case=='bad-mac':value['MAC']='020000000099';valid_reports=0
                if case=='bad-flag':value['RECVFLAG']='FINAL';valid_reports=0
                if case in ('orphan','auto-orphan'):value['RECVFLAG']='REMAINING';valid_reports=0
                if case=='nul':value['scanResult']='a\0b';valid_reports=0
                if case=='bad-json':send(b'{broken');valid_reports=0;continue
                if case=='foreign-source':send(dict(value,RECVFLAG='BAD'),foreign=True)
                if case=='wrong-method':send(dict(value,RPCMethod='Other',Result=0))
                if case=='empty':value['scanResult']='';expected=''
                if case=='split-line':value['scanResult']='row:1,5G,';expected='row:1,5G,'
                if case=='too-large':value['scanResult']='x'*15000;valid_reports=4
                send(value,fragments=case.startswith('fragment') or case=='too-large',missing=case=='fragment-missing',
                     conflict=case=='fragment-conflict',kind=6 if case=='request-type' else 7)
                if case.startswith('fragment-'):valid_reports=0
                if case=='pending-fragment':send(report('REMAINING','unfinished'),fragments=True,missing=True)
                if case=='bad-after-valid':send(dict(value,RECVFLAG='BAD'))
                if case in ('append','duplicate','replace','split-line'):
                    extra='Synthetic\n' if case=='split-line' else 'second'
                    send(report('NEW' if case=='replace' else 'REMAINING',extra));reports=valid_reports=2
                    expected=extra if case=='replace' else expected+extra
                    restarts=int(case=='replace')
                    if case=='duplicate':send(report('REMAINING',extra));expected+=extra;reports=valid_reports=3
                if case=='too-many':
                    valid_reports=64
                    for i in range(64):send(report('REMAINING','x'))
                if case=='too-large':
                    for i in range(4):send(report('REMAINING','x'*15000),fragments=True)
                if case=='late':delayed=time.monotonic()+2.4;expected+='later';reports=valid_reports=2
            out,err=proc.communicate(timeout=1)
            while select.select([remote],[],[],0)[0]:
                frame,meta=remote.recvfrom(16384)
                if meta[2]!=socket.PACKET_OUTGOING:
                    assert frame[14]&15==7;acknowledgements.append(frame)
            good=case in ('single','empty','append','replace','duplicate','auto','fragment','request-type',
                         'foreign-source','wrong-method','late','split-line')
            assert (proc.returncode==0)==good,(case,out,err)
            assert requests==1
            assert len(acknowledgements)==(0 if case=='ack-send-failure' else valid_reports),(case,len(acknowledgements),valid_reports,out,err)
            for frame in acknowledgements:
                assert frame[:12]==target+source and json.loads(frame[20:])=={'Result':0}
                if case not in ('auto','auto-orphan'):assert frame==bytes.fromhex(original['acknowledgement'])
            if good:
                result=json.loads(out)
                assert result['scanResult']==expected and result['reports']==reports and result['restarts']==restarts,(case,result,expected)
                assert not result['scan_complete'] and not result['identity_authenticated']
                assert time.monotonic()-start>=2.9
            else:assert not out,(case,out)
            cases.append(dict(case=case,requests=requests,acks=len(acknowledgements),exit=proc.returncode))
        finally:
            if proc.poll() is None:proc.kill();proc.communicate()
    remote.close()
a.output.write_text(json.dumps(dict(cases=cases,scope='Actual CLI/veth and report collection compared with original request/response/NEW/REMAINING. Fixed observation window never proves complete scan; remote payloads modeled.'),indent=2)+'\n')
print(f'Wireless neighbors: {len(cases)} collection/replace/append/limit/identity/error cases PASS')
