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
from trace_stock_fttr_discovery import DiscoveryOriginal
from trace_stock_fttr_fragments import FragmentOriginal, fragments
from trace_stock_fttr_settings import SettingsOriginal

GROUP = bytes.fromhex('0110ec000002')
MASTER_GROUP = bytes.fromhex('0110ec000001')


class Peer(C.Structure):
    _fields_ = [('mac',C.c_ubyte*6),('missed',C.c_ubyte),('online',C.c_bool)]


class Message(C.Structure):
    _fields_ = [('source', C.c_ubyte*6), ('destination', C.c_ubyte*6),
                ('slot', C.c_ubyte), ('type', C.c_ubyte),
                ('payload', C.c_void_p), ('length', C.c_size_t)]


class Receiver(C.Structure):
    _fields_ = [('source',C.c_ubyte*6),('type',C.c_ubyte),('sequence',C.c_ubyte),
                ('count',C.c_ubyte),('mask',C.c_ubyte),('ethertype',C.c_ushort),
                ('deadline',C.c_int64),('lengths',C.c_size_t*8),
                ('parts',(C.c_ubyte*1980)*8),('body',C.c_ubyte*(1980*8))]


def check_fragments(native, binary, stock):
    native.fttr_sync_receive.argtypes = [C.POINTER(Receiver),C.c_void_p,C.c_size_t,
                                         C.c_ushort,C.c_int64,C.POINTER(Message)]
    receiver, message = Receiver(), Message()
    base = stock.peer + stock.mac + b'\x88\xaa\x57'

    def feed(part, flags, now=0, source=None):
        frame = base + bytes([flags]) + part
        if source is not None:frame = frame[:6] + source + frame[12:]
        return native.fttr_sync_receive(C.byref(receiver),frame,len(frame),0x88aa,now,C.byref(message))

    body = (3900).to_bytes(4,'big') + b'x'*3900
    for count in range(2,9):
        for sequence in range(4):
            for reverse in (False,True):
                receiver = Receiver()
                original = FragmentOriginal(binary)
                parts = fragments(body,count,sequence)
                order = list(range(count))[::(-1 if reverse else 1)]
                for index in order:
                    original.receive(*parts[index])
                    ret = feed(*parts[index])
                    assert ret == (0 if index==order[-1] else -errno.EAGAIN)
                    if ret:assert not message.payload and not message.length
                assert C.string_at(message.payload,message.length) == body[4:]
                assert bytes(original.oracle.uc.mem_read(0x42bdc0,len(body))) == body
    parts = fragments(body,3,2)
    receiver = Receiver()
    assert feed(*parts[0]) == -errno.EAGAIN
    before = bytes(receiver)
    assert feed(*parts[0],now=100) == -errno.EAGAIN and bytes(receiver)==before
    assert feed(*parts[1],source=bytes.fromhex('020000000099')) == -errno.EAGAIN
    assert bytes(receiver)==before
    assert feed(parts[0][0]+b'!',parts[0][1]) == -errno.EBADMSG and not receiver.count
    assert feed(*parts[0]) == -errno.EAGAIN
    assert feed(*parts[1],now=3000) == -errno.ETIMEDOUT and not receiver.count
    assert feed(*parts[0]) == -errno.EAGAIN
    assert feed(parts[1][0],parts[1][1]^0x20) == -errno.EBADMSG and not receiver.count
    assert feed(b'x',0x3c) == -errno.EBADMSG  # index7/count2
    # New sequence retires old slices; an old missing slice cannot complete it.
    assert feed(*parts[0]) == -errno.EAGAIN
    changed=fragments(body,3,3)
    assert feed(*changed[1]) == -errno.EAGAIN and receiver.mask==2
    assert feed(*changed[0]) == -errno.EAGAIN
    assert feed(*changed[2]) == 0
    assert C.string_at(message.payload,message.length)==body[4:]
    # Single-frame sequence values, plus retirement of partial state.
    for sequence in range(4):
        receiver=Receiver()
        assert feed(*parts[0]) == -errno.EAGAIN
        assert feed(b'\0\0\0\x02{}',sequence)==0 and not receiver.count
        assert C.string_at(message.payload,message.length)==b'{}'
    # Maximum8 fragments, shorter final frame padding, and excessive length.
    receiver=Receiver()
    maximum=(8*1980-4).to_bytes(4,'big')+b'x'*(8*1980-4)
    for index,part in enumerate(fragments(maximum,8)):
        assert feed(*part)==(0 if index==7 else -errno.EAGAIN)
    assert message.length==8*1980-4
    for total,expected in ((52,0),(91,-errno.EMSGSIZE),(10,-errno.EBADMSG)):
        receiver=Receiver()
        assert feed(total.to_bytes(4,'big')+b'x'*46,0x20)==-errno.EAGAIN
        assert feed((b'y'*6).ljust(44,b'\0'),0x24)==expected
        if not expected:assert C.string_at(message.payload,message.length)==b'x'*46+b'y'*6
    receiver=Receiver()
    assert feed(b'x'*1981,0x20)==-errno.EMSGSIZE
    assert feed(b'x',0x20)==-errno.EAGAIN
    assert feed(b'y',0x24)==-errno.EBADMSG
    return 56


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
                        str(src/'sync-wire.c'),str(src/'sync-peer.c'),'-o',str(directory/'wire.so')], check=True)
        binary = directory/'fttr-sync'
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',
                        *[str(src/n) for n in ('fttr-sync.c','sync-wire.c','sync-peer.c','sync-wifi.c','sync-json.c','sync-register.c','omci-socket.c','omci-wire.c')],
                        '-ljson-c','-o',str(binary)], check=True)
        native = C.CDLL(str(directory/'wire.so'))
        native.fttr_sync_pack.argtypes = [C.c_void_p,C.c_void_p,C.c_ushort,C.c_ubyte,C.c_ubyte,
                                          C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t]
        native.fttr_sync_unpack.argtypes = [C.c_void_p,C.c_size_t,C.c_ushort,C.POINTER(Message)]
        native.fttr_sync_heartbeat.argtypes = [C.c_void_p,C.c_ushort,C.c_ubyte,C.c_void_p]
        native.fttr_sync_peer_receive.argtypes = [C.POINTER(Peer),C.c_void_p,C.c_ushort,C.c_void_p,C.c_size_t]
        native.fttr_sync_peer_age.argtypes = [C.POINTER(Peer)]
        original = DiscoveryOriginal(args.controller)
        peers = (Peer*16)()
        heartbeat = C.create_string_buffer(16)

        def compare_peers():
            assert [{'mac':bytes(p.mac).hex(':'),'slot':i,'missed':p.missed,'online':p.online}
                    for i,p in enumerate(peers)] == original.rows()

        def observe(mac, flags=0):
            original.receive(mac,flags)
            frame=MASTER_GROUP+mac+b'\x88\xaa\x01'+bytes([flags])
            ret=native.fttr_sync_peer_receive(peers,stock.mac,0x88aa,frame,len(frame))
            compare_peers()
            return ret

        for sequence in range(10):
            assert native.fttr_sync_heartbeat(stock.mac,0x88aa,sequence,heartbeat)==16
            assert heartbeat.raw==original.heartbeat()
            native.fttr_sync_peer_age(peers)
            compare_peers()
        macs=[bytes([2,0,0,0,0,i+1]) for i in range(17)]
        assert observe(macs[0],0x80)==-errno.EAGAIN
        for i,mac in enumerate(macs[:16]):assert observe(mac)==i
        assert observe(macs[16])==-errno.ENOSPC
        for _ in range(2):
            original.heartbeat();native.fttr_sync_peer_age(peers);compare_peers()
        assert observe(macs[5])==5
        original.heartbeat();native.fttr_sync_peer_age(peers);compare_peers()
        assert observe(macs[16])==0
        assert observe(macs[12])==12
        for header in (0,5,15):assert original.ingress_slot(macs[12],header)==12
        for slot in (0,5,12):
            expected=original.encode_bound(stock.wifi(get=True),slot)
            buffer=C.create_string_buffer(1996)
            n=native.fttr_sync_pack(stock.mac,GROUP,0x88aa,slot,6,expected[20:],len(expected)-20,buffer,1996)
            assert buffer.raw[:n]==expected
        # Native ingress rejects impossible identities and wrong envelopes.
        for mac in (bytes(6),stock.mac,bytes.fromhex('0110ec000001')):
            f=MASTER_GROUP+mac+b'\x88\xaa\x01\0'
            assert native.fttr_sync_peer_receive(peers,stock.mac,0x88aa,f,len(f))==-errno.EINVAL
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
        fragment_comparisons = check_fragments(native,args.controller,stock)

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
                    expected = stock.encode(stock.wifi(get=True),5,True)
                    assert received == GROUP+expected[6:]
                    if case in ('timeout','lease'):continue
                    value={'RPCMethod':'GetWifi','MAC':stock.peer.hex(),
                           'SSID':[{'Name':'do-not-print-name','WPAKey':'do-not-print-key'}]}
                    if case=='rejected':value={'Result':0,'FailReason':'do-not-print-key'}
                    if case=='ack':value={'Result':1}
                    response=stock.encode(value,5,False)
                    response=response[6:12]+response[:6]+response[12:]
                    if case=='unrelated':
                        peer.send(response[:6]+bytes.fromhex('020000000099')+response[12:])
                        other=stock.encode(dict(value,RPCMethod='GetTimeSetting',Result=0),5,False)
                        peer.send(other[6:12]+other[:6]+other[12:])
                    if case=='different-slot':response=MASTER_GROUP+response[6:14]+b'\xf7'+response[15:]
                    if case=='fragment':response=response[:15]+b'\x20'+response[16:]
                    if case.startswith('fragments-'):
                        value['Extra']='redacted-large-value'*200
                        text=json.dumps(value,separators=(',',':')).encode()
                        # Model a slave fragment transmitter. The retained
                        # controller's encoder does not split oversized JSON.
                        response=response[:16]+len(text).to_bytes(4,'big')+text
                        parts=fragments(response[16:],3,3)
                        frames=[response[:15]+bytes([flags])+body for body,flags in parts]
                        if case=='fragments-missing':
                            peer.send(frames[0]);peer.send(frames[2]);continue
                        if case=='fragments-conflict':
                            peer.send(frames[0]);peer.send(frames[0][:-1]+b'!');continue
                        if case=='fragments-count':
                            peer.send(frames[0]);peer.send(frames[1][:15]+b'\x67'+frames[1][16:]);continue
                        if case=='fragments-source':
                            peer.send(frames[0])
                            peer.send(frames[1][:6]+bytes.fromhex('020000000099')+frames[1][12:])
                            peer.send(frames[2])
                        if case=='fragments-duplicate':peer.send(frames[0])
                        for f in reversed(frames):peer.send(f)
                        continue
                    if case=='truncated':response=response[:-1]
                    if case=='bad-json':response=response[:20]+b'!'*len(response[20:])
                    if case=='bad-utf8':response=response[:21]+b'\xff'+response[22:]
                    if case=='nul-json':response=response[:21]+b'\0'+response[22:]
                    peer.send(response)
                output,error=process.communicate(timeout=1)
            finally:
                if process.poll() is None:process.kill();process.communicate()
            assert 'do-not-print' not in output+error
            if case in ('normal','unrelated','ack','different-slot','fragments-order','fragments-duplicate','fragments-source'):
                assert process.returncode==0,(case,error)
                result=json.loads(output)
                assert not result['configuration_verified']
                assert result['slot']==5
                if case=='ack':assert result['event']=='sync_ack' and 'ssid_entries' not in result
                else:assert result['ssid_entries']==1
            else:
                assert process.returncode==1 and not output,(case,error)
                if case=='rejected':assert str(-errno.EREMOTEIO) in error
                if case in ('timeout','fragment','fragments-missing'):assert str(-errno.ETIMEDOUT) in error
                if case in ('fragments-conflict','fragments-count'):assert str(-errno.EBADMSG) in error
            assert requests == (0 if case=='lease' else 1)
            cases.append(case)

        for case in ('normal','ack','unrelated','different-slot','fragment','truncated','bad-json','bad-utf8','nul-json','rejected','timeout',
                     'fragments-order','fragments-duplicate','fragments-source','fragments-missing','fragments-conflict','fragments-count'):
            run(case)
        lease=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
        try:
            lease.bind('\0tyfza1-fttr-sync-'+str(socket.if_nametoindex('sync-host')))
            run('lease')
        finally:lease.close()
        # Discovery operates under the same lease, and auto keeps that lease
        # while resolving a target MAC and sending its first JSON request.
        def discovery_run(mode):
            command=[str(binary),'sync-host','discover','0x88aa'] if mode=='list' else \
                [str(binary),'sync-host',stock.peer.hex(':'),'auto','0x88aa']
            process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+9
            beats=queries=0
            try:
                while process.poll() is None:
                    assert time.monotonic()<deadline,mode
                    ready,_,_=select.select([peer],[],[],0.02)
                    if not ready:continue
                    frame,address=peer.recvfrom(4096)
                    if address[2]==socket.PACKET_OUTGOING:continue
                    assert frame[:6]==GROUP
                    if frame[14]&15==1:
                        assert frame[14:]==bytes([1,0x80|(beats&7)])
                        beats+=1
                        if mode=='timeout':continue
                        # Group requests, zero/multicast/self source and other
                        # destination must not allocate slots ahead of real peers.
                        peer.send(stock.mac+stock.peer+b'\x88\xaa\x01\x80')
                        for invalid in (bytes(6),stock.mac,GROUP):
                            peer.send(MASTER_GROUP+invalid+b'\x88\xaa\x01\0')
                        peer.send(bytes.fromhex('020000000099')+stock.peer+b'\x88\xaa\x01\0')
                        first=bytes.fromhex('020000000033')
                        if beats==1:peer.send(MASTER_GROUP+first+b'\x88\xaa\x01\0')
                        peer.send(MASTER_GROUP+stock.peer+b'\x88\xaa\xf1\x03')
                    else:
                        queries+=1
                        assert mode=='auto' and frame[14]==0x16
                        expected=stock.encode(stock.wifi(get=True),1,True)
                        assert frame==GROUP+expected[6:]
                        reply=stock.encode({'Result':1},15,False)
                        peer.send(MASTER_GROUP+stock.peer+reply[12:])
                output,error=process.communicate(timeout=1)
            finally:
                if process.poll() is None:process.kill();process.communicate()
            if mode=='timeout':
                assert process.returncode==1 and str(-errno.ETIMEDOUT) in error and not output
                assert queries==0 and beats==4
            else:
                assert process.returncode==0,error
                result=json.loads(output)
                if mode=='auto':assert result['slot']==1 and result['event']=='sync_ack' and queries==1
                else:
                    assert beats==4 and queries==0 and not result['identity_authenticated']
                    assert result['peers']==[{'mac':stock.peer.hex(':'),'slot':1,'missed':0}]
            cases.append('discovery-'+mode)

        for mode in ('auto','list','timeout'):discovery_run(mode)

        settings_stock = SettingsOriginal(args.controller)
        settings_query = settings_stock.wifi_query()

        def settings_run(case):
            command = [str(binary),'sync-host',stock.peer.hex(':'),
                       'auto' if case=='auto' else '5','0x88aa','wifi-settings']
            process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline = time.monotonic()+5
            requests = 0
            reports_at = None
            response_value = {'RPCMethod':'SetAllSetting','Flag':1,'STATUS':0,
                              'content':{'content':{'List':[{'Name':'do-not-print-name','Key':'do-not-print-key'}]}}}

            def send(value,kind=7,source=stock.peer):
                encoded = stock.encode(value,15,kind==6)
                peer.send(MASTER_GROUP+source+encoded[12:])

            try:
                while process.poll() is None:
                    assert time.monotonic()<deadline,case
                    if reports_at is not None and time.monotonic() >= reports_at:
                        send(response_value,6)
                        reports_at=None
                    ready,_,_=select.select([peer],[],[],0.02)
                    if not ready:continue
                    received,address=peer.recvfrom(4096)
                    if address[2]==socket.PACKET_OUTGOING:continue
                    if received[14]&15==1:
                        assert case=='auto'
                        peer.send(MASTER_GROUP+stock.peer+b'\x88\xaa\x01\0')
                        continue
                    requests+=1
                    expected=stock.encode(settings_query,0 if case=='auto' else 5)
                    assert received==GROUP+expected[6:]
                    if case=='ack-only':send({'Result':1});continue
                    if case=='nack':send({'Result':0,'FailReason':'do-not-print-key'});continue
                    if case=='unrelated':
                        send(dict(response_value,Flag=2))
                        send(dict(response_value,RPCMethod='GetWifi',Result=0))
                        send(response_value,source=bytes.fromhex('020000000099'))
                    if case in ('ack-report','auto'):
                        send({'Result':1})
                        reports_at=time.monotonic()+0.15
                        continue
                    if case=='progress':
                        send(dict(response_value,STATUS=1))
                        reports_at=time.monotonic()+0.15
                        continue
                    if case=='empty':response_value['content']['content']['List']=[]
                    if case=='status-incomplete':response_value['STATUS']=1
                    if case=='status-only':response_value={'RPCMethod':'SetAllSetting','Flag':1,'STATUS':1}
                    if case=='no-status':del response_value['STATUS']
                    if case=='no-flag':del response_value['Flag']
                    if case=='bad-list':response_value['content']['content']['List']=['do-not-print-key']
                    if case=='unwrapped':response_value['content']=response_value['content']['content']
                    if case=='large-result':response_value={'Result':4294967297}
                    if case in ('fragment-report','fragment-response'):
                        response_value['content']['Extra']='do-not-print-large'*200
                        body=json.dumps(response_value,separators=(',',':')).encode()
                        body=len(body).to_bytes(4,'big')+body
                        kind=6 if case=='fragment-report' else 7
                        frames=[MASTER_GROUP+stock.peer+b'\x88\xaa'+bytes([0xf0|kind,flags])+part
                                for part,flags in fragments(body,3,1)]
                        peer.send(frames[2])
                        send({'Result':1})  # Response ACK amid request-type fragments.
                        peer.send(frames[0]);peer.send(frames[1])
                        continue
                    send(response_value,6 if case=='request-type' else 7)
                output,error=process.communicate(timeout=1)
            finally:
                if process.poll() is None:process.kill();process.communicate()
            assert requests==1 and 'do-not-print' not in output+error,(case,error)
            errors={'ack-only':errno.ETIMEDOUT,'nack':errno.EREMOTEIO,'large-result':errno.EREMOTEIO,
                    'status-incomplete':errno.ETIMEDOUT,'status-only':errno.ETIMEDOUT,'no-status':errno.EBADMSG,'no-flag':errno.EBADMSG,
                    'bad-list':errno.EBADMSG,'unwrapped':errno.EBADMSG}
            if case in errors:
                assert process.returncode==1 and not output and str(-errors[case]) in error,(case,error,output)
            else:
                assert process.returncode==0,(case,error)
                result=json.loads(output)
                assert result['event']=='sync_wifi_settings' and result['report_complete']
                assert not result['configuration_verified'] and result['values_redacted']
                assert result['ack_seen']==(case in ('ack-report','auto','fragment-report','fragment-response'))
                assert result['slot']==(0 if case=='auto' else 5)
                assert result['settings_entries']==(0 if case=='empty' else 1)
                assert reports_at is None  # Never exit on ACK before the delayed report.
            cases.append('settings-'+case)

        for case in ('direct','request-type','ack-report','auto','unrelated','empty','fragment-report','fragment-response','progress',
                     'ack-only','nack','status-incomplete','status-only','no-status','no-flag','bad-list','large-result','unwrapped'):
            settings_run(case)
        peer.close()
    result={'controller_sha256':hashlib.sha256(args.controller.read_bytes()).hexdigest(),
            'original_frame_comparisons':count,'packet_cases':cases,
            'fragment_concatenation_comparisons':fragment_comparisons,
            'settings_checks':['Wi-Fi act:get request matches actual original producer including target DMAC',
                               'positive ACK waits for Flag1 content.content.List and STATUS0; report alone is accepted',
                               'types6/7, interleaved ACK/fragments, method/flag/source correlation, strict errors and redaction'],
            'checks':['native bytes match original JSON Ethernet encoder at slots 0/5/15, requests and responses',
                      'invalid slot, oversize/truncated length and trailing data are rejected',
                      'bounded fragments match original concatenation; final original dispatch bug is corrected',
                      'fragment order, duplicates/conflicts, ownership, deadline, sequence, count and padding checked',
                      'short Ethernet padding and maximum native payload are handled',
                      'real AF_PACKET GetWifi probe sends one read-only request and redacts returned values'],
            'discovery_checks':['heartbeat and 16-slot lifecycle match original execution',
                                'complete original JSON route uses slave multicast group',
                                'original ingress maps source MAC independently of header slot',
                                'native discover and auto use group membership, valid source binding and bounded waits'],
            'scope':'original encoder/producer plus native AF_PACKET/veth. JSON-C serialization and peer responses modeled; no real subordinate acceptance.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
