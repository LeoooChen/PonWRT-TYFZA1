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
from arm64_elf_oracle import ElfOracle
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_PC


class OriginalSync:
    """Execute the original ONU's sync update with only its three-byte MIB row modeled."""
    def __init__(self,binary):
        self.value=0
        self.oracle=ElfOracle(binary,self.handler)
        self.oracle.symbols['increment_sync']=0x11f68

    def handler(self,name,a):
        u=self.oracle.uc
        if name=='MIB_GetFirst':
            assert a[2]==3
            u.mem_write(a[1],struct.pack('<HB',0,self.value));return 0
        if name=='MIB_Set':
            assert a[2]==3
            self.value=u.mem_read(a[1]+2,1)[0];return 0
        if name=='MIB_Default':
            assert a[2]==3
            u.mem_write(a[1],self.default_row);return 0
        raise AssertionError(('unmodeled sync call',name))

    def increment(self):
        self.oracle.call('increment_sync')

    def reset(self,default_row):
        # OMCI_ResetMib calls eaa0; execute its exact ONU Data default/set
        # block eb04..eb30, stopping before unrelated port initialization.
        self.default_row=default_row
        self.oracle.uc.reg_write(UC_ARM64_REG_SP,self.oracle.scratch+0x1ff000)
        self.oracle.uc.emu_start(0xeb04,0xeb30,count=20000)
        assert self.oracle.uc.reg_read(UC_ARM64_REG_PC)==0xeb30


