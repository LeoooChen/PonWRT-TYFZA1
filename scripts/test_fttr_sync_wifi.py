#!/usr/bin/env python3
"""Exercise actual Flag1 writes and independent field readback over AF_PACKET."""
import argparse
import copy
import ctypes as C
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
from trace_stock_fttr_wifi_fields import WifiFieldsOriginal


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manager',type=Path,required=True)
    p.add_argument('--mib',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
    stock=WifiFieldsOriginal(args.manager,args.mib)
    src=ROOT/'port/packages/tyfza1-fttr/src'
    source=bytes.fromhex('020304050607');target=stock.peer
    group=bytes.fromhex('0110ec000002');master=bytes.fromhex('0110ec000001')
    desired={'act':'config','SSIDIndex':'1','SSID':'do-not-print-new','ENCRYPT':4,
             'Enable':1,'PWD':'do-not-print-key','SSIDAdvertisementEnabled':'true','Standard':'11bgn'}
    baseline=stock.read_local()['List']
    with tempfile.TemporaryDirectory() as name:
        directory=Path(name);binary=directory/'fttr-sync'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                        *[str(src/n) for n in ('fttr-sync.c','sync-client.c','sync-controller.c','sync-wire.c','sync-peer.c','sync-wifi.c','sync-json.c','sync-register.c','omci-socket.c','omci-wire.c')],
                        '-ljson-c','-o',str(binary)],check=True)
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',
                        str(src/'sync-wifi.c'),str(src/'sync-json.c'),'-ljson-c','-o',str(directory/'wifi.so')],check=True)
        native=C.CDLL(str(directory/'wifi.so'))
        native.json_tokener_parse.argtypes=[C.c_char_p];native.json_tokener_parse.restype=C.c_void_p
        native.json_object_put.argtypes=[C.c_void_p]
        native.fttr_sync_wifi_validate.argtypes=[C.c_void_p]
        native.fttr_sync_wifi_check.argtypes=[C.c_void_p,C.c_void_p,C.c_bool]

        def check(entries,configuration,compare):
            a=native.json_tokener_parse(json.dumps(entries).encode())
            b=native.json_tokener_parse(json.dumps(configuration).encode())
            try:return native.fttr_sync_wifi_check(a,b,compare)
            finally:native.json_object_put(a);native.json_object_put(b)

        comparisons=0
        for ui,raw in ((1,0),(2,1),(3,2),(4,4),(5,6),(9,16),(10,20)):
            stock.row[1]=raw
            for index in (1,8,9,16):
                stock.row[338]=11 if index<=8 else 64
                entries=stock.read_local()['List']
                entry=entries[index-1]
                configuration=dict(desired,SSIDIndex=str(index),SSID=entry['SSID'],ENCRYPT=raw,
                                   PWD='' if raw==0 else 'abcde' if raw==1 else desired['PWD'],
                                   Standard=entry['Standard'])
                assert entry['ENCRYPT']==str(ui)
                assert check(entries,configuration,True)==0
                assert check(entries,dict(configuration,SSID='changed'),True)==-errno.EUCLEAN
                comparisons+=1
        stock.row[1]=4
        stock.row[338]=11
        assert check(baseline+[baseline[0]],desired,False)==-errno.EINVAL
        assert check([baseline[0],baseline[0]],desired,False)==-errno.EBADMSG
        assert check(baseline[1:],desired,False)==-errno.ENOENT
        assert check([dict(baseline[0],RadioType='5G')],desired,False)==-errno.EBADMSG
        for value in (None,[],1,'invalid'):
            assert check(baseline,value,False)==-errno.EINVAL
            assert check([value],desired,False)==-errno.EBADMSG

        subprocess.run(['ip','link','add','wifi-host','type','veth','peer','name','wifi-peer'],check=True)
        for interface,mac in (('wifi-host',source),('wifi-peer',target)):
            subprocess.run(['ip','link','set',interface,'address',mac.hex(':'),'mtu','1982','up'],check=True)
        peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa))
        peer.bind(('wifi-peer',0x88aa));peer.setblocking(False)
        config=directory/'desired.json'
        cases=[]

        def run(case,configuration=None):
            configuration=copy.deepcopy(configuration or desired)
            config.write_text(json.dumps(configuration)+'\n');config.chmod(0o600)
            if case=='permissions':config.chmod(0o644)
            if case=='bad-json':config.write_text('{')
            command=[str(binary),'wifi-host',target.hex(':'),'auto' if case=='auto' else '5',
                     '0x88aa','wifi-apply',str(config)]
            proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+11;gets=writes=0
            state=copy.deepcopy(baseline)
            for entry in state[8:]:entry['Standard']='11ac'

            def send(value):
                text=json.dumps(value,separators=(',',':')).encode()
                body=len(text).to_bytes(4,'big')+text
                count=(len(body)+1299)//1300
                if count==1:
                    peer.send(master+target+b'\x88\xaa\xf7\0'+body)
                else:
                    for index in range(count-1,-1,-1):
                        flags=(count-1)<<5|index<<2|1
                        peer.send(master+target+b'\x88\xaa\xf6'+bytes([flags])+body[index*1300:(index+1)*1300])

            try:
                while proc.poll() is None:
                    assert time.monotonic()<deadline,case
                    ready,_,_=select.select([peer],[],[],0.02)
                    if not ready:continue
                    frame,address=peer.recvfrom(4096)
                    if address[2]==socket.PACKET_OUTGOING:continue
                    if frame[14]&15==1:
                        assert case=='auto'
                        peer.send(master+target+b'\x88\xaa\x01\0');continue
                    assert frame[:12]==group+source and frame[14]&15==6
                    query=json.loads(frame[20:])
                    assert query['RPCMethod']=='SetAllSetting' and query['Flag']==1
                    assert query['DMAC']==target.hex().upper() and query['MAC']==source.hex().upper()
                    content=query['content']
                    if content['act']=='get':
                        gets+=1
                        if case=='preflight-timeout' or (case=='readback-timeout' and gets==2):continue
                        rows=copy.deepcopy(state)
                        if case=='missing':rows=rows[1:]
                        if case=='duplicate':rows[1]=copy.deepcopy(rows[0])
                        if case=='bad-encryption':rows[0]['ENCRYPT']='255'
                        if case=='wrong-band':rows[0]['RadioType']='5G'
                        if case=='readback-mismatch' and gets==2:rows[0]['SSID']='different'
                        send({'RPCMethod':'SetAllSetting','Flag':1,'STATUS':0,'content':{'content':{'List':rows}}})
                    else:
                        writes+=1
                        assert content==configuration and writes==1
                        # Compare with original full remote writer using its UI
                        # global index. Original maps it back to device-local1.
                        value={k:str(v) for k,v in configuration.items() if k!='act'}
                        value['SSIDIndex']=str(16+int(configuration['SSIDIndex']))
                        value['ENCRYPT']=str({0:1,1:2,2:3,4:4,6:5,16:9,20:10}[configuration['ENCRYPT']])
                        # For open networks the original rejects an explicit
                        # empty PWD but emits it when omitted. Its final wire
                        # content is still the explicit native empty key.
                        if not configuration['ENCRYPT']:del value['PWD']
                        actual=stock.write(value)['content']
                        if case=='ssid32':assert actual['SSID']==configuration['SSID'][:31]
                        else:assert actual==configuration
                        if case!='nack':
                            entry=state[int(configuration['SSIDIndex'])-1]
                            for key in ('SSID','Standard','SSIDAdvertisementEnabled'):entry[key]=configuration[key]
                            entry['Enable']=str(configuration['Enable'])
                            entry['ENCRYPT']=value['ENCRYPT']
                        if case=='write-timeout':continue
                        if case=='status-write':send({'RPCMethod':'SetAllSetting','Flag':1,'STATUS':0})
                        else:send({'Result':0 if case=='nack' else 1})
                output,error=proc.communicate(timeout=1)
            finally:
                if proc.poll() is None:proc.kill();proc.communicate()
            assert 'do-not-print' not in output+error
            preflight={'missing','duplicate','bad-encryption','wrong-band','preflight-timeout'}
            invalid=case.startswith('invalid-') or case in ('permissions','bad-json')
            if invalid or case in preflight:
                assert proc.returncode==1 and not output and writes==0,(case,error,output)
                assert gets==(0 if invalid else 1)
            else:
                assert gets==2 and writes==1,(case,gets,writes,error)
                result=json.loads(output)
                assert result['event']=='sync_wifi_apply' and result['write_attempted']
                assert not result['configuration_verified'] and not result['credentials_verified']
                assert result['values_redacted']
                failures={'write-timeout':(-errno.ETIMEDOUT,0),'nack':(-errno.EREMOTEIO,-errno.EUCLEAN),
                          'readback-timeout':(0,-errno.ETIMEDOUT),'readback-mismatch':(0,-errno.EUCLEAN)}
                expected=failures.get(case,(0,0))
                assert (result['write_result'],result['readback_result'])==expected,(case,result)
                assert result['readable_fields_match']==(expected[1]==0)
                assert proc.returncode==int(case in failures),(case,error)
            cases.append(case)

        for case in ('normal','auto','status-write','write-timeout','nack','readback-timeout','readback-mismatch',
                     'missing','duplicate','bad-encryption','wrong-band','preflight-timeout','permissions','bad-json'):
            run(case)
        run('ssid32',dict(desired,SSID='S'*32))
        run('open',dict(desired,ENCRYPT=0,PWD=''))
        run('wep',dict(desired,ENCRYPT=1,PWD='abcde'))
        run('wpa3',dict(desired,SSIDIndex='9',Standard='11ax',ENCRYPT=16))
        run('utf8',dict(desired,SSID='测试名称',PWD='测试密码测试密码',Enable=0,SSIDAdvertisementEnabled='false'))
        for name,changes in [('index',{'SSIDIndex':'17'}),('index-format',{'SSIDIndex':'01'}),
                             ('ssid',{'SSID':'x'*33}),('key',{'PWD':'short'}),('encryption',{'ENCRYPT':255}),
                             ('truncated-encryption',{'ENCRYPT':4294967300}),('standard',{'Standard':'invalid'}),
                             ('enable',{'Enable':'1'}),('unknown',{'extra':1}),('nul',{'SSID':'bad\0name'})]:
            run('invalid-'+name,dict(desired,**changes))
        missing=dict(desired);del missing['PWD'];run('invalid-missing-key',missing)
        peer.close()
    result={'original_field_comparisons':comparisons,'packet_cases':cases,
            'checks':['wire fields match actual original remote builder except intentional32-byte SSID preservation',
                      'invalid/ambiguous/missing target fails before write',
                      'one write followed by independent readback, including lost ACK and rejection',
                      'readable fields match is separate from credentials/configuration verification',
                      'strict complete private input, no defaults, no secrets printed'],
            'scope':'original local read/remote builder/enum execution with modeled MIB; real native AF_PACKET/veth against modeled slave. No actual slave apply/credential verification.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
