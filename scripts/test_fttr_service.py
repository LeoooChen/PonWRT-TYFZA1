#!/usr/bin/env python3
"""Exercise the native full service command over AF_PACKET with real original wire templates."""
import argparse
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import time

from test_fttr_association import Original
from test_fttr_gem import Original as OriginalGem, Config
from arm64_elf_oracle import ElfOracle
from test_fttr_controller import HOST, OLT
from test_fttr_vlan import Slave, master_rules


SYSFS_WRITE_SHIM = '''#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
ssize_t write(int fd,const void *buf,size_t size) {
 static unsigned int calls;
 char link[64],path[512];
 snprintf(link,sizeof(link),"/proc/self/fd/%d",fd);
 ssize_t n=readlink(link,path,sizeof(path)-1);
 if(n>6) {
  path[n]=0;
  if(!strcmp(path+n-6,"/tcont")) {
   unsigned int id,slot,f,a,m;
   sscanf(buf,"%u %u %u %u %u",&id,&slot,&f,&a,&m);
   const char *mode=getenv("FTTR_SERVICE_CASE");
   if((strcmp(mode,"grant-false-success") || calls) &&
      (strcmp(mode,"grant-restore-false") || !calls)) {
    strcpy(path+n-6,"/tcont_state");
    int state=open(path,O_WRONLY);
    uint32_t words[2]={f,a<<16|m};
    pwrite(state,words,8,(id*8+slot)*8);close(state);
   }
   calls++;
  }
 }
 ssize_t (*real_write)(int,const void *,size_t)=dlsym(RTLD_NEXT,"write");
 return real_write(fd,buf,size);
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--master', type=Path, required=True)
    p.add_argument('--slave', type=Path, required=True)
    p.add_argument('--control', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))} == {'lo'}
    master, slave = Original(args.master), Slave(args.slave)
    gem_source = OriginalGem(args.master)
    tcont_binary = args.slave.parent.parent/'lib/omci/mib_Tcont.so'
    def tcont_hook(name, a):
        if name == 'MIB_InfoRegister': return 0
        if name == 'memset': tcont.uc.mem_write(a[0],bytes([a[1]&255])*a[2]); return a[0]
        raise AssertionError(name)
    tcont = ElfOracle(tcont_binary,tcont_hook)
    info = struct.unpack('<Q',tcont.uc.mem_read(0x13fd8,8))[0]
    for flag, expected in [(0,255),(1,65535)]:
        tcont.uc.mem_write(info+584,struct.pack('<I',flag))
        assert tcont.call('mibTable_init',1)==0
        assert struct.unpack('<H',tcont.uc.mem_read(tcont.symbols['gMibTcontDefRow']+2,2))[0]==expected
    o, ptr = master.oracle, master.data
    widths = {}
    for c in (11,45,47,84,171,262,266,268,272,277,281,309,310,311,329,65531):
        assert o.call('plr_gponomci_medetail_get', c, ptr, ptr+8) == 1
        table = struct.unpack('<Q',o.uc.mem_read(ptr+8,8))[0]
        widths[c] = {struct.unpack('<H',o.uc.mem_read(table+i*40+8,2))[0]:o.uc.mem_read(table+i*40+12,1)[0]
                     for i in range(o.uc.mem_read(ptr,1)[0])}
    # Source high-level UNI mode1 selects two discard-tagged rows followed by
    # tag-untagged. Model the producer's validation and row-install boundaries.
    source_modes = []
    class Modes(Original):
        def handler(self, name, a):
            if name == 'mode_validate': return 0
            if name == 'mode_install': source_modes.append(struct.unpack('<5H',self.oracle.uc.mem_read(a[1]+40,10))); return 0
            return super().handler(name, a)
    modes = Modes(args.master)
    mo, mp = modes.oracle, modes.data
    mo.symbols.update(mode_access=0xb4c10, mode_transparent=0xb5704)
    mo.hooks.update({0xb37a8:'mode_validate',0xb4778:'mode_install',0xb4a28:'mode_install'})
    mo.uc.mem_write(mp, bytes(1024)); mo.uc.mem_write(mp+160+8, struct.pack('<I',3))
    mo.uc.mem_write(mp+64+14, struct.pack('<HH',200,3))
    assert mo.call('mode_access',mp,mp+64,mp+160) == 0
    assert source_modes == [(0xfffc,0xfffe,0xffff,0xffff,0), (0xfffd,0xfffd,0xffff,200,3)]
    source_modes.clear()
    assert mo.call('mode_transparent',mp,mp+64,mp+160) == 0
    assert source_modes[0][:4] == (0xfffe,)*4
    subprocess.run(['ip','link','add','fts-host','type','veth','peer','name','fts-peer'], check=True)
    for interface, mac in [('fts-host','02:03:04:05:06:07'),('fts-peer','0a:0b:0c:0d:0e:0f')]:
        subprocess.run(['ip','link','set','dev',interface,'address',mac,'mtu','1982','up'],check=True)
    peer = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x8888))
    peer.bind(('fts-peer',0x8888)); peer.setblocking(False)
    checks = []
    with tempfile.TemporaryDirectory() as directory:
        device = Path(directory)
        # Real sysfs store completes the hardware write before returning. Model
        # that synchronous boundary, which a FIFO notification alone cannot do.
        shim = device/'sysfs.c'
        shim.write_text(SYSFS_WRITE_SHIM)
        subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-o',str(device/'sysfs.so'),str(shim),'-ldl'],check=True)
        device.joinpath('omci_link').write_text('host=02:03:04:05:06:07 olt=0a:0b:0c:0d:0e:0f ethertype=8888 downstream_length=0\n')
        device.joinpath('backend').write_text('asic9690\n')
        device.joinpath('management_grant').write_text('')
        os.mkfifo(device/'tcont')
        grant_fd = os.open(device/'tcont',os.O_RDWR|os.O_NONBLOCK)
        offset = (7*8+1)*8

        def identity(replaced=False):
            sn = bytes.fromhex('5445535400000008' if replaced else '5445535400000007')
            device.joinpath('onu_state').write_bytes(b''.join(struct.pack('<III8s',1<<18 if i==7 else 0,1,42,sn) for i in range(64)))

        def set_grant(fixed, limits):
            with device.joinpath('tcont_state').open('r+b') as f:
                f.seek(offset); f.write(struct.pack('<II',fixed,limits))

        def run(case='normal', veip=False, tagged=False, initial=None, backend='asic9690', shared=False, multicast=0):
            nonlocal offset
            device.joinpath('backend').write_text(backend+'\n')
            physical_slot = 0 if backend == 'fpga1' else 1
            offset = (7*8+physical_slot)*8
            identity(); device.joinpath('tcont_state').write_bytes(bytes(4096))
            pool = {(262,0x8765):{0x8000:struct.pack('>H',255)}}
            pool[329 if veip else 11,0x107] = {0x8000:b'\1'} if veip else {0x8000:b'\0',0x0800:b'\1'}
            if shared:
                pool[11,0x101] = {0x8000:b'\0',0x0800:b'\1'}
                pool[11,0x203] = {0x8000:b'\0',0x0800:b'\1'}
                pool[329,0x301] = {0x8000:b'\1'}
            table, operations, grants, snapshot = {}, [], [], []
            if initial:
                pool,table = copy.deepcopy(initial)
                set_grant(50,100<<16|200)
            grant_buffer, vlan_writes = b'', 0
            if case == 'busy': set_grant(7,8)

            def attrs(c, instance, mask, values):
                fields, pos = {}, 0
                for bit, width in widths[c].items():
                    if mask & bit: fields[bit] = values[pos:pos+width]; pos += width
                assert pos == len(values), (c,mask,len(values),pos)
                return fields

            def records():
                # The first two T-CONTs cannot provide this service: one is busy,
                # the other has no matching upstream priority queue. A downstream
                # queue with the same Related Port must never be selected.
                rows = [(262,0x7001,0x8000,struct.pack('>H',555)),(262,0x8001,0x8000,b'\0\xff'),
                    (277,1,0x0400,struct.pack('>I',0x87650003)),
                    (277,0x89ab,0x0400,struct.pack('>I',0x87650003))]
                if case == 'no-queue': rows = [r for r in rows if r[0] != 277 or r[1] == 1]
                if case in ('alloc-without-queue','duplicate-alloc'):
                    rows = [(c,i,m,b'\x01\x39' if c==262 and i==0x8001 else v) for c,i,m,v in rows]
                    if case == 'duplicate-alloc': pool[262,0x8765][0x8000] = b'\x01\x39'
                for (c, instance), attributes in pool.items():
                    mask, content = 0, b''
                    for bit, value in sorted(attributes.items(),reverse=True):
                        if len(content)+len(value)>26:
                            rows.append((c,instance,mask,content)); mask,content = 0,b''
                        mask |= bit; content += value
                    rows.append((c,instance,mask,content))
                return rows

            query = subprocess.Popen([str(args.control.resolve()),str(device),'service','7','shared-ctc' if multicast==2 else 'shared-mcast' if multicast else 'shared' if shared else '2' if veip else '0',
                '0' if shared else '0x107','1','3','200' if tagged else 'none','50','100','200','fts-host'],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                env=dict(os.environ,LD_PRELOAD=str(device/'sysfs.so'),FTTR_SERVICE_CASE=case))
            try:
                deadline = time.monotonic()+9
                while query.poll() is None:
                    assert time.monotonic()<deadline, (case,operations)
                    ready,_,_ = select.select([peer,grant_fd],[],[],0.02)
                    for item in ready:
                        if item == grant_fd:
                            grant_buffer += os.read(grant_fd,1024)
                            while b'\n' in grant_buffer:
                                line,grant_buffer = grant_buffer.split(b'\n',1)
                                id,slot,f,a,m = map(int,line.split()); assert (id,slot)==(7,physical_slot)
                                grants.append((f,a,m))
                            continue
                        frame,address = peer.recvfrom(2048)
                        if address[2] == socket.PACKET_OUTGOING: continue
                        body = frame[16:60]
                        tci,op,dev,c,instance = struct.unpack('>HBBHH',body[:8])
                        assert frame[:16] == OLT+HOST+bytes.fromhex('88880007') and dev == 10
                        mask = struct.unpack('>H',body[8:10])[0]
                        operations.append((op,c,instance,body.hex()))
                        status, content, wire = 0, b'', None
                        if op == 0x4d:
                            snapshot = records(); content = struct.pack('>H',len(snapshot))
                        elif op == 0x4e:
                            a,b,d,v = snapshot[mask]; content = struct.pack('>HHH',a,b,d)+v.ljust(26,b'\0')
                        elif op == 0x44:
                            assert (c,instance) not in pool
                            if (case == 'mcast-reject' and c == 310) or (case == 'gem-reject' and c == 266) or (c == 171 and (
                                case in ('shared-uni-reject','shared-cleanup-reject') and instance == 2 or
                                case == 'shared-last-uni-reject' and instance == 4)): status = 6
                            else:
                                if c==47: create_mask=0xfc00 if body[11] in (5,6,11) else 0xfe00
                                elif c==84: create_mask=0xe000 if tagged and pool[47,instance][0x2000]==bytes([5]) else 0x4000
                                else: create_mask={272:0x8000,45:0xff80,268:0xfac0,266:0xf200,171:0x8200,281:0xf300,309:0xfc78,65531:0xfc59,310:0xf800,311:0x8000}[c]
                                n = sum(w for bit,w in widths[c].items() if create_mask&bit)
                                pool[c,instance] = attrs(c,instance,create_mask,body[8:8+n])
                        elif op == 0x46:
                            if (case == 'delete-reject' and c == 268) or (case == 'shared-cleanup-reject' and c == 47 and instance == 2): status = 6
                            else: pool.pop((c,instance))
                        elif op == 0x48:
                            if c == 171 and mask == 0x0400:
                                vlan_writes += 1
                                if case in ('vlan-reject','vlan-restore-reject','grant-restore-false') and vlan_writes == (5 if shared else 2): status = 6
                                elif case == 'vlan-restore-reject' and vlan_writes == (6 if shared else 3): status = 6
                                else:
                                    row = body[10:26]
                                    key=(instance,row[:8]) if shared else row[:8]
                                    if row[8:] == b'\xff'*8: table.pop(key,None)
                                    else: table[key] = row
                                wire = slave.reply(body,b''.join(row for key,row in table.items() if not shared or key[0]==instance),8,status)
                            elif c == 281 and mask == 0x80:
                                row = body[10:22]
                                if case == 'mcast-table-reject' and row[4:] != bytes(8): status = 6
                                elif row[4:] == bytes(8): table.pop(('mcast', instance), None)
                                else: table['mcast', instance] = row
                            else:
                                n = sum(w for bit,w in widths[c].items() if mask&bit)
                                pool[c,instance].update(attrs(c,instance,mask,body[10:10+n]))
                        else:
                            assert op in (0x49,0x5a)
                            if (c,instance) not in pool or (case == 'shared-check-missing' and c == 329 and instance == 0x301): status = 5
                            elif c == 171 and mask in (0x4000,0x0400):
                                wire = slave.reply(body,b''.join(row for key,row in table.items() if not shared or key[0]==instance),8)
                            elif c == 281 and mask == 0x80:
                                rows = table.get(('mcast', instance), b'')
                                content = struct.pack('>H',mask)+(struct.pack('>I',len(rows)) if op == 0x49 else rows)
                            else:
                                values = b''.join(pool[c,instance][bit] for bit in sorted(widths[c],reverse=True) if mask&bit)
                                content = struct.pack('>H',mask)+values
                        if case == 'delete-reject' and op == 0x49 and c == 171 and mask == 0x3100 and (c,instance) in pool:
                            # Force cleanup after the last byte of the actual Set group fails readback.
                            content = content[:-1]+bytes([content[-1]^1])
                        if wire is None:
                            wire = master.frame(tci,(op&0x1f)|0x20, content if op in (0x4d,0x4e) else bytes([status])+content,
                                                onu=7,class_id=c,instance=instance)[16:]
                        peer.send(HOST+OLT+bytes.fromhex('88880007')+wire)
                output,error = query.communicate(timeout=2)
                # Drain writes completed before command exit, so they cannot be
                # attributed to the following invocation.
                while select.select([grant_fd],[],[],0)[0]: grant_buffer += os.read(grant_fd,1024)
                while b'\n' in grant_buffer:
                    line,grant_buffer = grant_buffer.split(b'\n',1)
                    id,slot,f,a,m = map(int,line.split()); assert (id,slot)==(7,physical_slot)
                    grants.append((f,a,m))
                events = [json.loads(line) for line in output.splitlines()]
                result = next(x for x in events if x['event']=='service_state')
                assert query.returncode == (1 if result['error'] else 0),(case,output,error)
                return result,events,pool,table,operations,grants
            finally:
                if query.poll() is None: query.terminate(); query.communicate(timeout=3)

        for veip,tagged in [(False,False),(True,True)]:
            result,events,pool,table,operations,grants = run(veip=veip,tagged=tagged)
            assert result['configured'] and result['grant_verified'] and not result['error'],events
            assert result['tcont']==0x8765 and result['queue']==0x89ab and result['gem']==0x107
            assert grants == [(50,100,200)] and pool[262,0x8765][0x8000] == b'\x01\x39'
            assert pool[329 if veip else 11,0x107][0x8000 if veip else 0x0800] == b'\0'
            enable = master.me_request(0x48,329 if veip else 11,0x107,0x8000 if veip else 0x0800,bytes(32))
            assert [bytes.fromhex(o[3])[2:] for o in operations if o[0]==0x48 and o[1] in (11,329)] == [enable[2:]]
            _, expected_gem = gem_source.producer(Config(0x107,0x8765,0x89ab,0x8080,1,0,0,0,True))
            assert [bytes.fromhex(o[3])[2:] for o in operations if o[0]==0x44 and o[1] in (268,266)] == [b[2:] for b in expected_gem]
            profiles = master.producer('plr_gponrm_gal_add')+master.producer('shared')
            assert [bytes.fromhex(o[3])[2:] for o in operations if o[0]==0x44 and o[1] in (272,45)] == [b[2:] for b in profiles]
            expected = master_rules(master,0xfbd08,0,0,0)+master_rules(master,0xfc0c0,0,200,3) if tagged else master_rules(master,0xfbeb0,0,0,0)
            assert list(table.values()) == expected
            reused,_,_,_,again,writes = run(veip=veip,tagged=tagged,initial=(pool,table))
            assert reused['configured'] and not reused['grant_sent'] and not writes
            assert not any(o[0] in (0x44,0x48,0x46) for o in again)
        checks.append('fresh MIB chooses available real T-CONT and related upstream queue; Ethernet transparent and VEIP access paths configure/read back all objects, UNI administration, local DBA and original compound VLAN policy; repeats send no configuration writes')
        for tagged in (False,True):
            result,events,pool,table,operations,grants=run(shared=True,tagged=tagged)
            assert result['configured'] and result['shared'] and result['uni_count']==4
            unis=[e for e in events if e['event']=='service_uni']
            assert {(e['kind'],e['instance']) for e in unis}=={(0,0x101),(0,0x107),(0,0x203),(2,0x301)}
            assert len({e['port'] for e in unis})==4 and result['ani_port'] not in {e['port'] for e in unis}
            assert grants==[(50,100,200)]
            first_write=next(i for i,o in enumerate(operations) if o[0] in (0x44,0x48,0x46))
            checked={(c,i) for op,c,i,_ in operations[:first_write] if op==0x49}
            for u in unis:
                expected={(47,u['port']),(171,u['port']),
                          (329 if u['kind']==2 else 11,u['instance'])}
                if u['kind']==0: expected.add((84,u['port']))
                assert expected <= checked,(u,expected-checked)
            for u in unis:
                assert pool[47,u['port']][0x8000]==b'\x80\x80'
                assert pool[47,u['port']][0x1000]==struct.pack('>H',u['instance'])
                assert pool[329 if u['kind']==2 else 11,u['instance']][0x8000 if u['kind']==2 else 0x0800]==b'\0'
                expected=master_rules(master,0xfbd08,0,0,0)+master_rules(master,0xfc0c0,0,200,3) if tagged else master_rules(master,0xfbeb0,0,0,0)
                assert [row for key,row in table.items() if key[0]==u['port']]==expected
            reused,_,_,_,again,writes=run(shared=True,tagged=tagged,initial=(pool,table))
            assert reused['configured'] and not writes and not any(o[0] in (0x44,0x48,0x46) for o in again)
        for multicast in (1,2):
            result,events,pool,table,operations,grants=run(shared=True,tagged=True,multicast=multicast)
            assert result['configured'] and grants==[(50,100,200)]
            mc=next(e for e in events if e['event']=='service_multicast')
            assert mc['configured'] and not mc['error']
            uni=next(e for e in events if e['event']=='service_uni' and e['kind']==2)
            assert (65531 if multicast==2 else 309,uni['port']) in pool
            assert table['mcast',4095]==bytes.fromhex('0fff0000e0000100efffffff')
            first_write=next(i for i,o in enumerate(operations) if o[0] in (0x44,0x48,0x46))
            checked={(c,i) for op,c,i,_ in operations[:first_write] if op==0x49}
            assert {(268,4095),(281,4095),(310,uni['port']),(311,uni['port'])}<=checked
            reused,_,_,_,again,writes=run(shared=True,tagged=True,multicast=multicast,initial=(pool,table))
            assert reused['configured'] and not writes and not any(o[0] in (0x44,0x48,0x46) for o in again)
            foreign=copy.deepcopy(pool)
            port=next(i for (c,i),attrs in foreign.items() if c==47 and attrs[0x2000]==b'\6')
            foreign[47,port][0x8000]=b'\x80\x81'
            rejected,_,_,_,again,writes=run(shared=True,tagged=True,multicast=multicast,initial=(foreign,table))
            assert rejected['error']==-errno.EEXIST and not writes and not any(o[0] in (0x44,0x48,0x46) for o in again)
            for case in ('mcast-reject','mcast-table-reject'):
                result,_,pool,table,_,grants=run(case=case,shared=True,tagged=True,multicast=multicast)
                assert result['error']==-errno.EREMOTEIO and not result['cleanup_error'],result
                assert not table and set(pool)=={(262,0x8765),(11,0x107),(11,0x101),(11,0x203),(329,0x301)},pool
                assert pool[262,0x8765][0x8000]==b'\0\xff'
                assert all(pool[c,i][0x8000 if c==329 else 0x0800]==b'\1' for c,i in pool if c in (11,329))
                assert grants==[(50,100,200),(0,0,0)],grants
        checks.append('explicit standard/CTC multicast shared service preflights every resource, selects actual first VEIP, verifies original GEM4095 table, adopts without writes and unwinds all UNI/VLAN/DBA state on multicast failure')
        # The later UNI's missing administration ME is visible only during the
        # live preflight GET, after the complete MIB snapshot was uploaded.
        result,_,_,_,operations,grants=run(case='shared-check-missing',shared=True)
        assert result['error']==-errno.EREMOTEIO and not grants
        assert not any(o[0] in (0x44,0x48,0x46) for o in operations)
        for failure in ('shared-uni-reject','shared-last-uni-reject','vlan-reject'):
            result,events,pool,table,operations,grants=run(case=failure,shared=True,tagged=True)
            assert result['error'] and not result['configured'] and not result['cleanup_error'],(failure,result)
            assert not table and not any(c in (45,47,84,171,266,268,272) for c,i in pool),(failure,pool,table)
            assert pool[262,0x8765][0x8000]==b'\0\xff'
            assert all(pool[c,i][0x8000 if c==329 else 0x0800]==b'\1' for c,i in pool if c in (11,329))
            if failure=='shared-uni-reject':
                assert not any(op in (0x44,0x48,0x46) and ((c in (47,84,171) and i in (3,4)) or
                    (c==11 and i==0x203) or c==329) for op,c,i,_ in operations)
        for failure in ('shared-cleanup-reject','vlan-restore-reject'):
            result,events,pool,table,operations,grants=run(case=failure,shared=True,tagged=True)
            assert result['error']==result['cleanup_error']==-errno.EREMOTEIO
            assert all((c,i) in pool for c,i in ((45,0x8080),(266,0x107),(268,0x107),(272,1)))
            assert not any(op==0x46 and c in (45,266,268,272) for op,c,i,_ in operations)
        # Preserve an existing UNI table and its original admin state while a
        # later UNI fails. The table's tag-untagged row differs from the recipe.
        _,events,prior_pool,_,_,_=run(shared=True,tagged=True)
        old_row=master_rules(master,0xfc0c0,0,123,3)[0]
        prior_table={(e['port'],old_row[:8]):old_row for e in events if e['event']=='service_uni'}
        for c,i in prior_pool:
            if c in (11,329): prior_pool[c,i][0x8000 if c==329 else 0x0800]=b'\1'
        result,_,pool,table,_,grants=run(case='vlan-reject',shared=True,tagged=True,initial=(prior_pool,prior_table))
        assert result['error']==-errno.EREMOTEIO and not result['cleanup_error']
        assert pool==prior_pool and table==prior_table and not grants
        for conflict,expected in (('foreign-bridge',errno.EEXIST),('duplicate-uni',errno.EEXIST),
                                  ('duplicate-number',errno.EUCLEAN),('missing-bridge',errno.EUCLEAN)):
            inconsistent=copy.deepcopy(prior_pool)
            ports=[e['port'] for e in events if e['event']=='service_uni']
            if conflict=='foreign-bridge': inconsistent[47,ports[-1]][0x8000]=b'\x80\x81'
            elif conflict=='duplicate-uni': inconsistent[47,99]=copy.deepcopy(inconsistent[47,ports[0]])
            elif conflict=='duplicate-number': inconsistent[47,ports[-1]][0x4000]=inconsistent[47,ports[0]][0x4000]
            else: del inconsistent[45,0x8080]
            result,_,_,_,operations,grants=run(shared=True,tagged=True,initial=(inconsistent,prior_table))
            assert result['error']==-expected and not grants,(conflict,result)
            assert not any(o[0] in (0x44,0x48,0x46) for o in operations)
        checks.append('shared bridge attaches every reported Ethernet and VEIP UNI; all dependencies are checked before writes, repeated adoption is read-only, foreign/duplicate ownership rejects, later failures restore prior tables/admin state, and cleanup failure preserves shared dependencies')
        for backend in ('fpga1',):
            device.joinpath('management_grant').unlink()
            result,events,pool,table,operations,grants = run(backend=backend)
            assert result['configured'] and result['grant_verified'], events
            assert pool[262,0x8765][0x8000] == b'\x01\x07'
            assert grants == [(50,100,200)]
            reused,_,_,_,again,writes = run(backend=backend, initial=(pool,table))
            assert reused['configured'] and not writes
            assert not any(o[0] in (0x44,0x48,0x46) for o in again)
            failed,_,pool,table,_,grants = run(backend=backend, case='vlan-reject')
            assert failed['error'] == -errno.EREMOTEIO and not failed['cleanup_error']
            assert pool[262,0x8765][0x8000] == b'\0\xff' and not table
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),offset) == (0,0)
            device.joinpath('management_grant').write_text('')
        checks.append('FPGA1 service CLI generates the FPGA Alloc-ID, opens OMCI without an ASIC management grant, verifies the correct physical DBA slot, reuses resources and restores on VLAN failure')
        for case in ('gem-reject','delete-reject','grant-false-success','vlan-reject','vlan-restore-reject','grant-restore-false','busy','no-queue','alloc-without-queue','duplicate-alloc'):
            result,events,pool,table,operations,grants = run(case)
            expected_error = {'delete-reject':errno.EUCLEAN,'grant-false-success':errno.EUCLEAN,
                'busy':errno.EBUSY,'no-queue':errno.ENOSPC,'alloc-without-queue':errno.ENOSPC,
                'duplicate-alloc':errno.EUCLEAN}.get(case,errno.EREMOTEIO)
            assert result['error'] == -expected_error and not result['configured'],(case,events)
            if case in ('gem-reject','grant-false-success','vlan-reject'):
                assert not result['cleanup_error'] and set(pool) == {(262,0x8765),(11,0x107)} and pool[262,0x8765][0x8000] == b'\0\xff', (case,events,pool)
                assert pool[11,0x107][0x0800] == b'\1'
                assert not table and struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),offset) == (0,0)
            if case in ('delete-reject','vlan-restore-reject','grant-restore-false'):
                assert result['cleanup_error'] and (268,0x107) in pool
            if case == 'grant-restore-false':
                assert not result['grant_restored'] and not result['grant_verified']
                assert result['observed_grant'] == [50,100<<16|200] and pool[262,0x8765][0x8000] == b'\x01\x39'
            if case in ('busy','no-queue','alloc-without-queue','duplicate-alloc'):
                assert not grants and not any(o[0] in (0x44,0x48,0x46) for o in operations)
        checks.append('rejected GEM, failed deletion, false-successful local DBA write/restore, partial VLAN failure and failed VLAN restore retain separate errors/ownership; unavailable/occupied resources and duplicate Alloc-ID ownership stop before writes')
        os.close(grant_fd)
    peer.close()
    report = {'checks':checks,'master_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),
        'slave_sha256':hashlib.sha256(args.slave.read_bytes()).hexdigest(),
        'tcont_mib_sha256':hashlib.sha256(tcont_binary.read_bytes()).hexdigest(),
        'scope':'original ARM64 schemas/producers plus native AF_PACKET in a private namespace; MIB/ONU storage and DBA sysfs peer are modeled; no optical traffic'}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