class SyncSlave(Slave):
    def __init__(self,binary):
        super().__init__(binary)
        self.current_class=171
        self.sync_value=0

    def handler(self,name,a):
        if self.current_class==2:
            if name=='readable_mask':return 0x8000
            if name=='MIB_GetTableIndexByClassId':assert a[0]==2;return 1
            if name=='MIB_GetTableAttrNum':return 2
            if name=='MIB_GetAttrLen':assert a[1]==2;return 1
            if name=='MIB_GetAttrDataType':assert a[1]==2;return 0
            if name=='MIB_GetAttrFromBuf':
                assert a[1]==2 and a[4]==1
                self.oracle.uc.mem_write(a[2],bytes([self.sync_value]));return 0
            if name=='OMCI_MeOperCfg':return 0
        return super().handler(name,a)

    def reply(self,request,data,capacity,status=0):
        self.current_class=struct.unpack('>H',request[4:6])[0]
        return super().reply(request,data,capacity,status)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--master', type=Path, required=True)
    p.add_argument('--slave', type=Path, required=True)
    p.add_argument('--daemon', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))} == {'lo'}
    stock = Original(args.master)
    original_sync=OriginalSync(args.slave)
    for initial,expected in [(0,1),(1,2),(254,255),(255,1)]:
        original_sync.value=initial;original_sync.increment()
        assert original_sync.value==expected
    onu_data=args.slave.parent.parent/'lib/omci/mib_OntData.so'
    def default_hook(name,a):
        if name=='MIB_InfoRegister':return 0
        if name=='memset':default.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
        raise AssertionError(name)
    default=ElfOracle(onu_data,default_hook)
    assert default.call('mibTable_init',1)==0
    default_row=bytes(default.uc.mem_read(default.symbols['gMibOntDataDefRow'],3))
    assert default_row==bytes(3)
    original_sync.value=255;original_sync.reset(default_row)
    assert original_sync.value==0
    stock.oracle.symbols['stock_get']=0x65ce0
    sync_request=stock.me_request(0x49,2,0,0x8000,b'\0')
    o, ptr = stock.oracle, stock.data
    widths = {}
    for c in (2,11,45,47,84,171,262,266,268,272,277,329):
        assert o.call('plr_gponomci_medetail_get',c,ptr,ptr+8)==1
        table = struct.unpack('<Q',o.uc.mem_read(ptr+8,8))[0]
        widths[c] = {struct.unpack('<H',o.uc.mem_read(table+i*40+8,2))[0]:o.uc.mem_read(table+i*40+12,1)[0]
                     for i in range(o.uc.mem_read(ptr,1)[0])}

    class Node:
        def __init__(self,id,serial,uni,veip,hold=False,reject=False):
            self.id,self.serial,self.uni,self.veip = id,bytes.fromhex(serial),uni,veip
            self.tcont,self.queue = 0x8700+uni,0x8a00+uni
            self.slave,self.hold,self.reject = SyncSlave(args.slave),hold,reject
            self.counter=OriginalSync(args.slave)
            self.drop_sync=False
            self.fail_upload_once=False
            self.pool = {(262,self.tcont):{0x8000:b'\0\xff'},
                (277,self.queue):{0x0400:struct.pack('>I',self.tcont<<16|3)},
                (329 if veip else 11,uni):{0x8000:b'\1'} if veip else {0x8000:b'\0',0x0800:b'\1'}}
            if veip: self.pool[11,1] = {0x8000:b'\0',0x0800:b'\1'}
            self.table,self.snapshot,self.operations = {},[],[]
            self.held = None

        def records(self):
            rows = [(2,0,0x8000,bytes([self.counter.value]))]
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
            if c==2 and op==0x49:assert body[2:]==sync_request[2:]
            self.operations.append((tci,op,c,id))
            if c==2 and op==0x49 and self.drop_sync:return None
            status,content,wire = 0,b'',None
            if op==0x4d:
                self.snapshot=self.records();content=struct.pack('>H',len(self.snapshot))
                if self.fail_upload_once:
                    self.fail_upload_once=False
                    return stock.frame(tci,0x2d,content,onu=self.id,class_id=3,instance=0)[16:]
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
                if c==2:
                    self.slave.sync_value=self.counter.value
                    wire=self.slave.reply(body,b'',8)
                elif (c,id) not in self.pool: status=5
                elif c==171 and mask in (0x4000,0x0400): wire=self.slave.reply(body,b''.join(self.table.values()),8)
                else: content=struct.pack('>H',mask)+b''.join(self.pool[c,id][bit] for bit in sorted(widths[c],reverse=True) if mask&bit)
            if op in (0x44,0x48,0x46) and not status:
                self.counter.increment()
            if wire is None:
                wire=stock.frame(tci,(op&0x1f)|0x20,content if op in (0x4d,0x4e) else bytes([status])+content,
                                 onu=self.id,class_id=c,instance=id)[16:]
            if (self.hold and op==0x48 and c==262) or (self.reject and op==0x46 and c==268):
                self.held=wire;return None
            return wire

    nodes={7:Node(7,'5445535400000007',0x107,False,hold=True),
           9:Node(9,'5445535400000009',0x203,True)}
    nodes[9].fail_upload_once=True
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
        ni.joinpath('fttr_accel').write_text('0\n')
        device.joinpath('tx_enable').write_text('1\n')
        device.joinpath('management_grant').write_text('')
        device.joinpath('tcont_state').write_bytes(bytes(4096))
        os.mkfifo(device/'tcont');grant_fd=os.open(device/'tcont',os.O_RDWR|os.O_NONBLOCK)
        accel_log,accel_fault=root/'accel.log',root/'accel.fail'
        accel_log.write_text('')
        accel_shim = r'''
  if(n>=11 && !strcmp(path+n-11,"/fttr_accel")) {
   unsigned int seven=if_nametoindex("fttr-u7"),nine=if_nametoindex("fttr-u9");
   char row[64];int length=snprintf(row,sizeof(row),"%c %u %u\n",((const char *)buf)[0],seven,nine);
   ssize_t (*emit)(int,const void *,size_t)=dlsym(RTLD_NEXT,"write");
   int trace=open(getenv("FTTR_ACCEL_LOG"),O_WRONLY|O_APPEND);
   emit(trace,row,length);close(trace);
   if(((const char *)buf)[0]=='0' && seven && !access(getenv("FTTR_ACCEL_FAIL"),F_OK)) {
    errno=ETIMEDOUT;return -1;
   }
  }
'''
        shim=root/'sysfs.c';shim.write_text(SYSFS_WRITE_SHIM.replace('#include <fcntl.h>',
            '#include <fcntl.h>\n#include <errno.h>\n#include <net/if.h>').replace('  path[n]=0;', '  path[n]=0;'+accel_shim))
        subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-o',str(root/'sysfs.so'),str(shim),'-ldl'],check=True)
        policy=root/'services'
        subprocess.run(['ip','link','set','dev','fap-host','mtu','1982'],check=True)
        subprocess.run(['ip','link','add','br-fttr','type','bridge'],check=True)
        subprocess.run(['ip','link','set','dev','br-fttr','up'],check=True)
        policy.write_text('5445535400000007 0 0 1 3 none 50 100 200 br-fttr\n* 2 0 1 3 200 50 100 200 br-fttr\n')
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
        def ports():
            return {x['ifname']:x for x in json.loads(subprocess.check_output(['ip','-d','-j','link'],text=True))
                if x['ifname'].startswith('fttr-u')}
        def datapath(id):
            port=ports()[f'fttr-u{id}']
            assert port['master']=='br-fttr' and port['linkinfo']['info_data']['id']==256+id
            assert port['ifalias']==f'tyfza1-fttr:{id}:{nodes[id].serial.hex()}:{256+id}'
            assert status()['onus'][id]['datapath_configured']
            assert next(x for x in journal(id) if x['event']=='service_datapath')['ifindex']==port['ifindex']
            return port['ifindex']
        def accel_events():
            return [tuple(map(int,line.split())) for line in accel_log.read_text().splitlines()]
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
            current=status()
            current['onus']=[r for r in current.get('onus',[]) if r['id'] in nodes]
            raise AssertionError(('state timeout',current,grants))
        hardware()
        def launch(with_policy=True):
            subprocess.run(['ip','link','set','dev','fap-host','up'],check=True)
            ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
            device.joinpath('tx_enable').write_text('1\n')
            return subprocess.Popen([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host']+
                (['--services',str(policy)] if with_policy else []),
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,
                env=dict(os.environ,LD_PRELOAD=str(root/'sysfs.so'),FTTR_SERVICE_CASE='normal',
                    FTTR_ACCEL_LOG=str(accel_log),FTTR_ACCEL_FAIL=str(accel_fault)))
        process=launch()
        try:
            first=until(lambda s:s.get('onus') and s['onus'][9]['phase']=='configured' and nodes[7].held is not None)
            assert first['onus'][7]['phase']=='provision' and first['onus'][9]['service_configured']
            assert first['service_policies']==2
            assert set(ports())=={'fttr-u9'}
            datapath(9)
            assert first['onus'][9]['mib_rescans']==1
            assert not any(op==0x44 for _,op,_,_ in nodes[7].operations)
            old=nodes[7];old_count=len(old.operations)
            nodes[7]=Node(7,'5445535400000077',0x317,True);hardware()
            peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+old.held)
            current=until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured' and s['onus'][7]['mib_serial']==nodes[7].serial.hex())
            assert len(old.operations)==old_count and current['onus'][9]['phase']=='configured'
            datapath(7)
            previous=journal(7,'service-old')[0]
            assert previous['serial']==old.serial.hex() and previous['error']==-errno.ESTALE and previous['grant_withdrawn']
            assert (7,1,0,0,0) in grants
            recipe=next(x for x in journal(7) if x['event']=='service_recipe')
            assert recipe['kind']==2 and recipe['uni']==0x317 and recipe['uni_ordinal']==0
            # Lose registration after a primary failure has started cleanup.
            nodes[9]=Node(9,'5445535400000099',0x409,True,reject=True);hardware()
            until(lambda s:nodes[9].held is not None)
            assert 'fttr-u9' not in ports() and 'fttr-u7' in ports()
            failed=nodes[9]
            nodes[9]=Node(9,'5445535400000999',0x501,True);hardware()
            peer.send(HOST+OLT+struct.pack('>HH',0x8888,9)+failed.held)
            until(lambda s:s.get('onus') and s['onus'][9]['phase']=='configured' and s['onus'][9]['mib_serial']==nodes[9].serial.hex())
            datapath(9)
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
            # The hardware registration/SN never changes across these resets.
            # Data sync alone must withdraw the old local grant and re-inventory
            # actual newly created ONU-side resources.
            registered=device.joinpath('onu_state').read_bytes()
            prior=status()['onus'][7]
            old_port=datapath(7)
            healthy_uploads=sum(op==0x4d for _,op,_,_ in nodes[9].operations)
            nodes[7]=Node(7,nodes[7].serial.hex(),0x327,True)
            nodes[7].fail_upload_once=True
            current=until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured' and
                s['onus'][7]['mib_rescans']==prior['mib_rescans']+2)
            failed_mib=journal(7,'failed')
            assert failed_mib[-1]['error']==-errno.EPROTO
            assert any(x['event']=='mib_reply_error' and bytes.fromhex(x['body'])[4:6]==b'\0\3' for x in failed_mib)
            recovered_sync=journal(7,'sync-old')[0]
            assert recovered_sync['error']==-errno.EPROTO and not recovered_sync['active']
            assert recovered_sync['exchange']['replied'] and recovered_sync['exchange']['result']==0
            assert device.joinpath('onu_state').read_bytes()==registered
            assert next(x for x in journal(7) if x['event']=='service_recipe')['uni']==0x327
            old_sync=next(x for x in journal(7,'service-old') if x['event']=='mib_sync')
            assert old_sync['value']>0 and old_sync['observed']==0
            assert journal(7,'service-old')[0]['grant_withdrawn']
            assert datapath(7)!=old_port
            assert not next(x for x in journal(7,'service-old') if x['event']=='service_datapath')['configured']
            assert not any(op==0x4f for _,op,_,_ in nodes[7].operations)
            # An existing graph may legitimately be adopted with sync zero.
            # Keep zero, then erase the MIB again: only the real CTP probe can
            # distinguish that second reset from the intact zero baseline.
            previous=current['onus'][7]['mib_rescans']
            nodes[7].counter.value=0
            current=until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured' and
                s['onus'][7]['mib_rescans']==previous+1 and s['onus'][7]['mib_sync_valid'] and s['onus'][7]['mib_sync']==0)
            previous=current['onus'][7]['mib_rescans']
            probes=sum(op==0x49 and c==268 for _,op,c,_ in nodes[7].operations)
            until(lambda s:sum(op==0x49 and c==268 for _,op,c,_ in nodes[7].operations)>probes)
            assert status()['onus'][7]['mib_rescans']==previous
            nodes[7]=Node(7,nodes[7].serial.hex(),0x337,True)
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured' and s['onus'][7]['mib_rescans']==previous+1)
            assert device.joinpath('onu_state').read_bytes()==registered
            first_ops=[(op,c) for _,op,c,_ in nodes[7].operations]
            assert first_ops.index((0x49,268))<first_ops.index((0x4d,2))
            assert next(x for x in journal(7) if x['event']=='service_recipe')['uni']==0x337
            assert next(x for x in journal(7,'service-old') if x['event']=='mib_sync')['exchange']['result']==5
            # A longer reboot may first time out with registration still set.
            # Record the first failure, withdraw traffic, and recover when a
            # fresh sync reply arrives, while the other ONU remains configured.
            previous=status()['onus'][7]['mib_rescans']
            before=len(nodes[7].operations);nodes[7].drop_sync=True
            current=until(lambda s:s.get('onus') and s['onus'][7]['phase']=='service-failed' and
                s['onus'][7]['mib_sync_error']==-errno.ETIMEDOUT,seconds=12)
            assert current['onus'][9]['phase']=='configured'
            assert not current['onus'][7]['service_configured']
            assert not current['onus'][7]['datapath_configured'] and set(ports())=={'fttr-u9'}
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+1)*8)==(0,0)
            failed_probe=next(x for x in journal(7) if x['event']=='mib_sync')
            assert failed_probe['failure']['error']==-errno.ETIMEDOUT and failed_probe['failure']['attempts']==3
            nodes[7].drop_sync=False
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured' and s['onus'][7]['mib_rescans']==previous+1)
            datapath(7)
            assert not any(op in (0x44,0x48,0x46) for _,op,_,_ in nodes[7].operations[before:])
            assert next(x for x in journal(7,'service-old') if x['event']=='mib_sync')['failure']['error']==-errno.ETIMEDOUT
            assert sum(op==0x4d for _,op,_,_ in nodes[9].operations)==healthy_uploads
            # A malformed policy must fail before acquiring or changing hardware.
            valid_policy=policy.read_text()
            policy.write_text('* 2 0 1 3 200 50 100 200 br-fttr\n* 0 0 1 3 none 50 100 200 br-fttr\n')
            rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host',
                '--services',str(policy)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2)
            assert rejected.returncode==1 and 'policy' in rejected.stderr
            assert status()['pid']==process.pid and device.joinpath('tx_enable').read_text().startswith('1\n')
            policy.write_text(valid_policy)
            process.send_signal(signal.SIGTERM);_,error=process.communicate(timeout=3)
            assert process.returncode==0,error
            assert status()['state']=='stopped' and device.joinpath('tx_enable').read_text().startswith('0\n')
            assert not ports() and all(not x['datapath_configured'] for x in status()['onus'])
            old_pid=process.pid
            before={id:len(node.operations) for id,node in nodes.items()}
            process=launch()
            until(lambda s:s.get('onus') and all(s['onus'][id]['phase']=='configured' for id in nodes))
            assert process.pid!=old_pid
            for id,node in nodes.items():
                datapath(id)
                assert not any(op in (0x44,0x48,0x46) for _,op,_,_ in node.operations[before[id]:])
                assert journal(id)[0]['configured'] and journal(id,'service-old')[0]['configured']
            # An observed disconnect/reconnect with the same serial reuses the
            # actual remote MEs, after the local grant has been withdrawn.
            old_port=datapath(7);event_start=len(accel_events())
            node=nodes.pop(7);before=len(node.operations);hardware()
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='absent')
            assert 'fttr-u7' not in ports()
            events=accel_events()[event_start:]
            assert any(mode==0 and seven==old_port and nine for mode,seven,nine in events)
            assert any(mode==1 and seven==0 and nine for mode,seven,nine in events)
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+1)*8)==(0,0)
            nodes[7]=node;hardware()
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured')
            datapath(7)
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
            assert not ports()
            process.send_signal(signal.SIGTERM);_,error=process.communicate(timeout=3)
            assert process.returncode==0,error
            # A requested bridge failure must not look like a working service.
            # The other peer explicitly requests OMCI-only provisioning.
            policy.write_text(f'{nodes[7].serial.hex()} 2 0 1 3 200 50 100 200 br-missing\n'
                '* 2 0 1 3 200 50 100 200 -\n')
            process=launch()
            current=until(lambda s:s.get('onus') and s['onus'][7]['phase']=='service-failed' and
                s['onus'][9]['phase']=='configured')
            assert current['onus'][7]['datapath_error']==-errno.ENODEV
            assert next(x for x in journal(7) if x['event']=='service_datapath')['bridge']=='br-missing'
            assert not current['onus'][7]['service_configured'] and not ports()
            assert current['onus'][9]['service_configured'] and not current['onus'][9]['datapath_configured']
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+1)*8)==(0,0)
            process.send_signal(signal.SIGTERM);_,error=process.communicate(timeout=3)
            assert process.returncode==0,error
            policy.write_text(valid_policy)
            # An unconfirmed hardware cache withdrawal must stop this controller,
            # retain its error, and never resume acceleration on that teardown.
            process=launch()
            until(lambda s:s.get('onus') and all(s['onus'][id]['phase']=='configured' for id in nodes))
            event_start=len(accel_events());accel_fault.touch()
            node=nodes.pop(7);hardware()
            _,error=process.communicate(timeout=5)
            assert process.returncode==1 and status()['error']==-errno.ETIMEDOUT,error
            assert status()['onus'][7]['datapath_cleanup_error']==-errno.ETIMEDOUT
            assert not any(mode==1 for mode,_,_ in accel_events()[event_start:])
            assert not ports() and device.joinpath('tx_enable').read_text().startswith('0\n')
            accel_fault.unlink();nodes[7]=node;hardware()
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
            assert not ports()
        finally:
            if process.poll() is None:process.terminate();process.communicate(timeout=3)
        os.close(grant_fd)
    peer.close()
    report={'master_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),
        'slave_sha256':hashlib.sha256(args.slave.read_bytes()).hexdigest(),
        'onu_data_sha256':hashlib.sha256(onu_data.read_bytes()).hexdigest(),
        'checks':['one stalled ONU does not block another ONU service',
        'exact serial policy precedes wildcard; UNI instances come from each real MIB',
        'serial replacement stops old transactions, withdraws old local grants and provisions the new registration',
        'replacement during cleanup preserves the primary error, failed cleanup and unknown resources',
        'warm controller restart and observed same-serial reconnect reuse actual remote MEs',
        'duplicate policy fails before hardware changes; no policy remains inventory-only without stale current journals',
        'shared transport failure stops the controller and disables TX',
        'original sync update and reset block execute against modeled MIB storage: 255 wraps to 1 and reset writes zero',
        'same-serial MIB reset is recovered without any hardware registration transition',
        'a requested service recovers from an initial inventory failure after a valid read-only readiness reply',
        'failed reconciliation reply retains its raw body/error; a new valid sync reply resumes inventory',
        'an intact zero sync baseline remains configured; missing CTP distinguishes zero-to-zero MIB loss',
        'sync timeout withdraws only the affected local grant; a valid later reply reconciles without re-creating intact MEs',
        'atomic private runtime journals retain recipes/ownership/table snapshots; SIGTERM shuts hardware down',
        'actual GEM VLAN ports are created only after verified service sync, withdrawn on MIB loss/replacement/disconnect, and removed on shutdown',
        'missing bridge reports a peer datapath failure and withdraws its grant; explicit OMCI-only policy remains distinct',
        'acceleration withdrawal occurs with the old VLAN still present, resumes after removal, and an unconfirmed withdrawal stops TX without resuming'],
        'scope':'native daemon over AF_PACKET/veth in a private namespace with original ARM64 schemas and VLAN table replies; peer MIB and synchronous DBA sysfs are modeled; no optical traffic'}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
