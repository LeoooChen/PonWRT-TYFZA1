#!/usr/bin/env python3
"""Run the real controller over veth in a private network namespace; no optical IO."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import tempfile
import time

HOST, OLT = bytes.fromhex('020304050607'), bytes.fromhex('0a0b0c0d0e0f')


def crc(data):
    value = 0xffffffff
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04c11db7 if value & 0x80000000 else 0)) & 0xffffffff
    return value ^ 0xffffffff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--daemon', required=True, type=Path)
    parser.add_argument('--control', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    # The caller must create this process's private namespace with unshare -n.
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))} == {'lo'}
    subprocess.run(['ip','link','add','ftr-host','type','veth','peer','name','ftr-peer'],check=True)
    for interface, mac in [('ftr-host','02:03:04:05:06:07'),('ftr-peer','0a:0b:0c:0d:0e:0f')]:
        subprocess.run(['ip','link','set','dev',interface,'address',mac,'mtu','1982','up'],check=True)
    peer = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x8888))
    peer.bind(('ftr-peer',0x8888))
    peer.setblocking(False)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        miniolt, ni, store = [root/name for name in ('miniolt','ni','state')]
        for p in (miniolt,ni,store):p.mkdir(mode=0o700)
        miniolt.joinpath('omci_link').write_text('host=02:03:04:05:06:07 olt=0a:0b:0c:0d:0e:0f ethertype=8888 downstream_length=0\n')
        miniolt.joinpath('host_serdes').write_text('mode=9 nway=1 pll=1 error=0 link=1\n')
        ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
        miniolt.joinpath('tx_enable').write_text('1\n')
        os.mkfifo(miniolt/'management_grant')
        grants_fd = os.open(miniolt/'management_grant',os.O_RDWR|os.O_NONBLOCK)
        grants, requests, old_reply = [], [], None
        grant_buffer = b''
        serial = bytes.fromhex('5445535400000007')
        online = True
        epoch = 1

        def tables():
            rows, binary = [], b''
            for id in range(64):
                active = id == 5 or (id == 7 and online)
                sn = bytes.fromhex('5445535400000005') if id == 5 else serial if id == 7 else bytes(8)
                downstream = (1 << 18) if active else 0
                rows.append(f'{id} {downstream:08x} 00000001 0000002a {sn.hex()}\n')
                binary += struct.pack('<III8s',downstream,1,42,sn)
            for name,data in [('onu_state',binary),('onu_table',''.join(rows).encode())]:
                miniolt.joinpath(name+'.new').write_bytes(data)
                os.replace(miniolt/(name+'.new'),miniolt/name)

        def response(onu, transaction, opcode, content, class_id=2, instance=0):
            body = (struct.pack('>HBBHH',transaction,opcode,10,class_id,instance)+content).ljust(40,b'\0')+b'\0\0\0\x28'
            return HOST+OLT+struct.pack('>HH',0x8888,onu)+body+struct.pack('>I',crc(body))

        def handle(frame):
            nonlocal old_reply
            assert frame[:14] == OLT+HOST+b'\x88\x88'
            onu = struct.unpack('>H',frame[14:16])[0]
            body = frame[16:60]
            transaction, opcode = struct.unpack('>HB',body[:3])
            assert 0x8001 <= transaction <= 0xffff and body[3:8] == b'\x0a\0\2\0\0'
            assert body[40:44] == b'\0\0\0\x28'
            requests.append((onu,opcode,transaction,epoch))
            if onu == 5:
                assert opcode == 0x4d
                peer.send(response(onu,transaction,0x2d,b'\0\1',class_id=3))
            else:
                assert onu == 7
                if opcode == 0x4d:
                    if epoch == 2:
                        peer.send(old_reply)  # prior registration's response must be ignored
                    peer.send(response(onu,transaction,0x2d,b'\0\3' if epoch == 1 else b'\0\1'))
                else:
                    assert opcode == 0x4e
                    sequence = struct.unpack('>H',body[8:10])[0]
                    if epoch == 1:
                        c,instance,mask,attributes = [(256,0x8001,0xc000,bytes([1])*18),
                            (256,0x8001,0x2000,serial),(65000,1,0x8000,b'\x42')][sequence]
                    else:
                        assert sequence == 0
                        c,instance,mask,attributes = 262,0x8002,0xc000,bytes([2])*3
                    payload = struct.pack('>HHH',c,instance,mask)+attributes.ljust(26,b'\0')
                    packet = response(onu,transaction,0x2e,payload)
                    if epoch == 1:old_reply = packet
                    peer.send(packet)

        def status():
            try:return json.loads(store.joinpath('status.json').read_text())
            except FileNotFoundError:return {}

        def until(predicate, seconds=5):
            nonlocal grant_buffer
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(('controller exited',process.communicate()[1]))
                ready,_,_ = select.select([peer,grants_fd],[],[],0.02)
                for source in ready:
                    if source == grants_fd:
                        grant_buffer += os.read(grants_fd,4096)
                        while b'\n' in grant_buffer:
                            line,grant_buffer = grant_buffer.split(b'\n',1)
                            grants.append(tuple(map(int,line.split())))
                    else:
                        frame,address = peer.recvfrom(2048)
                        if address[2] != socket.PACKET_OUTGOING:handle(frame)
                if predicate(status()):return
            raise AssertionError(('controller state timeout',status(),grants,requests))

        tables()
        process = subprocess.Popen([str(args.daemon.resolve()),str(miniolt),str(ni),str(store),'ftr-host'],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        try:
            until(lambda s: s.get('onus') and s['onus'][5]['phase']=='failed' and s['onus'][7]['phase']=='inventory')
            first = status()
            assert first['onus'][5]['error'] == -71 and first['onus'][7]['records'] == 3
            assert first['onus'][7]['entities'] == 2 and first['onus'][7]['unparsed_records'] == 1
            assert store.joinpath('onu-7.mib').is_file() and not store.joinpath('onu-5.mib').exists()
            first_dump = [json.loads(x) for x in store.joinpath('onu-7.mib').read_text().splitlines()]
            assert first_dump[0]['serial'] == serial.hex() and first_dump[1]['class'] == 256
            entity = next(x for x in first_dump if x['event']=='mib_entity' and x['class']==256)
            assert entity['instance']==0x8001 and entity['present_mask']==0xe000
            assert entity['attributes'][-1]=={'mask':0x2000,'value':serial.hex()}
            # A malformed frame is visible without taking the healthy ONU down.
            peer.send(HOST+OLT+bytes.fromhex('88880005'))
            until(lambda s:s.get('rx_errors')==1)
            assert status()['state']=='running'
            online = False
            tables()
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='absent' and (7,0) in grants)
            assert not store.joinpath('onu-7.mib').exists()
            assert status()['onus'][7]['entities']==0
            serial = bytes.fromhex('5445535400000008')
            online,epoch = True,2
            tables()
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='inventory' and s['onus'][7]['mib_serial']==serial.hex())
            second_dump = [json.loads(x) for x in store.joinpath('onu-7.mib').read_text().splitlines()]
            assert second_dump[0]['serial']==serial.hex() and second_dump[1]['class']==262
            assert status()['onus'][7]['entities']==1 and status()['onus'][7]['unparsed_records']==0
            assert [x['class'] for x in second_dump if x['event']=='mib_entity']==[262]
            assert len([r for r in requests if r[0]==5])==1
            assert grants.count((7,1))==2 and (5,1) in grants
            process.send_signal(signal.SIGTERM)
            _,errors = process.communicate(timeout=10)
            assert process.returncode == 0,errors
            assert status()['state']=='stopped' and status()['pid']==0
            assert miniolt.joinpath('tx_enable').read_text()=='0\n'
            assert ni.joinpath('fttr_link').read_text().startswith('0 0\n')
            flags=json.loads(subprocess.check_output(['ip','-j','link','show','dev','ftr-host'],text=True))[0]['flags']
            assert 'UP' not in flags
            fd=os.open(miniolt,os.O_RDONLY|os.O_DIRECTORY)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            os.close(fd)
            # Exercise the installed command's read-only Get over the same real
            # packet transport after the controller releases the device lease.
            subprocess.run(['ip','link','set','dev','ftr-host','up'],check=True)
            for result in (0,6):
                query=subprocess.Popen([str(args.control.resolve()),str(miniolt),'get','7','268',
                    '0x8007','0xc000','ftr-host'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    deadline=time.monotonic()+4
                    while True:
                        assert time.monotonic()<deadline and query.poll() is None
                        if not select.select([peer],[],[],0.05)[0]:continue
                        frame,address=peer.recvfrom(2048)
                        if address[2]==socket.PACKET_OUTGOING:continue
                        transaction=struct.unpack('>H',frame[16:18])[0]
                        assert frame[14:16]==b'\0\7' and frame[18:26]==bytes.fromhex('490a010c8007c000')
                        peer.send(response(7,transaction,0x29,bytes([result])+bytes.fromhex('c00001178007'),
                                           class_id=268,instance=0x8007))
                        break
                    output,error=query.communicate(timeout=3)
                    row=json.loads(output)
                    assert query.returncode==(1 if result else 0),(output,error)
                    assert row['event']=='me_reply' and row['result']==result
                    assert row['error']==(-121 if result else 0) and bytes.fromhex(row['body'])[8]==result
                finally:
                    if query.poll() is None:
                        query.terminate();query.communicate(timeout=3)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
            os.close(grants_fd)
    peer.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'transport':'real AF_PACKET over veth in private network namespace',
        'modeled_hardware':'sysfs state and grant attributes only','independent_onu_failure':True,
        'split_entity_records_merged':True,'unknown_layout_explicit':True,'old_model_revoked_on_replacement':True,
        'real_cli_get_and_onu_rejection':True,
        'malformed_frame_reported_without_master_exit':True,'offline_releases_grant_and_inventory':True,
        'replaced_onu_reinventoried':True,'old_transaction_reply_ignored':True,'sigterm_closes_hardware_and_netdev':True,
        'optical_hardware_verified':False},indent=2)+'\n')
    print('Controller: packet exchange, model replacement, SIGTERM and real CLI Get/rejection passed')


if __name__ == '__main__':main()
