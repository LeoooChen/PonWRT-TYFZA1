#!/usr/bin/env python3
"""Compare syncserv frames with original ARM64 code and run the read-only probe."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from trace_stock_fttr_sync import SyncOriginal


class Message(C.Structure):
    _fields_ = [('source', C.c_ubyte*6), ('destination', C.c_ubyte*6),
                ('slot', C.c_ubyte), ('type', C.c_ubyte),
                ('payload', C.c_void_p), ('length', C.c_size_t)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link'], text=True))} == {'lo'}
    stock = SyncOriginal(args.controller)
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        src = ROOT / 'port/packages/tyfza1-fttr/src'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',
                        str(src/'sync-wire.c'),'-o',str(directory/'wire.so')], check=True)
        binary = directory/'fttr-sync'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                        *[str(src/n) for n in ('fttr-sync.c','sync-wire.c','omci-socket.c','omci-wire.c')],
                        '-ljson-c','-o',str(binary)], check=True)
        native = C.CDLL(str(directory/'wire.so'))
        native.fttr_sync_pack.argtypes = [C.c_void_p,C.c_void_p,C.c_ushort,C.c_ubyte,C.c_ubyte,
                                          C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t]
        native.fttr_sync_unpack.argtypes = [C.c_void_p,C.c_size_t,C.c_ushort,C.POINTER(Message)]
        count = 0
        for code in (0,2,4,6,16,20):
            stock.entries = [stock.entry(code),stock.entry(code)]
            value = stock.wifi()
            for slot in (0,5,15):
                for request in (False,True):
                    expected = stock.encode(value,slot,request)
                    payload = expected[20:]
                    frame = C.create_string_buffer(1996)
                    n = native.fttr_sync_pack(stock.mac,stock.peer,0x88aa,slot,6 if request else 7,
                                               payload,len(payload),frame,len(frame))
                    assert frame.raw[:n] == expected
                    message = Message()
                    assert native.fttr_sync_unpack(expected,len(expected),0x88aa,C.byref(message)) == 0
                    assert message.slot == slot and message.type == (6 if request else 7)
                    assert C.string_at(message.payload,message.length) == payload
                    count += 1
        frame = C.create_string_buffer(1996)
        assert native.fttr_sync_pack(stock.mac,stock.peer,0x88aa,16,6,b'{}',2,frame,1996) == -errno.EINVAL
        assert native.fttr_sync_pack(stock.mac,stock.peer,0x88aa,5,6,b'x'*1977,1977,frame,1996) == -errno.EMSGSIZE
        maximum = {'padding':'x'*1956}  # Exact native 1976-byte payload budget.
        text = json.dumps(maximum,separators=(',',':')).encode()
        text += b' '*(1976-len(text))
        assert native.fttr_sync_pack(stock.mac,stock.peer,0x88aa,5,6,text,len(text),frame,1996) == 1996
        assert native.fttr_sync_unpack(frame,1996,0x88aa,C.byref(Message())) == 0
        valid = stock.encode({'RPCMethod':'GetWifi','MAC':stock.mac.hex()})
        for invalid,error in ((valid[:19],-errno.EMSGSIZE), (valid[:-1],-errno.EMSGSIZE),
                              (valid[:15]+b'\x20'+valid[16:],-errno.EOPNOTSUPP),
                              (valid[:16]+b'\xff'*4+valid[20:],-errno.EMSGSIZE),
                              (valid+b'extra',-errno.EBADMSG)):
            assert native.fttr_sync_unpack(invalid,len(invalid),0x88aa,C.byref(Message())) == error
        short=stock.encode({})
        assert native.fttr_sync_unpack(short.ljust(60,b'\0'),60,0x88aa,C.byref(Message())) == 0

        subprocess.run(['ip','link','add','sync-host','type','veth','peer','name','sync-peer'],check=True)
        for interface,mac in (('sync-host',stock.mac),('sync-peer',stock.peer)):
            subprocess.run(['ip','link','set',interface,'address',mac.hex(':'),'mtu','1982','up'],check=True)
        peer = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa))
        peer.bind(('sync-peer',0x88aa));peer.setblocking(False)
        cases = []

        def run(case):
            command = [str(binary),'sync-host',stock.peer.hex(':'),'5','0x88aa']
            process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline = time.monotonic()+5
            requests = 0
            try:
                while process.poll() is None:
                    assert time.monotonic()<deadline,case
                    ready,_,_=select.select([peer],[],[],0.02)
                    if not ready:continue
                    received,address=peer.recvfrom(4096)
                    if address[2]==socket.PACKET_OUTGOING:continue
                    requests += 1
                    assert received == stock.encode(stock.wifi(get=True),5,True)
                    if case in ('timeout','lease'):continue
                    value={'RPCMethod':'GetWifi','MAC':stock.peer.hex(),
                           'SSID':[{'Name':'do-not-print-name','WPAKey':'do-not-print-key'}]}
                    if case=='rejected':value={'Result':0,'FailReason':'do-not-print-key'}
                    if case=='ack':value={'Result':1}
                    response=stock.encode(value,5,False)
                    response=response[6:12]+response[:6]+response[12:]
                    if case=='unrelated':
                        peer.send(response[:6]+bytes.fromhex('020000000099')+response[12:])
                        peer.send(response[:14]+b'\x47'+response[15:])
                        other=stock.encode(dict(value,RPCMethod='GetTimeSetting',Result=0),5,False)
                        peer.send(other[6:12]+other[:6]+other[12:])
                    if case=='fragment':response=response[:15]+b'\x20'+response[16:]
                    if case=='truncated':response=response[:-1]
                    if case=='bad-json':response=response[:20]+b'!'*len(response[20:])
                    if case=='bad-utf8':response=response[:21]+b'\xff'+response[22:]
                    if case=='nul-json':response=response[:21]+b'\0'+response[22:]
                    peer.send(response)
                output,error=process.communicate(timeout=1)
            finally:
                if process.poll() is None:process.kill();process.communicate()
            assert 'do-not-print' not in output+error
            if case in ('normal','unrelated','ack'):
                assert process.returncode==0,(case,error)
                result=json.loads(output)
                assert not result['configuration_verified']
                if case=='ack':assert result['event']=='sync_ack' and 'ssid_entries' not in result
                else:assert result['ssid_entries']==1
            else:
                assert process.returncode==1 and not output,(case,error)
                if case=='rejected':assert str(-errno.EREMOTEIO) in error
                if case=='timeout':assert str(-errno.ETIMEDOUT) in error
            assert requests == (0 if case=='lease' else 1)
            cases.append(case)

        for case in ('normal','ack','unrelated','fragment','truncated','bad-json','bad-utf8','nul-json','rejected','timeout'):
            run(case)
        lease=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
        try:
            lease.bind('\0tyfza1-fttr-sync-'+str(socket.if_nametoindex('sync-host')))
            run('lease')
        finally:lease.close()
        peer.close()
    result={'controller_sha256':hashlib.sha256(args.controller.read_bytes()).hexdigest(),
            'original_frame_comparisons':count,'packet_cases':cases,
            'checks':['native bytes match original JSON Ethernet encoder at slots 0/5/15, requests and responses',
                      'invalid slot, oversize/truncated length, trailing data and fragments are rejected',
                      'short Ethernet padding and maximum native payload are handled',
                      'real AF_PACKET GetWifi probe sends one read-only request and redacts returned values'],
            'scope':'original encoder/producer plus native AF_PACKET/veth. JSON-C serialization and peer responses modeled; no real subordinate acceptance.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
