#!/usr/bin/env python3
"""Interleave real controller service jobs and replace ONUs in a private network namespace."""
import argparse
import errno
import hashlib
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

from test_fttr_association import Original
from test_fttr_controller import HOST, OLT
from test_fttr_service import SYSFS_WRITE_SHIM
from test_fttr_vlan import Slave


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--master', type=Path, required=True)
    p.add_argument('--slave', type=Path, required=True)
    p.add_argument('--daemon', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))} == {'lo'}
    stock = Original(args.master)
    o, ptr = stock.oracle, stock.data
    widths = {}
    for c in (11,45,47,84,171,262,266,268,272,277,329):
        assert o.call('plr_gponomci_medetail_get',c,ptr,ptr+8)==1
        table = struct.unpack('<Q',o.uc.mem_read(ptr+8,8))[0]
        widths[c] = {struct.unpack('<H',o.uc.mem_read(table+i*40+8,2))[0]:o.uc.mem_read(table+i*40+12,1)[0]
                     for i in range(o.uc.mem_read(ptr,1)[0])}

    class Node:
        def __init__(self,id,serial,uni,veip,hold=False,reject=False):
            self.id,self.serial,self.uni,self.veip = id,bytes.fromhex(serial),uni,veip
            self.tcont,self.queue = 0x8700+uni,0x8a00+uni
            self.slave,self.hold,self.reject = Slave(args.slave),hold,reject
            self.pool = {(262,self.tcont):{0x8000:b'\0\xff'},
                (277,self.queue):{0x0400:struct.pack('>I',self.tcont<<16|3)},
                (329 if veip else 11,uni):{0x8000:b'\1'} if veip else {0x8000:b'\0',0x0800:b'\1'}}
            if veip: self.pool[11,1] = {0x8000:b'\0',0x0800:b'\1'}
            self.table,self.snapshot,self.operations = {},[],[]
            self.held = None

        def records(self):
            rows = []
            for (c,id),attrs in sorted(self.pool.items()):
                mask,content = 0,b''
                for bit,value in sorted(attrs.items(),reverse=True):
                    if len(content)+len(value)>26:
                        rows.append((c,id,mask,content));mask,content = 0,b''
                    mask |= bit;content += value
                rows.append((c,id,mask,content))
            return rows

        def fields(self,c,mask,data):
            pos,attrs = 0,{}
            for bit,width in widths[c].items():
                if mask&bit: attrs[bit] = data[pos:pos+width];pos += width
            assert pos == len(data)
            return attrs

        def reply(self,body):
            tci,op,dev,c,id = struct.unpack('>HBBHH',body[:8])
            assert dev==10
            mask = struct.unpack('>H',body[8:10])[0]
            self.operations.append((tci,op,c,id))
            status,content,wire = 0,b'',None
            if op==0x4d:
                self.snapshot=self.records();content=struct.pack('>H',len(self.snapshot))
            elif op==0x4e:
                a,b,d,v=self.snapshot[mask];content=struct.pack('>HHH',a,b,d)+v.ljust(26,b'\0')
            elif op==0x44:
                assert (c,id) not in self.pool
                if c==266 and self.reject: status=6
                else:
                    if c==47: create_mask=0xfc00 if body[11] in (5,11) else 0xfe00
                    elif c==84: create_mask=0xe000 if self.veip and self.pool[47,id][0x2000]==b'\5' else 0x4000
                    else: create_mask={272:0x8000,45:0xff80,268:0xfac0,266:0xf200,171:0x8200}[c]
                    n=sum(w for bit,w in widths[c].items() if create_mask&bit)
                    self.pool[c,id]=self.fields(c,create_mask,body[8:8+n])
            elif op==0x46:
                if not (self.reject and c==268): self.pool.pop((c,id),None)
            elif op==0x48:
                if c==171 and mask==0x0400:
                    row=body[10:26]
                    if row[8:]==b'\xff'*8: self.table.pop(row[:8],None)
                    else: self.table[row[:8]]=row
                    wire=self.slave.reply(body,b''.join(self.table.values()),8)
                else:
                    n=sum(w for bit,w in widths[c].items() if mask&bit)
                    self.pool[c,id].update(self.fields(c,mask,body[10:10+n]))
            else:
                assert op in (0x49,0x5a)
                if (c,id) not in self.pool: status=5
                elif c==171 and mask in (0x4000,0x0400): wire=self.slave.reply(body,b''.join(self.table.values()),8)
                else: content=struct.pack('>H',mask)+b''.join(self.pool[c,id][bit] for bit in sorted(widths[c],reverse=True) if mask&bit)
            if wire is None:
                wire=stock.frame(tci,(op&0x1f)|0x20,content if op in (0x4d,0x4e) else bytes([status])+content,
                                 onu=self.id,class_id=c,instance=id)[16:]
            if (self.hold and op==0x48 and c==262) or (self.reject and op==0x46 and c==268):
                self.held=wire;return None
            return wire

    nodes={7:Node(7,'5445535400000007',0x107,False,hold=True),
           9:Node(9,'5445535400000009',0x203,True)}
    subprocess.run(['ip','link','add','fap-host','type','veth','peer','name','fap-peer'],check=True)
    for interface,mac in [('fap-host','02:03:04:05:06:07'),('fap-peer','0a:0b:0c:0d:0e:0f')]:
        subprocess.run(['ip','link','set','dev',interface,'address',mac,'mtu','1982','up'],check=True)
    peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x8888))
    peer.bind(('fap-peer',0x8888));peer.setblocking(False)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);device,ni,store=[root/n for n in ('miniolt','ni','state')]
        for path in (device,ni,store):path.mkdir(mode=0o700)
        device.joinpath('omci_link').write_text('host=02:03:04:05:06:07 olt=0a:0b:0c:0d:0e:0f ethertype=8888 downstream_length=0\n')
        device.joinpath('host_serdes').write_text('mode=9 nway=1 pll=1 error=0 link=1\n')
        ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
        device.joinpath('tx_enable').write_text('1\n')
        device.joinpath('management_grant').write_text('')
        device.joinpath('tcont_state').write_bytes(bytes(4096))
        os.mkfifo(device/'tcont');grant_fd=os.open(device/'tcont',os.O_RDWR|os.O_NONBLOCK)
        shim=root/'sysfs.c';shim.write_text(SYSFS_WRITE_SHIM)
        subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-o',str(root/'sysfs.so'),str(shim),'-ldl'],check=True)
        policy=root/'services'
        policy.write_text('5445535400000007 0 0 1 3 none 50 100 200\n* 2 0 1 3 200 50 100 200\n')
        grants,grant_buffer=[],b''
        def hardware():
            data=b''.join(struct.pack('<III8s',1<<18 if id in nodes else 0,1,42,
                nodes[id].serial if id in nodes else bytes(8)) for id in range(64))
            device.joinpath('onu_state.new').write_bytes(data);os.replace(device/'onu_state.new',device/'onu_state')
            device.joinpath('onu_table').write_text(''.join(f'{id} {(1<<18 if id in nodes else 0):08x} 00000001 0000002a '+
                (nodes[id].serial if id in nodes else bytes(8)).hex()+'\n' for id in range(64)))
        def status():
            try:return json.loads(store.joinpath('status.json').read_text())
            except FileNotFoundError:return {}
        def journal(id,suffix='service'):
            return [json.loads(l) for l in store.joinpath(f'onu-{id}.{suffix}').read_text().splitlines()]
        def until(predicate,seconds=8):
            nonlocal grant_buffer
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                if process.poll() is not None:raise AssertionError(process.communicate())
                ready,_,_=select.select([peer,grant_fd],[],[],0.01)
                for source in ready:
                    if source==grant_fd:
                        grant_buffer+=os.read(grant_fd,4096)
                        while b'\n' in grant_buffer:
                            line,grant_buffer=grant_buffer.split(b'\n',1);grants.append(tuple(map(int,line.split())))
                    else:
                        frame,address=peer.recvfrom(2048)
                        if address[2]==socket.PACKET_OUTGOING:continue
                        assert frame[:14]==OLT+HOST+b'\x88\x88'
                        id=struct.unpack('>H',frame[14:16])[0]
                        wire=nodes[id].reply(frame[16:60])
                        if wire:peer.send(HOST+OLT+struct.pack('>HH',0x8888,id)+wire)
                current=status()
                if predicate(current):return current
            raise AssertionError(('state timeout',status(),grants))
        hardware()
        def launch(with_policy=True):
            subprocess.run(['ip','link','set','dev','fap-host','up'],check=True)
            ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
            device.joinpath('tx_enable').write_text('1\n')
            return subprocess.Popen([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host']+
                (['--services',str(policy)] if with_policy else []),
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,
                env=dict(os.environ,LD_PRELOAD=str(root/'sysfs.so'),FTTR_SERVICE_CASE='normal'))
        process=launch()
        try:
            first=until(lambda s:s.get('onus') and s['onus'][9]['phase']=='configured' and nodes[7].held is not None)
            assert first['onus'][7]['phase']=='provision' and first['onus'][9]['service_configured']
            assert first['service_policies']==2
            assert not any(op==0x44 for _,op,_,_ in nodes[7].operations)
            old=nodes[7];old_count=len(old.operations)
            nodes[7]=Node(7,'5445535400000077',0x317,True);hardware()
            peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+old.held)
            current=until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured' and s['onus'][7]['mib_serial']==nodes[7].serial.hex())
            assert len(old.operations)==old_count and current['onus'][9]['phase']=='configured'
            previous=journal(7,'service-old')[0]
            assert previous['serial']==old.serial.hex() and previous['error']==-errno.ESTALE and previous['grant_withdrawn']
            assert (7,1,0,0,0) in grants
            recipe=next(x for x in journal(7) if x['event']=='service_recipe')
            assert recipe['kind']==2 and recipe['uni']==0x317 and recipe['uni_ordinal']==0
            # Lose registration after a primary failure has started cleanup.
            nodes[9]=Node(9,'5445535400000099',0x409,True,reject=True);hardware()
            until(lambda s:nodes[9].held is not None)
            failed=nodes[9]
            nodes[9]=Node(9,'5445535400000999',0x501,True);hardware()
            peer.send(HOST+OLT+struct.pack('>HH',0x8888,9)+failed.held)
            until(lambda s:s.get('onus') and s['onus'][9]['phase']=='configured' and s['onus'][9]['mib_serial']==nodes[9].serial.hex())
            previous=journal(9,'service-old')
            assert previous[0]['error']==-errno.EREMOTEIO and previous[0]['cleanup_error']==-errno.ESTALE
            ledger=next(x for x in previous if x['event']=='service_objects')
            assert ledger['failure']['error']==-errno.EREMOTEIO and ledger['cleanup_failure']['error']==-errno.ESTALE
            assert any(x['class']==268 and x['touched'] for x in ledger['objects'])
            for id in nodes:
                row=journal(id)[0]
                assert row['configured'] and row['serial']==nodes[id].serial.hex()
                assert not store.joinpath(f'onu-{id}.service').stat().st_mode&0o077
                assert len(nodes[id].table)==3
            # A malformed policy must fail before acquiring or changing hardware.
            valid_policy=policy.read_text()
            policy.write_text('* 2 0 1 3 200 50 100 200\n* 0 0 1 3 none 50 100 200\n')
            rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host',
                '--services',str(policy)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2)
            assert rejected.returncode==1 and 'policy' in rejected.stderr
            assert status()['pid']==process.pid and device.joinpath('tx_enable').read_text().startswith('1\n')
            policy.write_text(valid_policy)
            process.send_signal(signal.SIGTERM);_,error=process.communicate(timeout=3)
            assert process.returncode==0,error
            assert status()['state']=='stopped' and device.joinpath('tx_enable').read_text().startswith('0\n')
            old_pid=process.pid
            before={id:len(node.operations) for id,node in nodes.items()}
            process=launch()
            until(lambda s:s.get('onus') and all(s['onus'][id]['phase']=='configured' for id in nodes))
            assert process.pid!=old_pid
            for id,node in nodes.items():
                assert not any(op in (0x44,0x48,0x46) for _,op,_,_ in node.operations[before[id]:])
                assert journal(id)[0]['configured'] and journal(id,'service-old')[0]['configured']
            # An observed disconnect/reconnect with the same serial reuses the
            # actual remote MEs, after the local grant has been withdrawn.
            node=nodes.pop(7);before=len(node.operations);hardware()
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='absent')
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+1)*8)==(0,0)
            nodes[7]=node;hardware()
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured')
            assert not any(op in (0x44,0x48,0x46) for _,op,_,_ in node.operations[before:])
            assert journal(7,'service-old')[0]['grant_withdrawn']
            process.send_signal(signal.SIGTERM);_,error=process.communicate(timeout=3)
            assert process.returncode==0,error
            # No policy keeps the original inventory-only behavior and cannot
            # expose a prior process's service journal as a current result.
            process=launch(with_policy=False)
            until(lambda s:s.get('onus') and all(s['onus'][id]['phase']=='inventory' for id in nodes))
            assert status()['service_policies']==0
            assert all(not store.joinpath(f'onu-{id}.service').exists() for id in nodes)
            process.send_signal(signal.SIGTERM);_,error=process.communicate(timeout=3)
            assert process.returncode==0,error
            # A shared AF_PACKET send failure is a controller failure, not a
            # peer timeout that leaves the global controller falsely running.
            nodes[7]=Node(7,'5445535400000077',0x3ff,True,hold=True)
            with device.joinpath('tcont_state').open('r+b') as f:
                f.seek((7*8+1)*8);f.write(bytes(8))
            hardware();process=launch()
            until(lambda s:nodes[7].held is not None and s.get('onus') and s['onus'][9]['phase']=='configured')
            subprocess.run(['ip','link','set','dev','fap-host','down'],check=True)
            _,error=process.communicate(timeout=4)
            assert process.returncode==1,error
            assert status()['state']=='failed' and status()['error'] in (-errno.ENETDOWN,-errno.EIO)
            assert device.joinpath('tx_enable').read_text().startswith('0\n')
        finally:
            if process.poll() is None:process.terminate();process.communicate(timeout=3)
        os.close(grant_fd)
    peer.close()
    report={'master_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),
        'slave_sha256':hashlib.sha256(args.slave.read_bytes()).hexdigest(),
        'checks':['one stalled ONU does not block another ONU service',
        'exact serial policy precedes wildcard; UNI instances come from each real MIB',
        'serial replacement stops old transactions, withdraws old local grants and provisions the new registration',
        'replacement during cleanup preserves the primary error, failed cleanup and unknown resources',
        'warm controller restart and observed same-serial reconnect reuse actual remote MEs',
        'duplicate policy fails before hardware changes; no policy remains inventory-only without stale current journals',
        'shared transport failure stops the controller and disables TX',
        'atomic private runtime journals retain recipes/ownership/table snapshots; SIGTERM shuts hardware down'],
        'scope':'native daemon over AF_PACKET/veth in a private namespace with original ARM64 schemas and VLAN table replies; peer MIB and synchronous DBA sysfs are modeled; no optical traffic'}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
