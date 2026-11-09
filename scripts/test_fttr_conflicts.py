#!/usr/bin/env python3
"""Compare native AP conflict queries/reports with target instructions over veth."""
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

from trace_stock_fttr_conflicts import verify

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--controller', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--ssid', action='store_true')
p.add_argument('--stage', type=Path)
p.add_argument('--openwrt', type=Path)
a = p.parse_args()
assert bool(a.stage) == bool(a.openwrt)
assert {v['ifname'] for v in json.loads(subprocess.check_output(['ip','-j','link'], text=True))} == {'lo'}
evidence = verify(a.controller)
original = evidence['cases'][4 if a.ssid else 1]
method = original['query']['RPCMethod']
command_name = 'ssid-conflicts' if a.ssid else 'bssid-conflicts'
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
    subprocess.run(['ip','link','add','station-host','type','veth','peer','name','station-peer'], check=True)
    for dev, mac in (('station-host',source),('station-peer',target)):
        subprocess.run(['ip','link','set',dev,'address',mac.hex(':'),'mtu','1982','up'], check=True)
    remote = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa))
    remote.bind(('station-peer',0))

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

    variants = ('normal','empty','one','maximum','too-many','auto','fragment','request-type',
                'foreign-source','wrong-method','nack','ack-only','timeout','reregister','no-method',
                'no-list','bad-list','bad-row','missing-onu','missing-sta','foreign-onu','bad-mac',
                'nul','long','numeric','unknown','partial-fields','same-ap-two-ssids',
                'missing-report-mac','wrong-report-mac','top-level-extra','ssid-punctuation',
                'fragment-missing','fragment-conflict','bad-json','bad-result')
    if not a.stage:variants+=('ack-send-failure',)
    for case in variants:
        while select.select([remote],[],[],0)[0]:remote.recv(16384)
        rows = copy.deepcopy(original['report']['ApInfo'])
        if case == 'empty': rows=[]
        if case == 'one': rows=rows[:1]
        if case in ('maximum','too-many'):
            rows=[dict(rows[0],wlanMac=f'02000000{i:04X}') for i in range(65 if case=='too-many' else 64)]
        if case == 'bad-row': rows[0]=None
        if case == 'missing-onu': del rows[0]['onuMac']
        if case == 'missing-sta': del rows[0]['wlanMac']
        if case == 'foreign-onu': rows[0]['onuMac']='020000000099'
        if case == 'bad-mac': rows[0]['wlanMac']='030000000001'
        if case == 'nul': rows[0]['onuModel']='wlan\0other'
        if case == 'long': rows[0]['onuModel']='x'*257
        if case == 'numeric': rows[0]['ssidIndex']=1
        if case == 'unknown': rows[0].update(Password='secret-not-to-print',VendorObject={'x':1})
        if case == 'partial-fields': rows=[dict(onuMac=rows[0]['onuMac'],wlanMac=rows[0]['wlanMac'])]
        if case == 'same-ap-two-ssids': rows.append(dict(rows[0],ssidIndex='2'))
        if case == 'ssid-punctuation': rows[0]['ssid']='合成,SSID\n"quoted"'
        report=dict(RPCMethod=method,mac=target.hex().upper(),ApInfo=rows)
        if case=='top-level-extra':report['IgnoredVendorField']={}
        if case=='missing-report-mac':del report['mac']
        if case=='wrong-report-mac':report['mac']='020000000077'
        env=os.environ.copy()
        if case=='ack-send-failure':env.update(LD_PRELOAD=str(d/'fault.so'),FTTR_TEST_STATION_ACK_FAIL='1')
        proc=subprocess.Popen([*command,'station-host',target.hex(':'),'auto' if case=='auto' else '5','0x88aa',command_name],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        end=time.monotonic()+7
        requests=0
        acknowledgements=[]
        try:
            while proc.poll() is None:
                assert time.monotonic()<end,case
                if not select.select([remote],[],[],.01)[0]:continue
                frame,meta=remote.recvfrom(16384)
                if meta[2]==socket.PACKET_OUTGOING:continue
                if frame[14]&15==1:
                    assert case=='auto';remote.send(source+target+b'\x88\xaa\x01\0');continue
                if frame[14]&15==7:
                    acknowledgements.append(frame);continue
                requests+=1
                assert requests==1 and frame[:12]==target+source
                assert json.loads(frame[20:])==original['query']
                if case!='auto':assert frame==bytes.fromhex(original['frame'])
                if case=='timeout':continue
                if case=='reregister':send({'RPCMethod':'Register'});continue
                if case=='nack':send({'RPCMethod':method,'Result':0});continue
                if case=='bad-result':send(dict(report,Result=True));continue
                if case=='ack-only':send({'Result':1});continue
                if case=='no-method':send({'Result':1,'ApInfo':rows});continue
                if case=='no-list':send({'RPCMethod':method});continue
                if case=='bad-list':send(dict(report,ApInfo={}));continue
                if case=='bad-json':send(b'{broken');continue
                if case=='foreign-source':send(dict(report,Result=0),foreign=True)
                if case=='wrong-method':send(dict(report,RPCMethod='GetWifiRadio',Result=0))
                if case in ('fragment','maximum'):send({'Result':1})
                send(report,fragments=case.startswith('fragment') or case=='maximum',missing=case=='fragment-missing',
                     conflict=case=='fragment-conflict',kind=6 if case=='request-type' else 7)
            out,err=proc.communicate(timeout=1)
            while select.select([remote],[],[],0)[0]:
                frame,meta=remote.recvfrom(16384)
                if meta[2]!=socket.PACKET_OUTGOING:
                    assert frame[14]&15==7
                    acknowledgements.append(frame)
            good=case in ('normal','empty','one','maximum','auto','fragment','request-type','foreign-source','wrong-method',
                         'unknown','foreign-onu','same-ap-two-ssids','top-level-extra','ssid-punctuation','numeric')
            assert (proc.returncode==0)==good,(case,out,err)
            assert requests==1
            assert len(acknowledgements)==int(good),(case,acknowledgements)
            if good:
                frame=acknowledgements[0]
                assert frame[:12]==target+source and json.loads(frame[20:])=={}
            if good:
                result=json.loads(out)
                expected=[{k:v for k,v in row.items() if k not in ('Password','VendorObject')} for row in rows]
                assert result['ApInfo']==expected and result['report_complete'] and not result['identity_authenticated'] and not result['scan_verified']
            else:assert not out,(case,out)
            assert 'secret-not-to-print' not in out+err
            cases.append(dict(case=case,requests=requests,exit=proc.returncode))
        finally:
            if proc.poll() is None:proc.kill();proc.communicate()
    remote.close()
a.output.write_text(json.dumps(dict(cases=cases,scope='Actual native CLI and AF_PACKET/reassembly, target query bytes and AP report parser contract; remote conflict reports modeled, no RF/association acceptance.'),indent=2)+'\n')
print(f'AP conflict reports: {len(cases)} CLI/fragment/identity/empty/64-row/error cases PASS')
