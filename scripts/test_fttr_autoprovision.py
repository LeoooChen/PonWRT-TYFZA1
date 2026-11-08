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
from test_fttr_controller import HOST, OLT, crc
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
    global OLT
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--master', type=Path, required=True)
    p.add_argument('--slave', type=Path, required=True)
    p.add_argument('--daemon', type=Path, required=True)
    p.add_argument('--admin-control', type=Path, help='exercise the resident ONU administration CLI')
    p.add_argument('--upgrade-control', type=Path, help='exercise resident image download/activate/commit')
    p.add_argument('--reboot-control', type=Path, help='exercise remote single-send reboot')
    p.add_argument('--move-control', type=Path, help='exercise the resident SN reservation move')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument("--move-recovery", action="store_true", help="recover durable migration target across process crash")
    p.add_argument("--release-cuts", action="store_true", help="crash at every remote release mutation and resume checked cleanup")
    p.add_argument("--service-state", action="store_true", help="exercise durable remote service ownership across controller restart")
    p.add_argument("--shared", action="store_true", help="exercise shared Ethernet/VEIP services and release with --move-control")
    p.add_argument("--filter-only", action="store_true", help="use transparent UNI mode with ANI VLAN filtering")
    p.add_argument("--multi-services", type=int, choices=(1,2,7), help="exercise a complete shared multi-GEM service collection")
    p.add_argument("--factory", action="store_true", help="generate original defaults for --multi-services, including card limits")
    p.add_argument("--multicast", choices=("standard","ctc"), help="exercise remote multicast shared provisioning and migration")
    p.add_argument("--wifi", action="store_true", help="exercise resident remote Wi-Fi policies")
    p.add_argument("--onu-policy", action="store_true", help="exercise per-ONU authentication and persistent assignment")
    p.add_argument("--auth", action="store_true", help="exercise original global LOID policy before services")
    p.add_argument("--backend", choices=("asic9690", "fpga1", "fpga2"), default="asic9690")
    args = p.parse_args()
    assert not args.multicast or args.shared
    assert not args.shared or (args.move_control and not args.onu_policy)
    assert not args.filter_only or args.shared
    assert not args.factory or (args.multi_services and args.multicast)
    assert args.multi_services != 1 or args.factory
    assert not args.multi_services or (args.shared and args.filter_only and args.auth and args.backend!='fpga1')

    physical_slot = 0 if args.backend == "fpga1" else 1
    downstream_length = args.backend=='fpga2'
    if args.backend == "fpga1": OLT = bytes.fromhex("001111111111")
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))} == {'lo'}
    if args.reboot_control:
        from trace_stock_fttr_reboot import Reboot
        reboot_wire=bytes.fromhex(Reboot(args.master).run()['wire'])
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
    for c in (2,5,6,11,45,47,84,171,256,262,266,268,272,277,281,309,310,311,329,65531,0xff69,0xff6a,0xfffa):
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
            self.wifi_hold = self.wifi_reject = self.wifi_restore_hold = self.wifi_drop = False
            self.wifi_sets = 0
            self.wifi_reject_at = 3
            self.wifi_timeout = self.wifi_malformed = False
            self.wifi_lost_tci = None
            self.upgrade_images=[(1,1,1),(0,0,0)]
            self.upgrade_lost='';self.upgrade_version=b'VERSION0000001'
            self.upgrade_mode='ok';self.upgrade_data=bytearray();self.upgrade_window=bytearray()
            self.upgrade_expected=b'';self.upgrade_section=0;self.upgrade_rejections=0
            self.reboot_mode="ack"
            self.drop_sync=False
            self.fail_upload_once=False
            self.auth_hold = self.auth_drop = self.auth_bad_result = self.auth_reject_result = False
            self.auth_malformed = False
            self.move_hold = self.move_drop = self.move_reject = self.move_false = False
            self.mib_hold = False
            self.release_cut = self.release_writes = 0
            self.auth_ctc_fail = self.auth_onug_fail = 0
            self.pool = {(262,self.tcont):{0x8000:b'\0\xff'},
                (277,self.queue):{0x0400:struct.pack('>I',self.tcont<<16|3)},
                (329 if veip else 11,uni):{0x8000:b'\1'} if veip else {0x8000:b'\0',0x0800:b'\1'}}
            if veip: self.pool[11,1] = {0x8000:b'\0',0x0800:b'\1'}
            if args.shared:
                self.pool[11,0x602]={0x8000:b'\0',0x0800:b'\1'}
                self.pool[329,0x605]={0x8000:b'\1'}
            if args.multi_services:
                for n in range(1,args.multi_services):
                    self.pool[262,self.tcont+n]={0x8000:b'\0\xff'}
                    self.pool[277,self.queue+n]={0x0400:struct.pack('>I',(self.tcont+n)<<16|3)}
            if args.factory:
                # First Ethernet card receives all PPTP UNIs; first VEIP
                # card receives only its matching slot. Surplus/orphan
                # entries must keep their initial administrative state.
                for c,i,t in ((5,2,47),(6,5,22),(5,3,48),(6,6,48)):
                    self.pool[c,i]={0x8000:bytes([t])}
                for i in (0x101,0x102,0x103,0x104,0x701):
                    self.pool[11,i]={0x8000:b'\0',0x0800:b'\1'}
                for i in (0x301,0x302):self.pool[329,i]={0x8000:b'\1'}
                for n in range(args.multi_services):
                    self.pool[277,self.queue+n]={0x0400:struct.pack('>I',(self.tcont+n)<<16)}
            self.reject_gem=0
            self.drift_second=False
            self.drift_table=False
            if args.auth or args.onu_policy:
                self.pool[0xfffa,0] = {0x4000:b'synthetic-loid'.ljust(24,b'\0'),
                    0x2000:b'synthetic-pw'.ljust(12,b'\0'),0x1000:b'\0'}
                self.pool[256,0] = {0x0040:b'synthetic-loid'.ljust(24,b'\0'),
                    0x0020:b'synthetic-pw'.ljust(12,b'\0')}
            if args.wifi:
                for instance in (0x101, 0x201):
                    for cls, masks in ((0xff69, (0x8000,0x4000,0x1000,0x2000,0x20,0x10)),
                                       (0xff6a, (0x8000,0x40,0x4000))):
                        self.pool[cls,instance] = {bit: bytes([0x55])*widths[cls][bit] for bit in masks}
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
            if self.drift_second and op==0x49 and c==262 and id==self.tcont+1:
                self.pool[11,self.uni][0x0800]=b'\1'
                self.drift_second=False
            if self.drift_table and op==0x49 and c==262 and id==self.tcont+1:
                key=next(iter(self.table));row=self.table[key]
                self.table[key]=row[:-1]+bytes([row[-1]^1])
                self.drift_table=False
            if c==2 and op==0x49:assert body[2:]==sync_request[2:]
            self.operations.append((tci,op,c,id))
            if args.upgrade_control and c==7:
                assert id in (0,1)
                if op==0x49:
                    if self.upgrade_mode=='verify-store-fail' and any(op==0x55 for _,op,_,_ in self.operations):
                        (upgrade_store/(self.serial.hex()+'.upgrade.new')).mkdir()
                    assert mask==0xf000
                    content=b'\xf0\0'+self.upgrade_version+bytes(self.upgrade_images[id])
                elif op==0x53:
                    assert id==1 and self.upgrade_images[id][:2]==(0,0)
                    assert body[8]==30 and struct.unpack('>I',body[9:13])[0]==len(self.upgrade_expected)
                    self.upgrade_data.clear();self.upgrade_window.clear();self.upgrade_section=0
                    if self.upgrade_mode=='drop-start':return None
                    content=b'\x1e'
                elif op in (0x14,0x54):
                    assert ni.joinpath('fttr_forward').read_text().startswith('0') and f'fttr-u{self.id}' not in ports()
                    if body[8]==0:self.upgrade_window.clear();self.upgrade_section=0
                    assert body[8]==self.upgrade_section
                    self.upgrade_window+=body[9:40];self.upgrade_section+=1
                    if op==0x14:return None
                    if self.upgrade_mode=='hold':self.held=body;return None
                    if self.upgrade_mode=='reject-window' and not self.upgrade_rejections:
                        self.upgrade_rejections+=1
                        return stock.frame(tci,0x34,bytes([2,body[8]]),onu=self.id,class_id=7,instance=id)[16:]
                    remaining=len(self.upgrade_expected)-len(self.upgrade_data)
                    self.upgrade_data+=self.upgrade_window[:remaining]
                    assert bytes(self.upgrade_data)==self.upgrade_expected[:len(self.upgrade_data)]
                    content=bytes([body[8]])
                elif op==0x55:
                    assert bytes(self.upgrade_data)==self.upgrade_expected
                    assert struct.unpack('>II',body[8:16])==(crc(self.upgrade_expected),len(self.upgrade_expected))
                    if self.upgrade_mode=='reject-end' and not self.upgrade_rejections:
                        self.upgrade_rejections+=1
                        return stock.frame(tci,0x35,b'\x06',onu=self.id,class_id=7,instance=id)[16:]
                    self.upgrade_images[id]=(0,0,1);content=b''
                elif op==0x56:
                    assert self.upgrade_images[id]==(0,0,1)
                    self.upgrade_images[0]=(1,0,1);self.upgrade_images[id]=(0,1,1);content=b''
                    if self.upgrade_lost=='activate':return None
                elif op==0x57:
                    assert self.upgrade_images[id]==(0,1,1)
                    self.upgrade_images[0]=(0,0,1);self.upgrade_images[id]=(1,1,1);content=b''
                    if self.upgrade_lost=='commit':return None
                else:raise AssertionError(('unexpected image operation',op))
                return stock.frame(tci,(op&31)|32,b'\0'+content,onu=self.id,class_id=7,instance=id)[16:]
            if op==0x59:
                assert args.reboot_control and c==256 and id==0
                assert body[2:]==reboot_wire[2:44]
                assert not ni.joinpath('fttr_forward').read_text().startswith('1')
                assert f'fttr-u{self.id}' not in ports()
                if args.backend!='fpga1':
                    assert device.joinpath('management_grant').read_bytes()==management_snapshot
                if self.reboot_mode=='drop':return None
                wire=stock.frame(tci,0x39,bytes([2 if self.reboot_mode=='reject' else 0]),onu=self.id,class_id=256,instance=0)[16:]
                if self.reboot_mode=='malformed':
                    wire=bytearray(wire);wire[3]=11;wire[44:]=struct.pack('>I',crc(wire[:44]));wire=bytes(wire)
                if self.reboot_mode=='hold':self.held=wire;return None
                return wire
            if (args.auth or args.onu_policy) and c in (256,0xfffa) and op in (0x48,0x49):
                if self.auth_drop:return None
                failure=(c==0xfffa and mask==self.auth_ctc_fail) or (c==256 and mask==self.auth_onug_fail)
                status=5 if failure else 2 if op==0x48 and self.auth_reject_result else 0
                if op==0x48:
                    assert c==0xfffa and mask==0x1000
                    if not status:
                        self.pool[c,id][mask]=body[10:11];self.counter.increment()
                    content=b''
                else:
                    value=self.pool[c,id][mask]
                    if mask==0x1000 and self.auth_bad_result:value=b'\0'
                    content=struct.pack('>H',mask)+value
                wire=stock.frame(tci,(op&31)|32,bytes([status])+content,onu=self.id,class_id=c,instance=id)[16:]
                if self.auth_malformed:
                    wire=bytearray(wire);wire[3]=0xff;wire[44:]=struct.pack('>I',crc(wire[:44]));wire=bytes(wire)
                if self.auth_hold:
                    self.held=wire;return None
                return wire
            if c==2 and op==0x49 and self.drop_sync:return None
            if c in (0xff69,0xff6a) and self.wifi_drop:return None
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
                if c==266 and (self.reject or id==self.reject_gem): status=6
                else:
                    if c==47: create_mask=0xfc00 if body[11] in (5,6,11) else 0xfe00
                    elif c==84: create_mask=0xe000 if (self.veip or args.shared) and self.pool[47,id][0x2000]==b'\5' else 0x4000
                    else: create_mask={272:0x8000,45:0xff80,268:0xfac0,266:0xf200,171:0x8200,281:0xf300,309:0xfc78,65531:0xfc59,310:0xf800,311:0x8000}[c]
                    n=sum(w for bit,w in widths[c].items() if create_mask&bit)
                    self.pool[c,id]=self.fields(c,create_mask,body[8:8+n])
            elif op==0x46:
                if self.move_reject:status=2
                elif not self.move_false and not (self.reject and c==268): self.pool.pop((c,id),None)
            elif op==0x48:
                if c==171 and mask==0x0400:
                    row=body[10:26]
                    key=(id,row[:8]) if args.shared else row[:8]
                    if row[8:]==b'\xff'*8: self.table.pop(key,None)
                    else: self.table[key]=row
                    wire=self.slave.reply(body,b''.join(row for key,row in self.table.items() if not args.shared or key[0]==id),8)
                elif c==281 and mask==0x80:
                    row=body[10:22]
                    if row[4:]==bytes(8):self.table.pop(('mcast',id),None)
                    else:self.table['mcast',id]=row
                else:
                    if c in (0xff69,0xff6a):
                        self.wifi_sets += 1
                    n=sum(w for bit,w in widths[c].items() if mask&bit)
                    if self.wifi_reject and self.wifi_sets == self.wifi_reject_at and c in (0xff69,0xff6a):
                        status=2
                    else:
                        self.pool[c,id].update(self.fields(c,mask,body[10:10+n]))
            else:
                assert op in (0x49,0x5a)
                if c==2:
                    self.slave.sync_value=self.counter.value
                    wire=self.slave.reply(body,b'',8)
                elif (c,id) not in self.pool: status=5
                elif c==171 and mask in (0x4000,0x0400): wire=self.slave.reply(body,b''.join(row for key,row in self.table.items() if not args.shared or key[0]==id),8)
                elif c==281 and mask==0x80:
                    rows=self.table.get(('mcast',id),b'')
                    content=struct.pack('>H',mask)+(struct.pack('>I',len(rows)) if op==0x49 else rows)
                else: content=struct.pack('>H',mask)+b''.join(self.pool[c,id][bit] for bit in sorted(widths[c],reverse=True) if mask&bit)
            if op in (0x44,0x48,0x46) and not status:
                self.counter.increment()
            if wire is None:
                wire=stock.frame(tci,(op&0x1f)|0x20,content if op in (0x4d,0x4e) else bytes([status])+content,
                                 onu=self.id,class_id=c,instance=id)[16:]
            if self.release_cut and op in (0x46,0x48) and c not in (65530,256):
                self.release_writes+=1
                if self.release_writes>=self.release_cut:self.held=wire;return None
            if op==0x4d and self.mib_hold:self.held=wire;return None
            if op==0x46 and self.move_drop:return None
            if op==0x46 and self.move_hold:self.held=wire;return None
            if self.wifi_timeout and c in (0xff69,0xff6a) and op==0x48:
                if self.wifi_lost_tci is None:self.wifi_lost_tci=tci
                if tci==self.wifi_lost_tci:return None
            if self.wifi_malformed and c in (0xff69,0xff6a) and op==0x48 and self.wifi_sets==2:
                wire=bytearray(wire);wire[3]=0xff;wire=bytes(wire)
            if c in (0xff69,0xff6a) and op==0x48 and (self.wifi_hold or
                (self.wifi_restore_hold and self.wifi_sets == self.wifi_reject_at + 1)):
                self.held=wire;return None
            if (self.hold and op==0x48 and c==262) or (self.reject and op==0x46 and c==268):
                self.held=wire;return None
            return wire

    nodes={7:Node(7,'5445535400000007',0x107,False,hold=True),
           9:Node(9,'5445535400000009',0x203,True)}
    nodes[9].fail_upload_once=not args.wifi
    if args.wifi:
        nodes[7].hold=False
        nodes[7].wifi_hold=True
    subprocess.run(['ip','link','add','fap-host','type','veth','peer','name','fap-peer'],check=True)
    for interface,mac in [('fap-host','02:03:04:05:06:07'),('fap-peer','0a:0b:0c:0d:0e:0f')]:
        subprocess.run(['ip','link','set','dev',interface,'address',mac,'mtu','1982','up'],check=True)
    if args.multicast:
        # Native upper identification/lifecycle with a macvlan netdev standing
        # in for the kernel P6 transport. Packet behavior is tested separately.
        subprocess.run(['ip','link','add','link','fap-host','name','fttr-mc','type','macvlan','mode','bridge'],check=True)
        subprocess.run(['ip','link','set','dev','fttr-mc','group',str(0x46544d43)],check=True)
    peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x8888))
    peer.bind(('fap-peer',0x8888));peer.setblocking(False)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);device,ni,store=[root/n for n in ('miniolt','ni','state')]
        for path in (device,ni,store):path.mkdir(mode=0o700)
        device.joinpath('omci_link').write_text('host=02:03:04:05:06:07 olt=0a:0b:0c:0d:0e:0f ethertype=8888 downstream_length=0\n')
        device.joinpath('host_serdes').write_text('mode=9 nway=1 pll=1 error=0 link=1\n')
        ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
        ni.joinpath('fttr_forward').write_text('0\n')
        ni.joinpath('fttr_mcast_flush').write_text('')
        device.joinpath('tx_enable').write_text('1\n')
        device.joinpath('backend').write_text(args.backend+'\n')
        auth_policy=root/'loid.bin'
        auth_policy.write_bytes(b'TFL1'+b'synthetic-loid'.ljust(25,b'\0')+b'synthetic-pw'.ljust(13,b'\0'))
        auth_policy.chmod(0o600)
        if args.backend != 'fpga1':
            device.joinpath('management_grant').write_text('')
        if args.backend != 'asic9690':
            device.joinpath('host_serdes').unlink()
            if args.backend=='fpga1':
                device.joinpath('omci_link').write_text('host=02:03:04:05:06:07 olt=00:11:11:11:11:11 ethertype=8888 downstream_length=0\n')
        if downstream_length:
            path=device/'omci_link';path.write_text(path.read_text().replace('downstream_length=0','downstream_length=1'))
        device.joinpath('tcont_state').write_bytes(bytes(4096))
        device.joinpath('onu_admin').write_text('')
        device.joinpath('onu_move').write_text('')
        device.joinpath('onu_move_recover').write_text('')
        admin_fault=root/'admin.fail'
        admin_hold,admin_log=root/'admin.hold',root/'admin.log'
        admin_log.write_text('')
        os.mkfifo(device/'tcont');grant_fd=os.open(device/'tcont',os.O_RDWR|os.O_NONBLOCK)
        accel_log,accel_fault=root/'accel.log',root/'accel.fail'
        accel_log.write_text('')
        recovery_shim = r'''
#include <stdbool.h>
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "rtl9617c-fttr-move.h"
struct recovery_io {int state,grants;bool fpga;};
static int recovery_read(void *ctx,unsigned reg,unsigned *value) {
 struct recovery_io *r=ctx;unsigned index,offset;uint32_t data=0;
 if(reg==0x40007){*value=0;return 0;}
 if(reg>=0x40400&&reg<0x40480){
  index=(reg-0x40400)/2;
  if(reg&1)pread(r->state,&data,4,index*20);
  else pread(r->grants,&data,4,index*64+4);
 }else if(reg>=0x40800&&reg<0x40900){
  index=(reg-0x40800)/4;offset=(reg-0x40800)%4;
  if(offset<2){unsigned char bytes[4];pread(r->state,bytes,4,index*20+12+(offset?0:4));for(unsigned i=0;i<4;i++)data=data<<8|bytes[i];}
  else pread(r->state,&data,4,index*20+(offset==2?8:4));
 }else if(reg>=0x41000&&reg<0x41800){
  index=(reg-0x41000)/32;unsigned slot=(reg-0x41000)%32/4;offset=(reg-0x41000)%4;
  if(offset>1)return -EINVAL;
  pread(r->grants,&data,4,index*64+slot*8+(offset?0:4));
 }else return -EINVAL;
 *value=data;return 0;
}
static int recovery_write(void *ctx,unsigned reg,unsigned value) {
 struct recovery_io *r=ctx;
 if(reg>=0x40007&&reg<=0x4000a)return 0;
 if(reg>=0x40401&&reg<0x40480&&(reg&1))pwrite(r->state,&value,4,((reg-0x40401)/2)*20);
 else if(reg>=0x40800&&reg<0x40900){
  unsigned id=(reg-0x40800)/4,offset=(reg-0x40800)%4;
  if(offset<2){unsigned char bytes[4]={value>>24,value>>16,value>>8,value};pwrite(r->state,bytes,4,id*20+12+(offset?0:4));}
  else if(offset==3)pwrite(r->state,&value,4,id*20+4);
  else return -EINVAL;
 }else return -EINVAL;
 return 0;
}
static void recovery_delay(void *ctx,unsigned delay){(void)ctx;(void)delay;}
static int recovery_run(const char *path,const char *command) {
 char base[512],file[544],sn[17],prior[17],extra;unsigned from,to;unsigned char serial[8],previous[8];
 if(sscanf(command,"%u %u %16s %16s %c",&from,&to,sn,prior,&extra)!=4||from>=64||to>=64)return -EINVAL;
 for(unsigned i=0;i<8;i++){unsigned v;sscanf(sn+2*i,"%2x",&v);serial[i]=v;sscanf(prior+2*i,"%2x",&v);previous[i]=v;}
 strcpy(base,path);*strrchr(base,'/')=0;
 snprintf(file,sizeof(file),"%s/../ni/fttr_forward",base);int fd=open(file,O_RDONLY);char active=0;pread(fd,&active,1,0);close(fd);if(active!='0')return -EBUSY;
 struct recovery_io state={.fpga=getenv("FTTR_ADMIN_FPGA")!=NULL};
 snprintf(file,sizeof(file),"%s/onu_state",base);state.state=open(file,O_RDWR);
 snprintf(file,sizeof(file),"%s/tcont_state",base);state.grants=open(file,O_RDONLY);
 struct rtl_fttr_reg_io io={&state,recovery_read,recovery_write,recovery_delay};
 int ret=rtl_fttr_onu_move_recover(&io,state.fpga?RTL_FTTR_FPGA1:RTL_FTTR_ASIC9690,from,to,serial,previous);
 snprintf(file,sizeof(file),"%s/onu_table",base);FILE *table=fopen(file,"w");
 for(unsigned i=0;i<64;i++){unsigned char row[20];uint32_t words[3];pread(state.state,row,20,i*20);memcpy(words,row,12);
  fprintf(table,"%u %08x %08x %08x ",i,words[0],words[1],words[2]);for(unsigned j=0;j<8;j++)fprintf(table,"%02x",row[12+j]);fputc('\n',table);}
 fclose(table);close(state.state);close(state.grants);
 if(!ret){FILE *trace=fopen(getenv("FTTR_ADMIN_LOG"),"a");fprintf(trace,"move %u %u\n",from,to);fclose(trace);}
 return ret;
}
'''
        accel_shim = r'''
  if(n>=17&&!strcmp(path+n-17,"/onu_move_recover")){int ret=recovery_run(path,buf);if(ret){errno=-ret;return -1;}}
  if(n>=9 && !strcmp(path+n-9,"/onu_move")) {
   unsigned from,to;char sn[17],target[17],base[512],file[544],name[32],accel;
   if(sscanf(buf,"%u %u %16s %16s",&from,&to,sn,target)!=4 || from>=64 || to>=64 || from==to){errno=EINVAL;return -1;}
   for(unsigned i=0;i<2;i++)for(unsigned ordinal=1;ordinal<=7;ordinal++){
    if(ordinal==1)snprintf(name,sizeof(name),"fttr-u%u",i?to:from);
    else snprintf(name,sizeof(name),"fttr-u%u-%u",i?to:from,ordinal);
    if(if_nametoindex(name)){errno=EBUSY;return -1;}}
   strcpy(base,path);base[n-9]=0;snprintf(file,sizeof(file),"%s/../ni/fttr_forward",base);
   int f=open(file,O_RDONLY);pread(f,&accel,1,0);close(f);if(accel!='0'){errno=EBUSY;return -1;}
   snprintf(file,sizeof(file),"%s/tcont_state",base);f=open(file,O_RDONLY);
   unsigned first=getenv("FTTR_ADMIN_FPGA")?0:1,last=first?7:0;
   for(unsigned i=0;i<2;i++)for(unsigned slot=first;slot<=last;slot++){
    uint32_t grant[2];pread(f,grant,8,((i?to:from)*8+slot)*8);
    if(grant[0]||grant[1]){close(f);errno=EBUSY;return -1;}}
   close(f);
   f=open(getenv("FTTR_ADMIN_FAIL"),O_RDONLY);
   if(f>=0){char fault=0;read(f,&fault,1);close(f);errno=fault=='s'?ESTALE:fault=='b'?EBUSY:EIO;return -1;}
   snprintf(file,sizeof(file),"%s/onu_state",base);f=open(file,O_RDWR);
   unsigned char source[20],dest[20];pread(f,source,20,from*20);pread(f,dest,20,to*20);
   char actual[17];for(unsigned i=0;i<8;i++)snprintf(actual+2*i,3,"%02x",source[12+i]);
   if(strcmp(actual,sn)){close(f);errno=ESTALE;return -1;}
   for(unsigned i=0;i<8;i++)snprintf(actual+2*i,3,"%02x",dest[12+i]);
   if(strcmp(actual,target)){close(f);errno=ESTALE;return -1;}
   uint32_t state;memcpy(&state,dest,4);if(state&0x50000){close(f);errno=EBUSY;return -1;}
   const char *move_fault=getenv("FTTR_MOVE_CRASH");
   int crash=0;if(move_fault){int test=open(move_fault,O_RDONLY);if(test>=0){char byte=0;read(test,&byte,1);close(test);crash=byte-'0';}}
   if(crash==1){kill(getpid(),SIGSTOP);}
   if(crash>=3){
    memcpy(dest+12,source+12,crash==4?4:8);pwrite(f,dest,20,to*20);
    if(crash>=5){uint32_t blocked=first?0x50000:0x10000;memcpy(source,&blocked,4);if(!first)memset(source+4,0,4);if(crash>=6)memset(source+12,0,4);pwrite(f,source,20,from*20);}
    kill(getpid(),SIGSTOP);
   }
   memcpy(dest+12,source+12,8);memset(source+12,0,8);
   memcpy(&state,source,4);state=first?(state|0x10000):0x10000;memcpy(source,&state,4);
   if(!first)memset(source+4,0,4);
   pwrite(f,source,20,from*20);pwrite(f,dest,20,to*20);
   snprintf(file,sizeof(file),"%s/onu_table",base);FILE *table=fopen(file,"w");
   for(unsigned i=0;i<64;i++){unsigned char row[20];uint32_t words[3];pread(f,row,20,i*20);memcpy(words,row,12);
    fprintf(table,"%u %08x %08x %08x ",i,words[0],words[1],words[2]);
    for(unsigned j=0;j<8;j++)fprintf(table,"%02x",row[12+j]);
    fputc('\n',table);}
   fclose(table);close(f);
   FILE *trace=fopen(getenv("FTTR_ADMIN_LOG"),"a");fprintf(trace,"move %u %u\n",from,to);fclose(trace);
   if(crash==2)kill(getpid(),SIGSTOP);
  }
  if(n>=13 && !strcmp(path+n-13,"/fttr_forward")) {
   unsigned enabled,type;char extra;
   if(sscanf(buf,"%u",&enabled)!=1 || enabled>1 ||
      (!enabled && sscanf(buf,"%u %c",&enabled,&extra)!=1) ||
      (enabled && (sscanf(buf,"%u %x %c",&enabled,&type,&extra)!=2 || type!=0x8888))){errno=EINVAL;return -1;}
   unsigned int seven=if_nametoindex("fttr-u7"),nine=if_nametoindex("fttr-u9");
   char row[64];int length=snprintf(row,sizeof(row),"%c %u %u\n",((const char *)buf)[0],seven,nine);
   ssize_t (*emit)(int,const void *,size_t)=dlsym(RTLD_NEXT,"write");
   int trace=open(getenv("FTTR_ACCEL_LOG"),O_WRONLY|O_APPEND);
   emit(trace,row,length);close(trace);
   if(((const char *)buf)[0]=='0' && seven && !access(getenv("FTTR_ACCEL_FAIL"),F_OK)) {
    errno=ETIMEDOUT;return -1;
   }
  }
  if(n>=10 && !strcmp(path+n-10,"/onu_admin")) {
   unsigned int id,blocked;char serial[17],base[512],name[32];
   if(sscanf(buf,"%u %u %16s",&id,&blocked,serial)!=3 || id>=64) {errno=EINVAL;return -1;}
   snprintf(name,sizeof(name),"fttr-u%u",id);
   if(if_nametoindex(name)) {errno=EBUSY;return -1;}
   strcpy(base,path);base[n-10]=0;
   char target[544],accel;
   snprintf(target,sizeof(target),"%s/../ni/fttr_forward",base);
   int fd=open(target,O_RDONLY);pread(fd,&accel,1,0);close(fd);
   if(blocked!=2 && accel!='0') {errno=EBUSY;return -1;}
   snprintf(target,sizeof(target),"%s/tcont_state",base);
   fd=open(target,O_RDONLY);uint32_t grants[2];
   int slot=getenv("FTTR_ADMIN_FPGA") ? 0 : 1;
   pread(fd,grants,8,(id*8+slot)*8);close(fd);
   if(grants[0] || grants[1]) {errno=EBUSY;return -1;}
   FILE *log=fopen(getenv("FTTR_ADMIN_LOG"),"a");fprintf(log,"%u %u %s\n",id,blocked,serial);fclose(log);
   fd=open(getenv("FTTR_ADMIN_FAIL"),O_RDONLY);
   if(fd>=0) {char fault=0;read(fd,&fault,1);close(fd);errno=fault=='s'?ESTALE:EIO;return -1;}
   snprintf(target,sizeof(target),"%s/onu_state",base);
   fd=open(target,O_RDWR);unsigned char row[20];pread(fd,row,20,id*20);
   char observed[17];for(unsigned i=0;i<8;i++)snprintf(observed+2*i,3,"%02x",row[12+i]);
   if(strcmp(serial,observed)) {close(fd);errno=ESTALE;return -1;}
   uint32_t state;memcpy(&state,row,4);
   if(blocked==3) {
    uint32_t upstream;memcpy(&upstream,row+4,4);
    uint32_t mask=slot==0?0x00f00000:0x01f00000;
    if((state&0x10000) || (upstream>>30) || !(upstream&mask)) {close(fd);errno=EAGAIN;return -1;}
    upstream&=~mask;memcpy(row+4,&upstream,4);blocked=1;
   }
   if(blocked==2) {
    if(slot==0 && (state&0x10000) && (state&0x40000)) {close(fd);errno=EAGAIN;return -1;}
    state&=~0x10000U;
   }
   else if(slot==0) {
    state=blocked?0x10000:0;if(blocked)memset(row+4,0,4);
    if(blocked && !access(getenv("FTTR_ADMIN_HOLD"),F_OK))state|=0x40000;
   }
   else state=(state&~0x10000U)|(blocked?0x10000:0);
   memcpy(row,&state,4);pwrite(fd,row,20,id*20);
   snprintf(target,sizeof(target),"%s/onu_table",base);FILE *table=fopen(target,"w");
   for(unsigned i=0;i<64;i++) {
    pread(fd,row,20,i*20);uint32_t words[3];memcpy(words,row,12);
    fprintf(table,"%u %08x %08x %08x ",i,words[0],words[1],words[2]);
    for(unsigned j=0;j<8;j++)fprintf(table,"%02x",row[12+j]);
    fputc('\n',table);
   }
   fclose(table);close(fd);
  }
'''
        shim=root/'sysfs.c';shim.write_text(('#include <signal.h>\n'+SYSFS_WRITE_SHIM).replace('#include <fcntl.h>',
            '#include <fcntl.h>\n#include <errno.h>\n#include <net/if.h>').replace('ssize_t write(int fd',recovery_shim+'ssize_t write(int fd').replace('  path[n]=0;', '  path[n]=0;'+accel_shim))
        subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(Path(__file__).resolve().parents[1]/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'),'-o',str(root/'sysfs.so'),str(shim),'-ldl'],check=True)
        policy=root/'services'
        subprocess.run(['ip','link','set','dev','fap-host','mtu','1982'],check=True)
        subprocess.run(['ip','link','add','br-fttr','type','bridge'],check=True)
        subprocess.run(['ip','link','set','dev','br-fttr','up'],check=True)
        policy.write_text('5445535400000007 0 0 1 3 none 50 100 200 br-fttr\n* 2 0 1 3 200 50 100 200 br-fttr\n')
        if args.multicast:
            conflict=root/'conflicting-multicast'
            conflict.write_text('5445535400000007 shared-mcast 0 1 3 200 50 100 200 br-fttr\n* shared-ctc 0 1 3 200 50 100 200 br-other\n')
            rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host',
                '--services',str(conflict)],capture_output=True,text=True,timeout=2)
            assert rejected.returncode==1 and not store.joinpath('status.json').exists()
            assert device.joinpath('tx_enable').read_text()=='1\n'
        # Backend policy rejection precedes ownership, TX and FE mutations.
        for backend, slot in (('fpga1', 2), ('fpga2', 8)):
            device.joinpath('backend').write_text(backend+'\n')
            rejected_policy=root/'unsupported-services'
            rejected_policy.write_text(f'* 2 0 {slot} 3 200 50 100 200 br-fttr\n')
            rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),
                'fap-host','--services',str(rejected_policy)],capture_output=True,text=True,timeout=2)
            assert rejected.returncode==1 and not store.joinpath('status.json').exists()
            assert device.joinpath('tx_enable').read_text()=='1\n'
            assert ni.joinpath('fttr_forward').read_text()=='0\n'
        device.joinpath('backend').write_text(args.backend+'\n')
        for vid in ('filter:', 'filter:none', 'filter:4095', 'filter:0x1000', 'filter:12x'):
            rejected_policy=root/'unsupported-services'
            rejected_policy.write_text(f'* shared 0 1 3 {vid} 50 100 200 br-fttr\n')
            rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),
                'fap-host','--services',str(rejected_policy)],capture_output=True,text=True,timeout=2)
            assert rejected.returncode==1 and not store.joinpath('status.json').exists()
            assert device.joinpath('tx_enable').read_text()=='1\n' and ni.joinpath('fttr_forward').read_text()=='0\n'
        # Missing forwarding withdrawal is a hard startup error, never an
        # acceleration-only fallback that still allows old CPU business TX.
        ni.joinpath('fttr_forward').unlink()
        rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host'],
            capture_output=True,text=True,timeout=2)
        assert rejected.returncode==1 and json.loads(store.joinpath('status.json').read_text())['error']==-errno.ENOENT
        assert device.joinpath('tx_enable').read_text()=='0\n'
        store.joinpath('status.json').unlink();device.joinpath('tx_enable').write_text('1\n')
        ni.joinpath('fttr_forward').write_text('0\n')
        ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
        # A down netdev is not a hardware acknowledgement: the kernel ignores
        # ndo_stop's error. Missing synchronous multicast flush must fail too.
        ni.joinpath('fttr_mcast_flush').unlink()
        rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host'],
            capture_output=True,text=True,timeout=2)
        assert rejected.returncode==1 and json.loads(store.joinpath('status.json').read_text())['error']==-errno.ENOENT
        assert device.joinpath('tx_enable').read_text()=='0\n'
        store.joinpath('status.json').unlink();device.joinpath('tx_enable').write_text('1\n')
        ni.joinpath('fttr_mcast_flush').write_text('')
        ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
        subprocess.run(['ip','link','set','dev','fap-host','up'],check=True)
        if args.auth:
            good_auth = auth_policy.read_bytes()
            rejected_auth = [b'', good_auth[:-1], b'FAIL'+good_auth[4:]]
            for data, mode, duplicate in [(data, 0o600, False) for data in rejected_auth] + [
                    (good_auth, 0o644, False), (good_auth, 0o600, True)]:
                auth_policy.write_bytes(data);auth_policy.chmod(mode)
                rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),
                    'fap-host']+['--loid-auth',str(auth_policy)]*(2 if duplicate else 1),
                    capture_output=True,text=True,timeout=2)
                assert rejected.returncode==1 and not store.joinpath('status.json').exists()
                assert device.joinpath('tx_enable').read_text()=='1\n'
                assert ni.joinpath('fttr_forward').read_text()=='0\n'
                assert device.joinpath('tcont_state').read_bytes()==bytes(4096)
            auth_policy.write_bytes(good_auth);auth_policy.chmod(0o600)
            device.joinpath('backend').write_text('invalid\n')
            rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),
                'fap-host','--loid-auth',str(auth_policy)],capture_output=True,text=True,timeout=2)
            assert rejected.returncode==1 and not store.joinpath('status.json').exists()
            assert device.joinpath('tx_enable').read_text()=='1\n' and ni.joinpath('fttr_forward').read_text()=='0\n'
            device.joinpath('backend').write_text(args.backend+'\n')
        wifi_policy=root/'wifi-policies'
        if args.wifi:
            from test_fttr_wifi_cli import policy as wifi_config_text
            from trace_stock_fttr_wifi import config as wifi_config
            for name, instance, channel in (('wifi-first',0x101,6), ('wifi-second',0x201,6), ('wifi-exact',0x101,11)):
                cfg=bytearray(wifi_config());cfg[4]=channel
                path=root/name
                path.write_text(wifi_config_text(cfg).replace('instance 257',f'instance {instance}'))
                path.chmod(0o600)
            wifi_policy.write_text(f'* {root}/wifi-first\n* {root}/wifi-second\n5445535400000007 {root}/wifi-exact\n')
        grants,grant_buffer=[],b''
        def hardware():
            data=b''.join(struct.pack('<III8s',1<<18 if id in nodes else 0,1,42,
                nodes[id].serial if id in nodes else bytes(8)) for id in range(64))
            device.joinpath('onu_state.new').write_bytes(data);os.replace(device/'onu_state.new',device/'onu_state')
            device.joinpath('onu_table.new').write_text(''.join(f'{id} {(1<<18 if id in nodes else 0):08x} 00000001 0000002a '+
                (nodes[id].serial if id in nodes else bytes(8)).hex()+'\n' for id in range(64)))
            os.replace(device/'onu_table.new',device/'onu_table')
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
        def multicast_up():
            link=json.loads(subprocess.check_output(['ip','-j','link','show','dev','fttr-mc'],text=True))[0]
            return 'UP' in link['flags'] and link.get('master')=='br-fttr'
        def accel_events():
            return [tuple(map(int,line.split())) for line in accel_log.read_text().splitlines()]
        def until(predicate,seconds=8,allow_exit=False):
            nonlocal grant_buffer
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                if process.poll() is not None and not allow_exit:raise AssertionError(process.communicate())
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
                        if downstream_length:
                            assert struct.unpack('>H',frame[16:18])[0]==48 and frame[66:70]==b'\0\1\2\3'
                            body=frame[18:62]
                        else: body=frame[16:60]
                        wire=nodes[id].reply(body)
                        if wire:peer.send(HOST+OLT+struct.pack('>HH',0x8888,id)+wire)
                current=status()
                if args.auth and not args.move_control and not args.reboot_control and not args.upgrade_control and not args.onu_policy and (not current.get('onus') or current['onus'][7]['mib_serial']!=nodes[7].serial.hex()):
                    continue
                if (current.get('pid') == process.pid or (allow_exit and current.get('pid')==0 and process.poll() is not None)) and predicate(current):return current
            current=status()
            current['onus']=[r for r in current.get('onus',[]) if r['id'] in nodes]
            raise AssertionError(('state timeout',current,grants))
        hardware()
        upgrade_store=root/'upgrade-store';upgrade_store.mkdir(mode=0o700)
        service_store=root/'service-store';service_store.mkdir(mode=0o700)
        def launch(with_policy=True, with_wifi=None, grant_case='normal', warm_move=False):
            while True:
                try:peer.recv(4096)
                except BlockingIOError:break
            subprocess.run(['ip','link','set','dev','fap-host','up'],check=True)
            ni.joinpath('fttr_link').write_text('configured=1 mode=9 nway=1 clock=1 error=0 link=1\n')
            device.joinpath('tx_enable').write_text('1\n')
            optical=root/'optical'
            if warm_move:
                optical.mkdir(exist_ok=True)
                (optical/'calibration').write_text('configured=1 model=GN25L99 marker=b0 writes=128 error=0\n')
                (optical/'maintenance').write_text('supported=0 interval_ms=0\n')
                if warm_move=='lost-link':ni.joinpath('fttr_link').write_text('configured=0 mode=0 nway=0 clock=0 error=0 link=0\n')
            return subprocess.Popen([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host']+
                ([str(optical),'9','1','3'] if warm_move else []) +
                (['--upgrade-state',str(upgrade_store)] if args.upgrade_control else []) +
                (['--service-state',str(service_store)] if args.service_state else []) +
                (['--services',str(policy)] if with_policy else []) +
                (['--loid-auth',str(auth_policy)] if args.auth or args.onu_policy else []) +
                (['--onu-auth',str(onu_policy)] if args.onu_policy else []) +
                (['--wifi',str(wifi_policy)] if (args.wifi if with_wifi is None else with_wifi) else []),
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,
                env=dict(os.environ,LD_PRELOAD=str(root/'sysfs.so'),FTTR_SERVICE_CASE=grant_case,
                    FTTR_ACCEL_LOG=str(accel_log),FTTR_ACCEL_FAIL=str(accel_fault),
                    FTTR_ADMIN_FAIL=str(admin_fault),FTTR_ADMIN_HOLD=str(admin_hold),FTTR_ADMIN_LOG=str(admin_log),FTTR_MOVE_CRASH=str(root/'move-crash'),
                    **({'FTTR_ADMIN_FPGA':'1'} if args.backend=='fpga1' else {})))
        if args.service_state:
            import copy
            assert args.move_control and not args.wifi
            checks=[];process=None
            nodes[7].hold=False;nodes[9].fail_upload_once=False
            if args.shared:
                kind='shared-ctc' if args.multicast=='ctc' else 'shared-mcast' if args.multicast else 'shared'
                policy.write_text(f'* {kind} 0 1 3 200 50 100 200 br-fttr\n')
                if args.multi_services:
                    policy.write_text(''.join(f'* {kind if slot==1 else "shared"} 0 {slot} 3 filter:{100*slot} 50 100 200 br-fttr\n' for slot in range(1,args.multi_services+1)))
            originals={i:copy.deepcopy((n.pool,n.table)) for i,n in nodes.items()}
            def stop():
                if process.poll() is None:process.send_signal(signal.SIGTERM)
                out,err=process.communicate(timeout=4)
                assert process.returncode==0,err
            def start_ready():
                until(lambda s:all(s['onus'][i]['datapath_configured'] for i in (7,9)))
                if args.multicast:until(lambda s:s['multicast']['configured'])
            def mutation(n):
                return [(op,c,i) for _,op,c,i in n.operations if op in (0x44,0x46,0x48) and c not in (65530,256)]
            if args.move_recovery:
                def snapshot():
                    return {i:copy.deepcopy((n.pool,n.table)) for i,n in nodes.items()}
                def crash_wait():
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        if Path(f'/proc/{process.pid}/status').read_text().split('State:')[1].lstrip().startswith('T'):return
                        until(lambda s:Path(f'/proc/{process.pid}/status').read_text().split('State:')[1].lstrip().startswith('T'),seconds=1)
                    raise AssertionError('driver crash point not reached')
                try:
                    process=launch();start_ready();remote=snapshot()
                    ledgers={p.name:p.read_bytes() for p in service_store.iterdir()}
                    stop()
                    for mode in ('release','removed-policy','intent','before','after','partial','target-conflict','auth-fail','auth-before','missing-owner','stage-fail','store-fail','inventory-before','inventory-after','lost-link','half-target','blocked-source','half-source','partial-conflict'):
                        for i,n in nodes.items():n.pool,n.table=copy.deepcopy(remote[i]);n.move_hold=False;n.held=None
                        for p in service_store.iterdir():
                            if p.is_dir():p.rmdir()
                            else:p.unlink()
                        for name,data in ledgers.items():
                            p=service_store/name;p.write_bytes(data);p.chmod(0o600)
                        inventory=mode.startswith('inventory-')
                        if inventory:
                            for i,n in nodes.items():n.pool,n.table=copy.deepcopy(originals[i])
                            for p in service_store.iterdir():p.unlink()
                        hardware();admin_log.write_text('');process=launch(with_policy=not inventory)
                        if inventory:until(lambda s:len(s.get('onus',[]))>9 and all(s['onus'][i]['phase']=='inventory' for i in (7,9)))
                        else:start_ready()
                        count=len(mutation(nodes[7]));nodes[7].move_hold=mode in ('release','removed-policy','intent','target-conflict','auth-fail','missing-owner','stage-fail','lost-link')
                        crash=root/'move-crash'
                        if mode in ('before','after','partial','auth-before','inventory-before','inventory-after','half-target','blocked-source','half-source','partial-conflict'):crash.write_text({'before':'1','after':'2','partial':'3','auth-before':'1','inventory-before':'1','inventory-after':'2','half-target':'4','blocked-source':'5','half-source':'6','partial-conflict':'6'}[mode])
                        if mode=='store-fail':(service_store/'move.new').mkdir()
                        client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move','7',nodes[7].serial.hex(),'11','0'*16],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                        try:
                            if mode=='store-fail':
                                until(lambda s:process.poll() is not None,allow_exit=True)
                                assert not (service_store/'move').exists() and len(mutation(nodes[7]))==count
                                checks.append('move intent persistence failure precedes remote cleanup and driver mutation');continue
                            if nodes[7].move_hold:until(lambda s:nodes[7].held is not None)
                            else:crash_wait()
                            assert (service_store/'move').exists()
                            process.kill();process.wait()
                            if mode=='intent':
                                # Crash after move intent persisted but before
                                # release journal publication: no remote write.
                                for i,n in nodes.items():n.pool,n.table=copy.deepcopy(remote[i])
                                for name,data in ledgers.items():(service_store/name).write_bytes(data)
                        finally:
                            if client.poll() is None:client.kill();client.wait()
                        if crash.exists():crash.unlink()
                        nodes[7].move_hold=False
                        if mode in ('target-conflict','partial-conflict'):
                            data=bytearray(device.joinpath('onu_state').read_bytes());struct.pack_into('<I',data,11*20,1<<18);data[11*20+12:12*20]=b'OTHER011';device.joinpath('onu_state').write_bytes(data)
                        if mode in ('auth-fail','auth-before'):nodes[7].pool[65530,0][0x4000]=b'wrong'.ljust(24,b'\0')
                        if mode=='stage-fail':(service_store/'move.new').mkdir()
                        if mode=='missing-owner':(service_store/(nodes[7].serial.hex()+'.service')).unlink()
                        count=len(mutation(nodes[7]));process=launch(with_policy=mode!='removed-policy' and not inventory,warm_move='lost-link' if mode=='lost-link' else mode in ('before','after','auth-before','inventory-before','inventory-after'))
                        if mode in ('auth-fail','auth-before'):
                            until(lambda s:len(s.get('onus',[]))>7 and s['onus'][7]['phase']=='auth-failed')
                            assert (service_store/'move').exists() and len(mutation(nodes[7]))==count and 'move ' not in admin_log.read_text()
                            stop()
                        elif mode=='stage-fail':
                            until(lambda s:process.poll() is not None,allow_exit=True)
                            assert process.returncode and (service_store/'move').read_bytes()[20]==1 and 'move ' not in admin_log.read_text()
                        elif mode in ('target-conflict','partial-conflict','missing-owner','lost-link'):
                            until(lambda s:process.poll() is not None,allow_exit=True)
                            assert process.returncode and (service_store/'move').exists() and len(mutation(nodes[7]))==count
                            assert 'move ' not in admin_log.read_text()
                        else:
                            until(lambda s:'move 7 11' in admin_log.read_text() and not (service_store/'move').exists())
                            assert admin_log.read_text().count('move 7 11')==1
                            assert not (service_store/(nodes[7].serial.hex()+'.service')).exists()
                            data=device.joinpath('onu_state').read_bytes()
                            assert data[7*20+12:8*20]==bytes(8) and data[11*20+12:12*20]==nodes[7].serial
                            assert 'fttr-u7' not in ports() and 'fttr-u11' not in ports()
                            if mode in ('before','after','partial','half-target','blocked-source','half-source') or inventory:assert len(mutation(nodes[7]))==count
                            stop()
                        checks.append('durable migration '+mode)
                finally:
                    if process is not None and process.poll() is None:process.kill();process.wait()
                    peer.close();os.close(grant_fd)
                args.output.write_text(json.dumps(dict(backend=args.backend,checks=checks,scope='Actual daemon/CLI and Linux network namespace; original OMCI encoders, modeled MiniOLT and remote MIB. No device or flash power-loss acceptance.'),indent=2)+'\n')
                print(json.dumps(checks,indent=2));return
            try:
                process=launch();start_ready()
                saved={p.name:p.read_bytes() for p in service_store.glob('*.service')}
                assert len(saved)==2 and all(b[26]==1 for b in saved.values())
                counts={i:len(mutation(n)) for i,n in nodes.items()}
                process.kill();process.wait()
                process=launch();start_ready()
                assert all(len(mutation(n))==counts[i] for i,n in nodes.items())
                assert saved=={p.name:p.read_bytes() for p in service_store.glob('*.service')}
                checks.append('SIGKILL and restart read back all remote objects/tables, restore local grants and ports without remote mutation or ownership loss')
                stop()
                # Same serial, changed remote selected field: no repair or delete.
                key=next(k for k in nodes[7].pool if k[0]==268 and k[1]!=4095)
                attr=next(iter(nodes[7].pool[key]));old=nodes[7].pool[key][attr]
                nodes[7].pool[key][attr]=bytes([old[0]^1])+old[1:]
                process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'])
                assert 'fttr-u7' not in ports() and len(mutation(nodes[7]))==counts[7]
                stop();nodes[7].pool[key][attr]=old
                checks.append('remote drift declines recovery without overwriting the foreign change or the durable owner')
                # Policy mismatch must not silently rewrite a committed recipe.
                old_policy=policy.read_text();policy.write_text(old_policy.replace('50 100 200','51 100 200'))
                process=launch();until(lambda s:all(s['onus'][i]['phase']=='service-failed' for i in (7,9)))
                assert 'fttr-u7' not in ports() and 'fttr-u9' not in ports()
                stop();policy.write_text(old_policy)
                checks.append('changed policy is rejected before remote or local service installation')
                # A complete reset to original MIB defaults is distinguishable
                # from partial drift; it permits a fresh normal transaction.
                nodes[7].pool,nodes[7].table=copy.deepcopy(originals[7])
                nodes[7].counter.value=0
                process=launch();start_ready();stop()
                counts[7]=len(mutation(nodes[7]))
                checks.append('complete same-serial MIB reset retires the old record and provisions a new owned service; partial drift retains it')
                if args.shared:
                    nodes[7].pool[11,0x777]={0x8000:b'\0',0x0800:b'\1'}
                    process=launch();until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'])
                    assert 'fttr-u7' not in ports() and len(mutation(nodes[7]))==counts[7]
                    stop();del nodes[7].pool[11,0x777]
                    checks.append('changed complete UNI inventory cannot reopen a stale shared recipe')
                    saved_unis={k:v for k,v in nodes[7].pool.items() if k[0] in (11,329)}
                    for k in saved_unis:del nodes[7].pool[k]
                    original_record=(service_store/(nodes[7].serial.hex()+'.service')).read_bytes()
                    process=launch();until(lambda s:s['onus'][7]['phase']=='service-failed')
                    assert status()['onus'][7]['service_error']==-errno.ENOENT and 'fttr-u7' not in ports()
                    assert (service_store/(nodes[7].serial.hex()+'.service')).read_bytes()==original_record
                    stop();nodes[7].pool.update(saved_unis)
                    checks.append('planner ENOENT cannot be mistaken for an absent journal or overwrite existing ownership')
                process=launch(grant_case='grant-false-success')
                until(lambda s:process.poll() is not None or s['onus'][7]['phase']=='service-failed',allow_exit=True)
                assert 'fttr-u7' not in ports() and (service_store/(nodes[7].serial.hex()+'.service')).read_bytes()[26]==1
                if process.poll() is None:stop()
                else:
                    out,err=process.communicate(timeout=1);assert process.returncode==1 and '(-117)' in err,err
                checks.append('failed local grant restoration withdraws partial grants and keeps the committed remote ledger')
                process=launch();start_ready()
                # A release that crashes must retain its complete original
                # recipe but cannot be advertised as a fully active commit.
                old_owner=(service_store/(nodes[7].serial.hex()+'.service')).read_bytes()
                old_remote=copy.deepcopy((nodes[7].pool,nodes[7].table))
                nodes[7].move_hold=True;nodes[7].held=None
                interrupted=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move','7',nodes[7].serial.hex(),'11','0'*16],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    until(lambda s:nodes[7].held is not None)
                    pending=(service_store/(nodes[7].serial.hex()+'.service')).read_bytes()
                    assert pending[26]==2 and pending[32:-4]==old_owner[32:-4]
                    process.kill();process.wait()
                finally:
                    if interrupted.poll() is None:interrupted.kill();interrupted.wait()
                # Exercise compatibility with older standalone release records.
                (service_store/'move').unlink()
                nodes[7].move_hold=False;before_partial=len(mutation(nodes[7]))
                partial_remote=copy.deepcopy((nodes[7].pool,nodes[7].table))
                foreign_key=(268,4095) if args.multicast else (262,nodes[7].tcont)
                foreign_attr=next(iter(nodes[7].pool[foreign_key]))
                foreign_value=nodes[7].pool[foreign_key][foreign_attr]
                foreign_value=bytes([foreign_value[0]^1])+foreign_value[1:]
                nodes[7].pool[foreign_key][foreign_attr]=foreign_value
                process=launch();until(lambda s:s['onus'][7]['phase']=='service-failed')
                assert nodes[7].pool[foreign_key][foreign_attr]==foreign_value and 'fttr-u7' not in ports()
                assert (service_store/(nodes[7].serial.hex()+'.service')).read_bytes()==pending
                stop();nodes[7].pool,nodes[7].table=partial_remote
                checks.append('foreign attributes during interrupted release stop cleanup without overwriting the foreign value or losing persistent ownership')
                process=launch();until(lambda s:s['onus'][7]['phase']=='service-released')
                assert 'fttr-u7' not in ports()
                assert {k:v for k,v in nodes[7].pool.items() if k[0] not in (65530,256)}=={k:v for k,v in originals[7][0].items() if k[0] not in (65530,256)} and nodes[7].table==originals[7][1]
                assert (service_store/(nodes[7].serial.hex()+'.service')).read_bytes()==pending
                stop()
                count=len(mutation(nodes[7]))
                process=launch();until(lambda s:s['onus'][7]['phase']=='service-released')
                assert len(mutation(nodes[7]))==count and 'fttr-u7' not in ports()
                stop()
                # Restore the fixture only to test ordinary complete migration.
                nodes[7].pool,nodes[7].table=copy.deepcopy(old_remote)
                (service_store/(nodes[7].serial.hex()+'.service')).write_bytes(old_owner)
                checks.append('crash during release resumes checked cleanup, preserves closed hold and repeats read-only after completion')
                process=launch();start_ready()
                release_start=len(mutation(nodes[7]))
                client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move','7',nodes[7].serial.hex(),'11','0'*16],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    until(lambda s:client.poll() is not None,seconds=25)
                    out,err=client.communicate(timeout=1);assert client.returncode==0,(out,err)
                finally:
                    if client.poll() is None:client.kill();client.wait()
                assert not (service_store/(nodes[7].serial.hex()+'.service')).exists()
                assert {k:v for k,v in nodes[7].pool.items() if k[0] not in (65530,256)}=={k:v for k,v in originals[7][0].items() if k[0] not in (65530,256)} and nodes[7].table==originals[7][1]
                checks.append('post-restart move restores original T-CONT/UNI/VLAN state and deletes only owned shared GEM/multicast objects; durable record retires after release')
                writes=len(mutation(nodes[7]))-release_start
                stop()
                if args.release_cuts:
                    for cut in range(1,writes+1):
                        nodes[7].pool,nodes[7].table=copy.deepcopy(old_remote)
                        nodes[7].release_cut=nodes[7].release_writes=0;nodes[7].held=None
                        (service_store/(nodes[7].serial.hex()+'.service')).write_bytes(old_owner)
                        (service_store/(nodes[7].serial.hex()+'.service')).chmod(0o600)
                        hardware();process=launch();start_ready()
                        nodes[7].release_cut=cut
                        client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move','7',nodes[7].serial.hex(),'11','0'*16],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                        try:
                            until(lambda s:nodes[7].held is not None)
                            process.kill();process.wait()
                        finally:
                            if client.poll() is None:client.kill();client.wait()
                        (service_store/'move').unlink()
                        nodes[7].release_cut=0
                        process=launch(with_policy=cut!=writes)
                        until(lambda s:s['onus'][7]['phase']=='service-released')
                        assert 'fttr-u7' not in ports()
                        assert {k:v for k,v in nodes[7].pool.items() if k[0] not in (65530,256)}=={k:v for k,v in originals[7][0].items() if k[0] not in (65530,256)} and nodes[7].table==originals[7][1],cut
                        stop()
                    checks.append(f'every one of {writes} applied Set/Delete crash points recovers original objects/VLANs, including policy removal; no automatic service reopen')
                    # Only explicit resume after completed cleanup releases hold.
                    process=launch();until(lambda s:s['onus'][7]['phase']=='service-released')
                    client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-admin','7',nodes[7].serial.hex(),'resume'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    try:
                        until(lambda s:client.poll() is not None)
                        out,err=client.communicate(timeout=1);assert client.returncode==0,(out,err)
                    finally:
                        if client.poll() is None:client.kill();client.wait()
                    assert not (service_store/(nodes[7].serial.hex()+'.service')).exists()
                    if args.backend!='asic9690':hardware() # FPGA resume releases registration; model its new discovery.
                    start_ready();stop()
                    checks.append('explicit resume after cleanup removes hold only after locked driver identity confirmation and reprovisions normally')
                # A power loss during initial Set leaves durable uncertainty.
                nodes[7]=Node(7,'5445535400000007',0x107,False,hold=True)
                nodes[9]=Node(9,'5445535400000009',0x203,True);nodes[9].fail_upload_once=False
                for p in service_store.iterdir():p.unlink()
                hardware();process=launch()
                until(lambda s:nodes[7].held is not None)
                record=service_store/(nodes[7].serial.hex()+'.service')
                assert record.read_bytes()[26]==0
                process.kill();process.wait();nodes[7].hold=False
                count=len(mutation(nodes[7]));process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed')
                assert len(mutation(nodes[7]))==count and 'fttr-u7' not in ports() and record.read_bytes()[26]==0
                stop()
                checks.append('power loss after a remote write but before complete commit preserves pending state and refuses automatic replay/adoption')
                # Cannot durably mark intent: no remote service mutation.
                for p in service_store.iterdir():p.unlink()
                nodes[7]=Node(7,'5445535400000007',0x107,False);nodes[9]=Node(9,'5445535400000009',0x203,True)
                nodes[9].fail_upload_once=False;hardware()
                (service_store/(nodes[7].serial.hex()+'.service.new')).mkdir()
                process=launch();until(lambda s:process.poll() is not None,allow_exit=True)
                assert process.returncode and not mutation(nodes[7])
                checks.append('intent persistence failure stops before service Set/Create or local data admission')
            finally:
                if process is not None and process.poll() is None:process.kill();process.wait()
                peer.close();os.close(grant_fd)
            args.output.write_text(json.dumps(dict(backend=args.backend,checks=checks,scope='Real daemon/CLI and original OMCI encoders with modeled remote MIB and MiniOLT; no device access.'),indent=2)+'\n')
            print(json.dumps(checks,indent=2));return
        if args.upgrade_control:
            checks=[];process=None
            image=root/'image.bin';image.write_bytes(bytes(i%251 for i in range(1923)))
            def command(action='download',serial=None):
                return subprocess.Popen([str(args.upgrade_control.resolve()),str(store),'onu-upgrade','7',
                    nodes[7].serial.hex() if serial is None else serial,action]+([str(image)] if action=='download' else []),
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            def finish(client,error=0,seconds=8):
                until(lambda s:client.poll() is not None,seconds=seconds)
                stdout,stderr=client.communicate(timeout=2)
                assert client.returncode==bool(error),(stdout,stderr)
                if error:assert f'({-error})' in stderr,(stdout,stderr)
                if stdout:assert json.loads(stdout)['boot_verified'] is False
            def stop():
                process.send_signal(signal.SIGTERM)
                until(lambda s:process.poll() is not None,allow_exit=True)
                assert process.wait(timeout=2)==0,process.stderr.read()
            try:
                for mode in ('ok','reject-window','reject-end','abort','replace','shutdown','client-gone','peer-admin','withdraw-fail','drop-start','snapshot','crash-download','crash-activate','crash-commit','store-fail','verify-store-fail','resume-io','resume-stale','held-deactivate'):
                    while select.select([peer],[],[],0)[0]:peer.recv(2048)
                    nodes[7]=Node(7,'5445535400000007',0x107,False)
                    nodes[9]=Node(9,'5445535400000009',0x203,True)
                    for receipt in upgrade_store.iterdir():receipt.unlink()
                    nodes[7].upgrade_mode='hold' if mode in ('abort','replace','shutdown','peer-admin','crash-download') else mode
                    nodes[7].upgrade_expected=image.read_bytes()
                    hardware();process=launch()
                    until(lambda s:all(s['onus'][id]['phase']=='configured' for id in (7,9)))
                    healthy=datapath(9);before=len(accel_events())
                    finish(command(serial='00'*8),errno.ESTALE);assert len(accel_events())==before
                    # Version3 requires one FD only for download and rejects extra descriptors.
                    control=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);control.bind('');control.settimeout(2);control.connect(str(store/'control.sock'))
                    request=struct.pack('III8sI8s',3,7,5,nodes[7].serial,0,bytes(8))
                    control.send(request);assert struct.unpack('i',control.recv(4))[0]==-errno.EINVAL
                    with image.open('rb') as stream:
                        control.sendmsg([request],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,struct.pack('ii',stream.fileno(),stream.fileno()))])
                        assert struct.unpack('i',control.recv(4))[0]==-errno.EINVAL
                    for bad in (root/'empty-image',root/'pipe-image'):
                        if bad.name=='empty-image':bad.write_bytes(b'')
                        else:os.mkfifo(bad)
                        descriptor=os.open(bad,os.O_RDONLY|os.O_NONBLOCK)
                        control.sendmsg([request],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,struct.pack('i',descriptor))])
                        os.close(descriptor)
                        assert struct.unpack('i',control.recv(4))[0]==-errno.EINVAL
                        bad.unlink()
                    control.close();assert len(accel_events())==before
                    if mode=='store-fail':
                        # Staging creation must fail before any download mutation.
                        (upgrade_store/(nodes[7].serial.hex()+'.upgrade.new')).mkdir()
                    if mode=='withdraw-fail':accel_fault.touch()
                    client=command()
                    if mode in ('withdraw-fail','store-fail'):
                        until(lambda s:process.poll() is not None,allow_exit=True)
                        stdout,stderr=client.communicate(timeout=2)
                        assert process.wait(timeout=2)==1 and client.returncode==1
                        assert f'({-errno.ETIMEDOUT if mode=="withdraw-fail" else -errno.EISDIR})' in stderr
                        assert not any(op in (0x53,0x14,0x54,0x55,0x56,0x57) for _,op,_,_ in nodes[7].operations)
                        assert not any(v for v,_,_ in accel_events()[before:])
                        if mode=='store-fail':
                            (upgrade_store/(nodes[7].serial.hex()+'.upgrade.new')).rmdir();process=None;checks.append(mode);continue
                        assert not any(c==7 for _,_,c,_ in nodes[7].operations)
                        accel_fault.unlink();process=None;checks.append(mode);continue
                    until(lambda s:any(op==0x53 for _,op,_,_ in nodes[7].operations))
                    assert datapath(9)==healthy and 'fttr-u7' not in ports()
                    if mode=='snapshot':image.write_bytes(b'changed after controller snapshot')
                    if mode=='client-gone':client.kill();client.wait()
                    if mode in ('abort','replace','shutdown','peer-admin','crash-download'):
                        until(lambda s:any(op==0x54 for _,op,_,_ in nodes[7].operations))
                        if mode=='crash-download':
                            process.kill();process.wait();client.kill();client.wait()
                            before_ops=len(nodes[7].operations)
                            process=launch()
                            until(lambda s:s['onus'][7]['phase']=='upgrade-wait' and s['onus'][9]['phase']=='configured')
                            assert not nodes[7].operations[before_ops:] and 'fttr-u7' not in ports()
                            finish(command('download'),errno.EALREADY);finish(command('check'),errno.ENODATA)
                            result=subprocess.run([str(args.upgrade_control.resolve()),str(store),'onu-admin','7',nodes[7].serial.hex(),'resume'],capture_output=True,text=True,timeout=3)
                            assert result.returncode==0,(result.stdout,result.stderr)
                            if args.backend=='fpga1':hardware()
                            until(lambda s:s['onus'][7]['phase']=='configured')
                            assert not list(upgrade_store.iterdir())
                        elif mode=='peer-admin':
                            # Other ONU control may close, but must not reopen the shared gate.
                            result=subprocess.run([str(args.upgrade_control.resolve()),str(store),'onu-admin','9',nodes[9].serial.hex(),'block'],capture_output=True,text=True,timeout=3)
                            assert result.returncode==0,(result.stdout,result.stderr)
                            assert ni.joinpath('fttr_forward').read_text().startswith('0')
                            assert not any(v for v,_,_ in accel_events()[before:])
                            finish(command('abort'));finish(client,errno.ECANCELED)
                        elif mode=='abort':finish(command('abort'));finish(client,errno.ECANCELED)
                        elif mode=='replace':
                            nodes[7]=Node(7,'5445535400000077',0x307,True);hardware()
                            finish(client,errno.ESTALE);until(lambda s:s['onus'][7]['phase']=='configured')
                        else:
                            process.send_signal(signal.SIGTERM);until(lambda s:process.poll() is not None,allow_exit=True)
                            stdout,stderr=client.communicate(timeout=2)
                            assert client.returncode==1 and f'({-errno.ECANCELED})' in stderr
                            assert process.wait(timeout=2)==0;process=None;checks.append(mode);continue
                    elif mode=='verify-store-fail':
                        until(lambda s:process.poll() is not None,allow_exit=True)
                        stdout,stderr=client.communicate(timeout=2)
                        assert process.wait(timeout=2)==1 and client.returncode==1
                        assert f'({-errno.EISDIR})' in stderr
                        assert not any(v for v,_,_ in accel_events()[before:])
                        assert (upgrade_store/(nodes[7].serial.hex()+'.upgrade')).read_bytes()[42]==1
                        (upgrade_store/(nodes[7].serial.hex()+'.upgrade.new')).rmdir()
                        process=None;checks.append(mode);continue
                    elif mode=='drop-start':
                        finish(client,errno.ETIMEDOUT,seconds=35)
                        assert sum(op==0x53 for _,op,_,_ in nodes[7].operations)==1
                        assert not any(op in (0x14,0x54,0x55,0x56,0x57) for _,op,_,_ in nodes[7].operations)
                    else:
                        if mode!='client-gone':finish(client,seconds=12)
                        until(lambda s:s['onus'][7]['phase']=='upgrade-wait')
                        assert status()['onus'][7]['upgrade_verified'] and nodes[7].upgrade_data==nodes[7].upgrade_expected
                        assert datapath(9)==healthy and 'fttr-u7' not in ports()
                        if mode in ('resume-io','resume-stale','held-deactivate'):
                            receipt=upgrade_store/(nodes[7].serial.hex()+'.upgrade');saved=receipt.read_bytes()
                            def admin(action):
                                return subprocess.run([str(args.upgrade_control.resolve()),str(store),'onu-admin','7',nodes[7].serial.hex(),action],capture_output=True,text=True,timeout=3)
                            if mode=='held-deactivate':
                                result=admin('deactivate');assert result.returncode==0,(result.stdout,result.stderr)
                                until(lambda s:not s['onus'][7]['temporary_deactivation'],seconds=18)
                                if args.backend=='fpga1':hardware()
                                until(lambda s:s['onus'][7]['phase']=='upgrade-wait')
                                assert receipt.read_bytes()==saved and 'fttr-u7' not in ports()
                            else:
                                admin_fault.write_text('io' if mode=='resume-io' else 'stale')
                                result=admin('resume');expected=errno.EIO if mode=='resume-io' else errno.ESTALE
                                assert result.returncode==1 and f'({-expected})' in result.stderr,(result.stdout,result.stderr)
                                assert receipt.read_bytes()==saved
                                admin_fault.unlink()
                                if mode=='resume-io':
                                    until(lambda s:process.poll() is not None,allow_exit=True);assert process.wait()==1
                                    process=launch()
                                until(lambda s:s['onus'][7]['phase']=='upgrade-wait')
                                assert 'fttr-u7' not in ports()
                            result=admin('resume');assert result.returncode==0,(result.stdout,result.stderr)
                            if args.backend=='fpga1':hardware()
                            until(lambda s:s['onus'][7]['phase']=='configured')
                            assert not receipt.exists()
                        if mode in ('ok','crash-activate','crash-commit'):
                            if mode.startswith('crash-'):nodes[7].upgrade_lost='activate'
                        if mode=='ok':
                            finish(command('activate'))
                            assert status()['onus'][7]['upgrade_accepted'] and not status()['onus'][7]['upgrade_verified']
                            finish(command('check'))
                            finish(command('commit'))
                            assert status()['onus'][7]['upgrade_verified']
                        if mode in ('crash-activate','crash-commit'):
                            activate=command('activate')
                            until(lambda s:any(op==0x56 for _,op,_,_ in nodes[7].operations))
                            process.kill();process.wait();activate.kill();activate.wait()
                            nodes[7].upgrade_lost='';before_ops=len(nodes[7].operations)
                            if mode=='crash-activate':
                                receipt=upgrade_store/(nodes[7].serial.hex()+'.upgrade');saved=receipt.read_bytes()
                                for mismatch in ('corrupt','other-controller'):
                                    changed=bytearray(saved);changed[12]^=1
                                    if mismatch=='other-controller':changed[60:64]=struct.pack('>I',crc(changed[:60]))
                                    receipt.write_bytes(changed);process=launch()
                                    until(lambda s:process.poll() is not None,allow_exit=True)
                                    assert process.wait(timeout=2)==1
                                    assert not nodes[7].operations[before_ops:] and 'fttr-u7' not in ports()
                                receipt.write_bytes(saved)
                            process=launch()
                            until(lambda s:s['onus'][7]['phase']=='upgrade-wait' and s['onus'][9]['phase']=='configured')
                            assert not nodes[7].operations[before_ops:]
                            finish(command('activate'),errno.EALREADY)
                            nodes[7].upgrade_version=b'WRONGVERSION00'
                            finish(command('check'),errno.EUCLEAN)
                            nodes[7].upgrade_version=b'VERSION0000001'
                            finish(command('check'))
                            assert status()['onus'][7]['upgrade_verified'] and 'fttr-u7' not in ports()
                            if mode=='crash-commit':
                                nodes[7].upgrade_lost='commit';commit=command('commit')
                                until(lambda s:any(op==0x57 for _,op,_,_ in nodes[7].operations))
                                process.kill();process.wait();commit.kill();commit.wait()
                                nodes[7].upgrade_lost='';before_ops=len(nodes[7].operations);process=launch()
                                until(lambda s:s['onus'][7]['phase']=='upgrade-wait' and s['onus'][9]['phase']=='configured')
                                assert not nodes[7].operations[before_ops:]
                                finish(command('check'));assert status()['onus'][7]['upgrade_verified']
                    stop();process=None;checks.append(mode)
            finally:
                if process is not None and process.poll() is None:process.kill();process.wait()
                peer.close();os.close(grant_fd)
            args.output.write_text(json.dumps(dict(checks=checks,backend=args.backend,
                scope='Actual resident/CLI/FD handoff/veth/VLAN with modeled image storage and hardware; no flash or physical reboot.'),indent=2)+'\n')
            print(json.dumps(checks));return
        if args.reboot_control:
            checks=[]
            def command(action='reboot',serial=None):
                return subprocess.Popen([str(args.reboot_control.resolve()),str(store),'onu-admin','7',
                    nodes[7].serial.hex() if serial is None else serial,action],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            def result(client,error=0):
                until(lambda s:client.poll() is not None)
                stdout,stderr=client.communicate(timeout=2)
                assert client.returncode==bool(error),(stdout,stderr)
                if error:assert f'({-error})' in stderr,(error,stdout,stderr)
                return json.loads(stdout) if stdout else None
            def stop():
                process.send_signal(signal.SIGTERM)
                until(lambda s:process.poll() is not None,allow_exit=True)
                assert process.wait(timeout=2)==0,process.stderr.read()
            process=None
            try:
                for mode in ('ack','drop','reject','malformed','replace','client-gone','shutdown','finish-fail','withdraw-fail'):
                    while select.select([peer],[],[],0)[0]:peer.recv(2048)
                    nodes[7]=Node(7,'5445535400000007',0x107,False)
                    nodes[9]=Node(9,'5445535400000009',0x203,True)
                    nodes[7].reboot_mode='hold' if mode in ('replace','client-gone','shutdown','finish-fail') else mode
                    hardware();process=launch()
                    until(lambda s:all(s['onus'][id]['phase']=='configured' for id in (7,9)))
                    old_port=datapath(7);peer_port=datapath(9);before=len(accel_events())
                    result(command(serial='00'*8),errno.ESTALE)
                    assert len(accel_events())==before
                    if mode=='withdraw-fail':accel_fault.touch()
                    management_snapshot=device.joinpath('management_grant').read_bytes() if args.backend!='fpga1' else None
                    client=command()
                    if mode=='withdraw-fail':
                        until(lambda s:process.poll() is not None,allow_exit=True)
                        assert process.wait(timeout=2)==1
                        stdout,stderr=client.communicate(timeout=3)
                        assert client.returncode==1 and f'({-errno.ETIMEDOUT})' in stderr
                        assert not any(op==0x59 for _,op,_,_ in nodes[7].operations)
                        assert not any(v for v,_,_ in accel_events()[before:])
                        accel_fault.unlink();process=None;continue
                    until(lambda s:any(op==0x59 for _,op,_,_ in nodes[7].operations))
                    assert datapath(9)==peer_port and 'fttr-u7' not in ports()
                    if mode=='replace':
                        result(command('block'),errno.EBUSY)
                        result(command(),errno.EBUSY)
                        nodes[7]=Node(7,'5445535400000077',0x307,True);hardware()
                        result(client,errno.ESTALE)
                        until(lambda s:s['onus'][7]['phase']=='configured')
                    elif mode=='client-gone':
                        client.kill();client.wait()
                        # Wrong TCI is unrelated; the original request stays pending.
                        wire=bytearray(nodes[7].held)
                        wire[1]^=1;wire[44:]=struct.pack('>I',crc(wire[:44]))
                        peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+wire)
                        nodes[7].reboot_mode='drop'
                        until(lambda s:s['onus'][7]['phase']=='reboot-wait')
                        assert status()['onus'][7]['reboot_error']==-errno.ETIMEDOUT
                        # A valid but late ACK cannot reopen service after timeout.
                        peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                        peer_before=len(nodes[9].operations)
                        until(lambda s:len(nodes[9].operations)>peer_before)
                        assert status()['onus'][7]['phase']=='reboot-wait'
                        assert sum(op==0x59 for _,op,_,_ in nodes[7].operations)==1
                        result(command('resume'))
                        if args.backend=='fpga1':hardware()
                        until(lambda s:s['onus'][7]['phase']=='configured')
                    elif mode=='finish-fail':
                        attribute='backend' if args.backend=='fpga1' else 'management_grant'
                        device.joinpath(attribute).rename(device/(attribute+'.saved'))
                        peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                        until(lambda s:process.poll() is not None,allow_exit=True)
                        stdout,stderr=client.communicate(timeout=2)
                        assert client.returncode==1 and f'({-errno.ENOENT})' in stderr
                        assert process.wait(timeout=2)==1
                        assert not any(v for v,_,_ in accel_events()[before:])
                        device.joinpath(attribute+'.saved').rename(device/attribute)
                        process=None;continue
                    elif mode=='shutdown':
                        process.send_signal(signal.SIGTERM)
                        until(lambda s:process.poll() is not None,allow_exit=True)
                        stdout,stderr=client.communicate(timeout=2)
                        assert client.returncode==1 and f'({-errno.ECANCELED})' in stderr
                        assert process.wait(timeout=2)==0,process.stderr.read()
                        assert not any(v for v,_,_ in accel_events()[before:])
                        process=None;continue
                    else:
                        error={'ack':0,'drop':errno.ETIMEDOUT,'reject':errno.EREMOTEIO,'malformed':errno.EPROTO}[mode]
                        row=result(client,error)
                        assert row['restart_verified'] is False
                        assert row['command_accepted']==(True if mode=='ack' else False if mode=='reject' else None)
                        assert sum(op==0x59 for _,op,_,_ in nodes[7].operations)==1
                        if mode=='reject':until(lambda s:s['onus'][7]['phase']=='configured')
                        else:
                            until(lambda s:s['onus'][7]['phase']=='reboot-wait')
                            result(command(),errno.EBUSY)
                            peer_before=len(nodes[9].operations)
                            until(lambda s:len(nodes[9].operations)>peer_before)
                            assert sum(op==0x59 for _,op,_,_ in nodes[7].operations)==1
                            assert 'fttr-u7' not in ports() and datapath(9)==peer_port
                            if mode=='ack':
                                # Observe departure before allowing fresh inventory/authentication.
                                saved=nodes.pop(7);hardware()
                                until(lambda s:s['onus'][7]['phase']=='absent')
                                nodes[7]=Node(7,saved.serial.hex(),0x107,False);hardware()
                            else:
                                result(command('resume'))
                                if args.backend=='fpga1':hardware()
                            until(lambda s:s['onus'][7]['phase']=='configured')
                            datapath(7)
                    stop();process=None
                    checks.append(mode)
                checks += ['shutdown','finish-fail','withdraw-fail']
            finally:
                if process is not None and process.poll() is None:process.kill();process.wait()
                peer.close();os.close(grant_fd)
            args.output.write_text(json.dumps(dict(checks=checks,backend=args.backend,auth=args.auth,
                scope='Actual resident, CLI, veth, bridge/VLAN and original reboot encoder; modeled ONU/MDIO/FE IO, no physical restart.'),indent=2)+'\n')
            print(json.dumps(checks));return
        if args.onu_policy:
            checks=[]
            auth_policy.write_bytes(b'TFL1'+bytes(38))
            onu_policy=root/'onus.json';onu_state=root/'onu-state';onu_state.mkdir(mode=0o700)
            revision=0
            def policy_row(id=8,method=1,serial='5445535400000007',active=True,loid='synthetic-loid',key='synthetic-pw'):
                return dict(id=id,method=method,serial=serial,active=active,ploam='0'*20,loid=loid,key=key)
            def configure(rows,learn=False,find=False,vendors=None):
                nonlocal revision
                revision+=1
                document=dict(version=1,revision=f'v{revision}',state_directory=str(onu_state),
                    auto_learn=learn,auto_find=find,vendors=['54455354'] if vendors is None else vendors,
                    onus=rows+[policy_row(id=10,serial='5445535400000009')])
                onu_policy.write_text(json.dumps(document));onu_policy.chmod(0o600)
            def reset():
                # Prior process requests can remain queued after SIGTERM.
                # They do not belong to the new synthetic registration.
                while True:
                    try: peer.recvfrom(2048)
                    except BlockingIOError: break
                nodes.clear();nodes[7]=Node(7,'5445535400000007',0x107,False)
                nodes[9]=Node(9,'5445535400000009',0x203,True)
                admin_log.write_text('');hardware()
            def stop():
                process.send_signal(signal.SIGTERM);_,errors=process.communicate(timeout=3)
                assert process.returncode==0,errors
                assert not ports() and not any(r['onu_authenticated'] for r in status()['onus'])
            def accepted(ids=(7,9)):
                until(lambda s:all(s['onus'][id]['datapath_configured'] for id in ids))
                for id in ids:
                    row=status()['onus'][id]
                    assert row['onu_authenticated'] and row['onu_auth_required']
                    assert row['loid_authenticated']==row['loid_auth_required']
            def denied(error,id=7):
                until(lambda s:s['onus'][id]['phase']=='auth-failed')
                row=status()['onus'][id]
                assert row['error']==error,(row,error)
                assert not row['onu_authenticated'] and not row['datapath_configured'] and not row['wifi_configured']
                assert not any(op in (0x44,0x48) and c not in (256,65530) for _,op,c,_ in nodes[id].operations)
                assert device.joinpath('tcont_state').read_bytes()[(id*8+physical_slot)*8:(id*8+physical_slot+1)*8]==bytes(8)
            process=None
            try:
                reset();configure([policy_row()])
                good=onu_policy.read_text()
                for invalid,mode,duplicate,backend in [('{}',0o600,False,args.backend),
                        (good,0o644,False,args.backend),(good,0o600,True,args.backend),(good,0o600,False,'invalid')]:
                    onu_policy.write_text(invalid);onu_policy.chmod(mode);device.joinpath('backend').write_text(backend+'\n')
                    rejected=subprocess.run([str(args.daemon.resolve()),str(device),str(ni),str(store),'fap-host']+
                        ['--onu-auth',str(onu_policy)]*(2 if duplicate else 1),capture_output=True,text=True,timeout=2)
                    assert rejected.returncode==1 and not store.joinpath('status.json').exists()
                    assert ni.joinpath('fttr_forward').read_text()=='0\n' and device.joinpath('tx_enable').read_text()=='1\n'
                onu_policy.write_text(good);onu_policy.chmod(0o600);device.joinpath('backend').write_text(args.backend+'\n')
                checks.append('malformed/private-mode/duplicate policies and invalid backend fail before hardware ownership')
                if args.backend=='fpga2':
                    reset();configure([policy_row()]);policy.write_text('* 0 0 7 3 none 50 100 200 br-fttr\n')
                    process=launch();until(lambda s:s['onus'][7]['datapath_configured'])
                    assert nodes[7].pool[262,nodes[7].tcont][0x8000]==struct.pack('>H',256+7*8+7)
                    assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+7)*8)==(50,100<<16|200)
                    stop();policy.write_text('5445535400000007 0 0 1 3 none 50 100 200 br-fttr\n* 2 0 1 3 200 50 100 200 br-fttr\n')
                    checks.append('FPGA2 highest business slot7 binds the original Alloc-ID and distinct physical DBA slot')
                for method in (1,4,5):
                    reset();configure([policy_row(method=method)])
                    process=launch();accepted()
                    if method==1:assert not any(c in (256,65530) and op in (0x48,0x49) for _,op,c,_ in nodes[7].operations)
                    stop()
                checks.append('SN, LOID and LOID+key methods gate actual Wi-Fi/VLAN/DBA service creation; disabled global result stays unauthenticated')
                for method in (2,3):
                    reset();configure([policy_row(method=method)],learn=True,find=True)
                    process=launch();denied(-errno.ENODATA);accepted((9,));stop()
                checks.append('unavailable PLOAM credentials reject without fabricated credentials or auto-learning bypass; healthy SN peer continues')
                for rows,vendors,error in [([policy_row(active=False)],None,-errno.EACCES),
                        ([policy_row(method=5, key='wrong')],None,-errno.EACCES),
                        ([policy_row()],['454c5345'],-errno.EACCES)]:
                    reset();configure(rows,vendors=vendors);process=launch();denied(error);stop()
                checks.append('inactive row, wrong key and vendor filtering prohibit business configuration')
                reset();configure([policy_row(method=5,serial='0'*16)])
                process=launch();denied(-errno.EAGAIN)
                saved=onu_state/f'v{revision}.json'
                assert json.loads(saved.read_text())['bindings'][7]==nodes[7].serial.hex()
                accepted();stop()
                # Restart must retrieve credentials again; a durable SN is not authentication.
                reset();nodes[7].auth_hold=True;process=launch()
                until(lambda s:s['onus'][7]['phase']=='authentication' and nodes[7].held is not None)
                assert not status()['onus'][7]['onu_authenticated'] and 'fttr-u7' not in ports()
                nodes[7].auth_hold=False;peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                accepted();stop()
                reset();nodes[7].serial=bytes.fromhex('5445535400000088');hardware();process=launch();denied(-errno.EACCES);stop()
                checks.append('SN binding persists before retry, survives restart, requires fresh OMCI credentials and rejects replacement identity')
                reset();configure([policy_row(method=5)]);process=launch();accepted()
                previous=status()['onus'][7]['mib_rescans'];nodes[7].counter.value=0;nodes[7].auth_hold=True;nodes[7].held=None
                until(lambda s:s['onus'][7]['phase']=='authentication' and s['onus'][7]['mib_rescans']==previous+1)
                assert not status()['onus'][7]['onu_authenticated'] and 'fttr-u7' not in ports()
                until(lambda s:nodes[7].held is not None)
                stale=nodes[7].held
                nodes[7]=Node(7,'5445535400000088',0x107,False);hardware()
                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+stale)
                until(lambda s:s['onus'][7]['mib_serial']==nodes[7].serial.hex() and s['onus'][7]['phase']=='auth-failed')
                assert not status()['onus'][7]['onu_authenticated'] and not status()['onus'][7]['datapath_configured']
                stop();checks.append('same-SN MIB reset reauthenticates; stale replies cannot authorize a replacement SN')
                reset();configure([policy_row(method=5,serial='0'*16)])
                # Force persistence failure after policy load, before binding.
                nodes[7].auth_hold=True;process=launch()
                until(lambda s:nodes[7].held is not None)
                (onu_state/f'v{revision}.json.new').mkdir()
                nodes[7].auth_hold=False;peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                until(lambda s:s['state']=='failed',allow_exit=True)
                assert process.wait(timeout=3)==1 and not ports() and device.joinpath('tx_enable').read_text()=='0\n'
                assert json.loads((onu_state/f'v{revision}.json').read_text())['bindings'][7] is None
                checks.append('failed binding persistence stops TX and never leaves an authenticated runtime or durable SN assertion')
                for method in (4,5):
                    reset();configure([policy_row(method=method)])
                    nodes[7].auth_ctc_fail=0x2000;process=launch();accepted();stop()
                    reset();nodes[7].auth_ctc_fail=0x4000;nodes[7].auth_onug_fail=0x0020
                    process=launch();denied(-errno.EREMOTEIO);stop()
                checks.append('per-ONU-only LOID uses original CTC/ONU-G fallback; partial acquisition remains rejected')
                reset();configure([policy_row(method=1),policy_row(id=12,method=5,serial='0'*16,loid='other')])
                nodes[7].auth_ctc_fail=0x4000;nodes[7].auth_onug_fail=0x0040
                process=launch();accepted();stop()
                checks.append('an unrelated LOID rule does not block a proven SN-only peer lacking credential MEs')
                reset();configure([],learn=True,find=True);process=launch();denied(-errno.EAGAIN);accepted();stop()
                reset();process=launch();accepted()
                assert not any(c in (256,65530) and op in (0x48,0x49) for _,op,c,_ in nodes[7].operations)
                stop();checks.append('auto-find AND auto-learn persists method1 at the current free row and authenticates only on a new pass')
                # Exact and unbound wrong-slot matching uses the same owned move path.
                for method,serial in ((1,'5445535400000007'),(5,'0'*16)):
                    reset();configure([policy_row(id=12,method=method,serial=serial)])
                    process=launch()
                    until(lambda s:'move 7 11' in admin_log.read_text())
                    assert not any(op in (0x44,0x48) and c not in (256,65530) for _,op,c,_ in nodes[7].operations)
                    assert 'fttr-u7' not in ports() and 'fttr-u11' not in ports()
                    data=device.joinpath('onu_state').read_bytes()
                    assert data[11*20+12:12*20]==nodes[7].serial
                    node=nodes.pop(7);node.id=11;nodes[11]=node;hardware()
                    if method==5:denied(-errno.EAGAIN,11)
                    accepted((9,11));stop()
                checks.append('wrong-slot SN/LOID selection inventories without provisioning, migrates reservation and reauthenticates after external registration')
                # Refresh must retire the old high slot before selecting a newly observed low slot.
                reset();configure([policy_row(id=12)],learn=True,find=True)
                node=nodes.pop(7);node.id=11;nodes[11]=node;hardware()
                process=launch();accepted((9,11))
                del nodes[11];nodes[7]=Node(7,'5445535400000007',0x107,False);hardware()
                until(lambda s:'move 7 11' in admin_log.read_text())
                assert json.loads((onu_state/f'v{revision}.json').read_text())['bindings'][7] is None
                assert not any(op in (0x44,0x48) and c not in (256,65530) for _,op,c,_ in nodes[7].operations)
                stop();checks.append('same-scan departure invalidates the old live row before lower-slot selection, preserving the original SN assignment')
                # A legacy remote GEM blocks automatic migration before MiniOLT mutation.
                reset();configure([policy_row(id=12)])
                nodes[7].pool[268,77]={0x8000:b'\0\x77'}
                process=launch();denied(-errno.ENODATA)
                assert 'move' not in admin_log.read_text();accepted((9,));stop()
                checks.append('unowned remote GEM prevents automatic serial migration and leaves the healthy peer running')
                reset();configure([policy_row(id=12,method=5,serial='0'*16)])
                nodes[7].mib_hold=True;process=launch()
                until(lambda s:nodes[7].held is not None and s['onus'][7]['phase']=='mib')
                nodes[11]=Node(11,'5445535400000011',0x311,True);hardware()
                until(lambda s:s['onus'][11]['datapath_configured'])
                nodes[7].mib_hold=False;peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                denied(-errno.ESTALE)
                assert 'move' not in admin_log.read_text() and status()['onus'][11]['onu_authenticated']
                stop();checks.append('a target bound by another registration during source inventory invalidates the earlier move selection')
                reset();configure([policy_row(method=5)])
                auth_policy.write_bytes(b'TFL1'+b'synthetic-loid'.ljust(25,b'\0')+b'wrong'.ljust(13,b'\0'))
                process=launch();denied(-errno.EACCES)
                assert status()['onus'][7]['loid_result']==3 and status()['onus'][7]['loid_result_verified']
                stop();checks.append('per-ONU match still requires global LOID acceptance and verified result readback')
                for path in store.iterdir():
                    if path.is_file():
                        for secret in (b'synthetic-loid',b'synthetic-pw'):
                            assert secret not in path.read_bytes() and secret.hex().encode() not in path.read_bytes()
                checks.append('private credential buffers are omitted from status and MIB journals; shutdown clears authentication assertions')
            finally:
                if process and process.poll() is None:process.kill();process.wait()
                peer.close();os.close(grant_fd)
            args.output.write_text(json.dumps(dict(backend=args.backend,checks=checks,
                scope='Real controller/veth/bridge/VLAN, modeled MiniOLT and original OMCI encoders; no physical packet-retirement acceptance.'),indent=2)+'\n')
            print(json.dumps(checks,indent=2));return
        if args.multi_services:
            from test_fttr_vlan import master_rules
            count=args.multi_services
            checks=[]
            # Reverse file order: execution is stable by hardware slot.
            def kind(slot):
                return ('shared-ctc' if args.multicast=='ctc' else 'shared-mcast') if args.multicast and slot==count else 'shared'
            rows=[f'* {kind(slot)} 0 {slot} 3 filter:{100*slot} 50 100 200 br-fttr\n' for slot in range(count,0,-1)]
            policy.write_text(f"* {'factory-ctc' if args.multicast=='ctc' else 'factory'} {count+1} br-fttr\n" if args.factory else ''.join(rows))
            nodes[7].hold=False;nodes[9].fail_upload_once=False
            process=None
            def ready():
                until(lambda s:all(s['onus'][id]['datapath_configured'] for id in (7,9)),seconds=20)
                for id in (7,9):
                    states=[e for e in journal(id) if e['event']=='service_state']
                    assert len(states)==count and all(e['configured'] for e in states)
                    assert [e['gem'] for e in states]==[256+id+64*n for n in range(count)]
                    assert len({e['tcont'] for e in states})==count
                    assert len({e['ani_port'] for e in states})==count
                    assert len([e for e in nodes[id].pool if e[0]==45])==1
                    assert len([o for o in nodes[id].operations if o[1:3]==(0x44,45)])<=1
                    for n,state in enumerate(states):
                        name=f'fttr-u{id}'+(f'-{n+1}' if n else '')
                        assert ports()[name]['linkinfo']['info_data']['id']==state['gem']
                        assert int(ports()[name]['group'])==(0x46544d55 if args.multicast else 0x46545452)
                        assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(id*8+n+1)*8)==((0,19440) if args.factory else (50,100<<16|200))
                    if args.factory:
                        recipes=[e for e in journal(id) if e['event']=='service_recipe']
                        assert all(e['factory'] and e['priority']==0 and e['bandwidth']==[0,0,19440] for e in recipes)
                        assert [e['vid'] for e in recipes]==([100*n for n in range(1,count+1)] if count>1 else [0])
                        assert all(e['tagged']==(count>1) and e['filter_only']==(count>1) for e in recipes)
                        selected={(11,i) for i in ((1,0x101,0x102,0x103) if nodes[id].veip else (0x101,0x102,0x103,0x104))}|{(329,0x301)}
                        unis={(329 if e['kind']==2 else 11,e['instance']) for e in journal(id) if e['event']=='service_uni'}
                        assert unis==selected
                        for c,i in nodes[id].pool:
                            if c in (11,329):
                                assert nodes[id].pool[c,i][0x8000 if c==329 else 0x0800]==bytes([0 if (c,i) in selected else 1])
                    expected=master_rules(stock,0xfbeb0,0,0,0)
                    uni_ports={e['port'] for e in journal(id) if e['event']=='service_uni'}
                    for port in uni_ports:assert [row for key,row in nodes[id].table.items() if key[0]==port]==expected
                if args.multicast:
                    until(lambda s:s['multicast']['configured'])
                    assert status()['multicast']['members']==2 and multicast_up()
            def stop():
                process.send_signal(signal.SIGTERM);_,errors=process.communicate(timeout=3)
                assert process.returncode==0,errors
                assert not ports()
            def reset():
                nodes.clear();nodes[7]=Node(7,'5445535400000007',0x107,False)
                nodes[9]=Node(9,'5445535400000009',0x203,True)
                hardware();admin_log.write_text('')
            def request_move(error=0):
                client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move','7',nodes[7].serial.hex(),'11','0'*16],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    until(lambda s:client.poll() is not None,seconds=20,allow_exit=bool(error))
                    output,errors=client.communicate(timeout=1)
                    assert client.returncode==bool(error),(output,errors)
                    if error:assert f'({-error})' in errors,errors
                finally:
                    if client.poll() is None:client.kill();client.wait()
            try:
                good_policy=policy.read_text()
                if args.factory:
                    for invalid in ('* factory 1 br-fttr\n', '* factory 9 br-fttr\n',
                                    '* factory 4294967298 br-fttr\n','* factory x br-fttr\n',
                                    '* factory 2 br-fttr extra\n',
                                    good_policy+'* shared 0 7 0 filter:900 0 0 19440 br-fttr\n',
                                    '* shared 0 7 0 filter:900 0 0 19440 br-fttr\n'+good_policy):
                        policy.write_text(invalid)
                        rejected=launch();_,errors=rejected.communicate(timeout=3)
                        assert rejected.returncode==1 and not store.joinpath('status.json').exists()
                    policy.write_text('* factory 2 br-fttr\n');device.joinpath('backend').write_text('fpga1\n')
                    rejected=launch();_,errors=rejected.communicate(timeout=3)
                    assert rejected.returncode==1 and not store.joinpath('status.json').exists()
                    device.joinpath('backend').write_text(args.backend+'\n');policy.write_text(good_policy)
                    process=launch();ready();request_move();stop()
                    assert not nodes[7].table
                    assert not any(c in (45,47,84,171,266,268,272,281,309,310,311,65531) for c,i in nodes[7].pool)
                    checks.append(f'factory total{count+1} generates {count} jobs with priority0, actual DBA0/0/19440, default ANI filters and last-job multicast; complete local admission and reverse migration succeed')
                    checks.append('actual MIB card classification selects four Ethernet UNIs and first-slot VEIP only; excess, later-card and orphan ports retain admin state')
                    for fault in ('missing-card-type','empty-first-veip','missing-queue','last-gem-reject'):
                        reset()
                        if fault=='missing-card-type':nodes[7].pool[5,2]={0x4000:b'\x30'}
                        if fault=='empty-first-veip':
                            del nodes[7].pool[329,0x301];del nodes[7].pool[329,0x302]
                        if fault=='missing-queue':nodes[7].pool.pop((277,nodes[7].queue+count-1))
                        if fault=='last-gem-reject':nodes[7].reject_gem=263+64*(count-1)
                        process=launch()
                        until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'],seconds=20)
                        assert not any(name=='fttr-u7' or name.startswith('fttr-u7-') for name in ports())
                        assert not nodes[7].table and not any(c in (45,47,84,171,266,268,272) for c,i in nodes[7].pool)
                        if fault!='last-gem-reject':
                            assert not any(op in (0x44,0x48,0x46) and c not in (256,65530) for _,op,c,_ in nodes[7].operations)
                        for n in range(count):assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+n+1)*8)==(0,0)
                        stop()
                    checks.append('missing card type, empty first VEIP and missing final queue reject before service IO; failed final GEM reverses prior members and leaves no partial local grant')
                    args.output.write_text(json.dumps(dict(checks=checks,backend=args.backend,scope='Actual resident/CLI/OMCI/veth/bridge/VLAN with original wire encoders and modeled MiniOLT/ONU storage; no physical traffic acceptance.'),indent=2)+'\n')
                    print(json.dumps(checks,indent=2));return
                for invalid in (
                        '* shared 0 1 3 filter:100 50 100 200 br-fttr\n* shared 0 2 3 filter:100 50 100 200 br-fttr\n',
                        '* shared 0 1 3 100 50 100 200 br-fttr\n* shared 0 2 3 filter:200 50 100 200 br-fttr\n',
                        '* shared-mcast 0 1 3 filter:100 50 100 200 br-fttr\n* shared-mcast 0 2 3 filter:200 50 100 200 br-fttr\n'):
                    policy.write_text(invalid)
                    rejected=launch();_,errors=rejected.communicate(timeout=3)
                    assert rejected.returncode==1 and not store.joinpath('status.json').exists()
                    assert ni.joinpath('fttr_forward').read_text()=='0\n'
                policy.write_text(good_policy)
                process=launch();ready()
                stop()
                checks.append(f'{count} jobs reserve distinct T-CONT/GEM/ANI, share one bridge and transparent UNI and publish all local ports')
                # Restart adopts existing remote state and must not invent
                # deletion rights for a later migration.
                process=launch();ready();request_move(errno.ENODATA);stop()
                reset()
                process=launch();ready();request_move()
                assert not any(name=='fttr-u7' or name.startswith('fttr-u7-') for name in ports())
                assert len(ports())==count
                assert not nodes[7].table
                assert not any(c in (45,47,84,171,266,268,272,281,309,310,311,65531) for c,i in nodes[7].pool)
                assert all(nodes[7].pool[262,nodes[7].tcont+n][0x8000]==b'\0\xff' for n in range(count))
                deletes=[(c,i) for _,op,c,i in nodes[7].operations if op==0x46]
                for n in range(count-1,0,-1):assert deletes.index((268,263+64*n))<deletes.index((268,263+64*(n-1)))
                if args.multicast:assert deletes.index((281,4095))<deletes.index((268,263)) and not multicast_up()
                stop();checks.append('whole collection migrates in reverse order, removes every source port/grant and restores shared UNI only after later GEMs; restart adoption refuses unowned deletion')
                # Last job fails after previous jobs have completed. Those
                # jobs must rollback; no local port is published partway.
                reset();nodes[7].reject_gem=263+64*(count-1)
                process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'],seconds=20)
                collection=next(e for e in journal(7) if e['event']=='service_set')
                assert collection['error']==-errno.EREMOTEIO and not collection['cleanup_error'],collection
                assert not nodes[7].table and not any(c in (45,47,84,171,266,268,272) for c,i in nodes[7].pool)
                assert not any(name=='fttr-u7' or name.startswith('fttr-u7-') for name in ports())
                for n in range(count):assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+n+1)*8)==(0,0)
                stop();checks.append('last-job rejection restores earlier grants/UNI/core in reverse order without publishing partial datapath')
                reset();nodes[7].reject_gem=263+64*(count-1);nodes[7].move_reject=True
                process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'],seconds=20)
                collection=next(e for e in journal(7) if e['event']=='service_set')
                assert collection['error']==-errno.EREMOTEIO and collection['cleanup_error']==-errno.EREMOTEIO
                assert (45,0x8080) in nodes[7].pool and (268,263) in nodes[7].pool
                assert not any(name=='fttr-u7' or name.startswith('fttr-u7-') for name in ports())
                for n in range(count):assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+n+1)*8)==(0,0)
                stop();checks.append('failed rollback preserves shared dependencies and separate initiating/cleanup errors while all local grants close')
                reset();nodes[7].pool.pop((277,nodes[7].queue+count-1))
                process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'],seconds=20)
                assert next(e for e in journal(7) if e['event']=='service_set')['error']==-errno.ENOSPC
                assert not any(op in (0x44,0x48,0x46) and c not in (256,65530) for _,op,c,_ in nodes[7].operations)
                stop();checks.append('missing last T-CONT queue fails the complete reservation before any business write; conflicting VLAN/access/multicast policies reject at startup')
                reset();nodes[7].drift_second=True
                process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'],seconds=20)
                collection=next(e for e in journal(7) if e['event']=='service_set')
                assert collection['error']==-errno.ESTALE and collection['cleanup_error']==-errno.EUCLEAN,collection
                assert not any(op==0x48 and c==262 and i==nodes[7].tcont+1 for _,op,c,i in nodes[7].operations)
                assert (45,0x8080) in nodes[7].pool
                for n in range(count):assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+n+1)*8)==(0,0)
                stop();checks.append('UNI drift between members rejects later writes and retains shared dependencies when checked restoration cannot prove ownership')
                reset();nodes[7].drift_table=True
                process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'],seconds=20)
                collection=next(e for e in journal(7) if e['event']=='service_set')
                assert collection['error']==-errno.ESTALE and collection['cleanup_error']==-errno.EUCLEAN,collection
                assert (268,263) in nodes[7].pool and (268,327) not in nodes[7].pool
                expected=master_rules(stock,0xfbeb0,0,0,0)
                assert any(row not in expected for row in nodes[7].table.values())
                stop();checks.append('later members compare the complete shared EVTO snapshot and preserve an intervening writer instead of restoring transparency over it')
                reset()
                # Model an ONU which does not maintain data-sync, including
                # the auth result Set. Ordinary original-sync tests above
                # execute the target's normal incrementing implementation.
                nodes[7].counter.increment=lambda:None
                process=launch();ready();before=status()['onus'][7]['mib_rescans']
                assert status()['onus'][7]['mib_sync']==0
                queried={o[3] for o in nodes[7].operations if o[1:3]==(0x49,268)}
                assert {256+7+64*n for n in range(count)}<=queried
                del nodes[7].pool[268,263+64*(count-1)]
                until(lambda s:s['onus'][7]['mib_rescans']>before and s['onus'][7]['phase']=='service-failed',seconds=15)
                assert not any(name=='fttr-u7' or name.startswith('fttr-u7-') for name in ports())
                assert status()['onus'][9]['datapath_configured']
                for n in range(count):assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+n+1)*8)==(0,0)
                stop();checks.append('loss of the last GEM with unchanged zero MIB sync withdraws every affected port/grant while the other ONU stays active')
                reset()
                policy.write_text(good_policy+'5445535400000007 shared 0 2 3 filter:900 50 100 200 br-fttr\n')
                process=launch()
                until(lambda s:all(s['onus'][id]['datapath_configured'] for id in (7,9)),seconds=20)
                assert status()['onus'][7]['service_count']==1 and status()['onus'][9]['service_count']==count
                assert 'fttr-u7-2' in ports() and 'fttr-u7' not in ports()
                assert next(e for e in journal(7) if e['event']=='service_recipe')['vid']==900
                stop();checks.append('an exact serial replaces the complete wildcard collection, including a single higher-slot service; no wildcard rows leak into it')
                reset()
                policy.write_text(good_policy+
                    '5445535400000007 shared 0 1 3 filter:100 50 100 200 br-fttr\n'
                    '5445535400000007 shared 0 2 3 filter:200 50 100 200 br-missing\n')
                process=launch()
                until(lambda s:s['onus'][7]['phase']=='service-failed' and s['onus'][9]['datapath_configured'],seconds=20)
                assert status()['onus'][7]['datapath_error']==-errno.ENODEV
                assert not any(name=='fttr-u7' or name.startswith('fttr-u7-') for name in ports())
                for n in range(2):assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+n+1)*8)==(0,0)
                stop();checks.append('second local bridge failure withdraws the already-created first port and every grant, preserving the healthy ONU')
            finally:
                if process and process.poll() is None:process.kill();process.wait()
                peer.close();os.close(grant_fd)
            args.output.write_text(json.dumps(dict(checks=checks,backend=args.backend,scope='Actual resident/CLI/OMCI/veth/bridge/VLAN with modeled MiniOLT/ONU storage; no physical retirement or traffic acceptance.'),indent=2)+'\n')
            print(json.dumps(checks,indent=2));return
        if args.move_control:
            if args.shared:
                kind='shared-ctc' if args.multicast=='ctc' else 'shared-mcast' if args.multicast else 'shared'
                vid='filter:200' if args.filter_only else '200'
                policy.write_text(f'* {kind} 0 1 3 {vid} 50 100 200 br-fttr\n')
            checks=[];nodes[7].hold=False;nodes[7].wifi_hold=False;nodes[9].fail_upload_once=False
            def move(from_id,to,serial=None,target=None,error=0):
                client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move',str(from_id),
                    nodes[from_id].serial.hex() if serial is None else serial,str(to),
                    (nodes[to].serial.hex() if to in nodes else '0'*16) if target is None else target],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    until(lambda s:client.poll() is not None,seconds=20,allow_exit=bool(error))
                    output,errors=client.communicate(timeout=1)
                finally:
                    if client.poll() is None:client.kill();client.wait()
                assert client.returncode==bool(error),(output,errors)
                if error:assert f'({-error})' in errors,errors
                else:
                    row=json.loads(output);assert row['reservation_updated'] and not row['registration_verified']
            if args.multicast and args.auth:nodes[7].auth_hold=True
            process=launch()
            try:
                if args.multicast and args.auth:
                    until(lambda s:nodes[7].held is not None and s['onus'][9]['datapath_configured'])
                    assert not multicast_up() and not status()['multicast']['configured']
                    nodes[7].auth_hold=False
                    peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                until(lambda s:all(s['onus'][id]['datapath_configured'] for id in (7,9)))
                if args.multicast:
                    until(lambda s:s.get('multicast',{}).get('configured'))
                    assert multicast_up() and status()['multicast']['members']==2
                if args.shared:
                    for id in (7,9):
                        unis=[e for e in journal(id) if e['event']=='service_uni']
                        reported={(c,i) for c,i in nodes[id].pool if c in (11,329)}
                        assert {(329 if u['kind']==2 else 11,u['instance']) for u in unis}==reported
                        assert len({u['port'] for u in unis})==len(unis)
                        assert all(nodes[id].pool[47,u['port']][0x8000]==b'\x80\x80' for u in unis)
                        assert all(nodes[id].pool[c,i][0x8000 if c==329 else 0x0800]==b'\0' for c,i in reported)
                        for uni in unis:
                            expected={(47,uni['port']),(171,uni['port']),
                                      (329 if uni['kind']==2 else 11,uni['instance'])}
                            if uni['kind']==0: expected.add((84,uni['port']))
                            assert {(o['class'],o['instance']) for o in uni['objects']}==expected
                        recipe=next(e for e in journal(id) if e['event']=='service_recipe')
                        assert recipe['filter_only']==args.filter_only and recipe['tagged'] and recipe['vid']==200
                        if args.filter_only:
                            from test_fttr_vlan import master_rules
                            expected=master_rules(stock,0xfbeb0,0,0,0)
                            for uni in unis:
                                assert [row for key,row in nodes[id].table.items() if key[0]==uni['port']]==expected
                            ani=journal(id)[0]['ani_port']
                            assert nodes[id].pool[84,ani][0x8000]==b'\x00\xc8'+bytes(22)
                        if args.multicast:
                            recipe=next(e for e in journal(id) if e['event']=='service_recipe')
                            mc=next(e for e in journal(id) if e['event']=='service_multicast')
                            assert recipe['multicast']==(2 if args.multicast=='ctc' else 1)
                            assert mc['configured'] and not mc['error'] and not mc['cleanup_error']
                            assert nodes[id].table['mcast',4095]==bytes.fromhex('0fff0000e0000100efffffff')
                    checks.append('shared policy configures every reported Ethernet/VEIP UNI before attaching a single GEM port through the native FE lifecycle')
                if args.multicast:
                    healthy=datapath(9)
                    # Mutate only multicast while ONU Data remains zero. The
                    # normal unicast GEM probe cannot detect this loss.
                    old=nodes[7].table['mcast',4095]
                    nodes[7].counter.value=0
                    nodes[7].table['mcast',4095]=old[:-1]+bytes([old[-1]^1])
                    until(lambda s:not multicast_up() and s['onus'][7]['mib_sync_error'])
                    assert datapath(9)==healthy and not multicast_up()
                    nodes[7].table['mcast',4095]=old
                    until(lambda s:s['onus'][7]['datapath_configured'] and s['multicast']['configured'],seconds=12)
                    process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                    assert not multicast_up()
                    # Recreate fresh ownership for the existing move tests.
                    nodes[7]=Node(7,'5445535400000007',0x107,False)
                    nodes[9]=Node(9,'5445535400000009',0x203,True)
                    hardware();process=launch()
                    until(lambda s:s['multicast']['configured'])
                    checks.append('zero-sync multicast-only drift closes the shared endpoint and withdraws the affected UNI while the healthy peer stays active; shutdown detaches the kernel-created upper')
                before=len(accel_events());peer_port=datapath(9)
                move(7,11,serial='0'*16,error=errno.ESTALE)
                move(7,11,target='1'*16,error=errno.ESTALE)
                move(7,9,error=errno.EBUSY)
                assert len(accel_events())==before and datapath(9)==peer_port
                # An occupied target discovered only under the driver lock stops the move.
                admin_fault.write_text('busy');move(7,11,error=errno.EBUSY);admin_fault.unlink()
                until(lambda s:s['onus'][7]['datapath_configured'])
                assert 'move' not in admin_log.read_text()
                checks.append('stale source/target and occupied target reject; driver preflight rejection leaves the healthy peer active')
                before=len(accel_events());old_port=datapath(7)
                # Hold release mid-flight: healthy ONU sync continues while local data stays withdrawn.
                nodes[7].move_hold=True;nodes[7].held=None
                client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move','7',
                    nodes[7].serial.hex(),'11','0'*16],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    until(lambda s:nodes[7].held is not None)
                    current=status()['onus'][7]
                    assert current['phase']=='move-release' and not current['datapath_configured'] and not current['loid_authenticated']
                    if args.multicast:assert not multicast_up()
                    previous=len(nodes[9].operations)
                    until(lambda s:len(nodes[9].operations)>previous)
                    assert not ports().get('fttr-u7') and datapath(9)==peer_port
                    assert 'move' not in admin_log.read_text()
                    nodes[7].move_hold=False
                    peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                    until(lambda s:client.poll() is not None)
                    output,errors=client.communicate(timeout=1)
                    assert client.returncode==0,(output,errors)
                finally:
                    if client.poll() is None:client.kill();client.wait()
                current=until(lambda s:not s['onus'][7]['datapath_configured'])
                assert 'fttr-u7' not in ports() and 'fttr-u11' not in ports() and datapath(9)==peer_port
                assert journal(7,'service-old')[0]['grant_withdrawn']
                assert journal(7,'service-old')[0]['phase']=='released' and not journal(7,'service-old')[0]['error']
                assert accel_events()[before:]==[(0,old_port,peer_port),(1,0,peer_port),(0,0,peer_port),(1,0,peer_port)]
                data=device.joinpath('onu_state').read_bytes()
                assert data[7*20+12:8*20]==bytes(8) and data[11*20+12:12*20]==nodes[7].serial
                if args.shared:
                    assert not nodes[7].table and not any(c in (45,47,84,171,266,268,272,281,309,310,311,65531) for c,i in nodes[7].pool)
                    assert all(nodes[7].pool[c,i][0x8000 if c==329 else 0x0800]==b'\1' for c,i in nodes[7].pool if c in (11,329))
                    uni_ledgers=[e for e in journal(7,'service-old') if e['event']=='service_uni_objects']
                    assert len(uni_ledgers)>=3 and all(not o['touched'] for e in uni_ledgers for o in e['objects'])
                    checks.append('shared migration restores all UNI VLAN/admin state and releases every owned attachment before shared GEM/T-CONT and SN mutation')
                    if args.multicast:
                        writes=[(op,c) for _,op,c,i in nodes[7].operations if op==0x46 or op==0x48 and c==281]
                        assert writes.index((0x46,281))<writes.index((0x46,45))
                        checks.append('remote multicast standard/CTC profile and original GEM4095 row are journaled and released before the shared bridge; local multicast opens after both members verify and closes before migration')
                # Registration is an external event, never synthesized by the controller.
                node=nodes.pop(7);node.id=11;node.auth_hold=args.auth;node.held=None;nodes[11]=node
                hardware()
                if args.auth:
                    until(lambda s:s['onus'][11]['phase']=='authentication' and nodes[11].held is not None)
                    assert 'fttr-u11' not in ports() and not status()['onus'][11]['loid_authenticated']
                    nodes[11].auth_hold=False
                    peer.send(HOST+OLT+struct.pack('>HH',0x8888,11)+nodes[11].held)
                until(lambda s:s['onus'][11]['datapath_configured'])
                datapath(11)
                if args.multicast:
                    until(lambda s:s.get('multicast',{}).get('configured'))
                    assert multicast_up()
                until(lambda s:s['onus'][7]['phase']=='absent',seconds=17)
                assert datapath(9)==peer_port
                checks.append('move withdraws source FE/VLAN/DBA, reserves target, waits for registration and reauthenticates before service')
                # Move back, then fail during hardware mutation: never reopen FE.
                move(11,7);node=nodes.pop(11);node.id=7;nodes[7]=node;hardware()
                until(lambda s:s['onus'][7]['datapath_configured'])
                before=len(accel_events());admin_fault.write_text('io');move(7,11,error=errno.EIO)
                _,errors=process.communicate(timeout=3)
                assert process.returncode==1 and status()['error']==-errno.EIO,errors
                events=accel_events()[before:]
                assert sum(enabled for enabled,_,_ in events)==1 and events[-1][0]==0,events
                assert device.joinpath('tx_enable').read_text()=='0\n' and not ports()
                checks.append('partial move error stops TX and removes all services without resuming FE')
                admin_fault.unlink()
                for fault,expected in [('vlan-drift',errno.EUCLEAN),('object-drift',errno.EUCLEAN),('move_reject',errno.EREMOTEIO),('move_false',errno.EUCLEAN),
                        ('move_drop',errno.ETIMEDOUT),('target-replaced',errno.ESTALE),('stop',errno.ECANCELED)]:
                    nodes[7]=Node(7,'5445535400000007',0x107,False)
                    nodes[9]=Node(9,'5445535400000009',0x203,True)
                    hardware();admin_log.write_text('');accel_log.write_text('')
                    process=launch()
                    until(lambda s:all(s['onus'][id]['datapath_configured'] for id in (7,9)))
                    if fault=='vlan-drift':
                        key=next(iter(nodes[7].table));value=nodes[7].table[key]
                        nodes[7].table[key]=value[:-1]+bytes([value[-1]^1])
                    if fault=='object-drift':nodes[7].pool[262,nodes[7].tcont][0x8000]=b'\x00\xfa'
                    if fault.startswith('move_'):setattr(nodes[7],fault,True)
                    elif not fault.endswith('drift'):nodes[7].move_hold=True;nodes[7].held=None
                    if fault.startswith('move_') or fault.endswith('drift'):
                        move(7,11,error=expected)
                    else:
                        client=subprocess.Popen([str(args.move_control.resolve()),str(store),'onu-move','7',
                            nodes[7].serial.hex(),'11','0'*16],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                        try:
                            until(lambda s:nodes[7].held is not None)
                            if fault=='stop':process.send_signal(signal.SIGTERM)
                            else:
                                nodes[11]=Node(11,'54455354000000aa',0x311,True);hardware()
                                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                            until(lambda s:client.poll() is not None,allow_exit=True)
                            _,errors=client.communicate(timeout=1)
                            assert client.returncode==1 and f'({-expected})' in errors,errors
                        finally:
                            if client.poll() is None:client.kill();client.wait()
                    _,errors=process.communicate(timeout=3)
                    assert process.returncode==(0 if fault=='stop' else 1),(fault,errors)
                    assert 'move' not in admin_log.read_text() and device.joinpath('tx_enable').read_text()=='0\n' and not ports()
                    assert journal(7)[0]['error']==-expected
                    nodes.pop(11,None)
                checks.append('VLAN/object drift, release rejection, false-success delete, timeout, target replacement and shutdown never program the SN reservation')
                # Restarted adoption lacks deletion ownership and rejects before FE changes.
                nodes[7]=Node(7,'5445535400000007',0x107,False);nodes[9]=Node(9,'5445535400000009',0x203,True);hardware()
                process=launch();until(lambda s:all(s['onus'][id]['datapath_configured'] for id in (7,9)))
                process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                process=launch();until(lambda s:all(s['onus'][id]['datapath_configured'] for id in (7,9)))
                before=len(accel_events());move(7,11,error=errno.ENODATA)
                assert len(accel_events())==before and set(ports())=={'fttr-u7','fttr-u9'}
                process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                checks.append('adopted remote resources without a prior ownership recipe reject move before FE or peer writes')
                if args.multicast:
                    saved_policy=policy.read_text()
                    kind='shared-ctc' if args.multicast=='ctc' else 'shared-mcast'
                    # ONU9 has no multicast policy but retains the old shared
                    # GEM after restart. No deletion ownership is invented.
                    policy.write_text(f'5445535400000007 {kind} 0 1 3 200 50 100 200 br-fttr\n')
                    process=launch(with_wifi=False)
                    until(lambda s:s['onus'][7]['datapath_configured'] and s['onus'][9]['mib_sync_valid'])
                    assert not multicast_up() and not status()['multicast']['configured']
                    assert (281,4095) in nodes[9].pool
                    # Model an external owner removing its obsolete multicast
                    # GEM. The live absence probe can now admit the one member.
                    nodes[9].pool.pop((281,4095));nodes[9].pool.pop((268,4095))
                    until(lambda s:s['multicast']['configured'])
                    assert multicast_up() and status()['multicast']['members']==1
                    process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                    assert not multicast_up()
                    if args.auth:
                        nodes[9].pool[65530,0][0x4000]=b'wrong'.ljust(24,b'\0')
                        process=launch(with_wifi=False)
                        until(lambda s:s['onus'][9]['phase']=='auth-failed' and s['onus'][7]['datapath_configured'])
                        assert not multicast_up() and not status()['multicast']['configured']
                        process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                        nodes[9].pool[65530,0][0x4000]=b'synthetic-loid'.ljust(24,b'\0')
                    policy.write_text(saved_policy)
                    checks.append('cross-bridge multicast policy rejects before IO; retained GEM4095 on an unselected ONU prevents local admission without deleting foreign resources; live absence permits recovery, while failed authentication keeps multicast closed')
                process=launch(with_policy=False,with_wifi=False)
                until(lambda s:all(s['onus'][id]['phase']=='inventory' for id in (7,9)))
                before=len(accel_events());move(7,11,error=errno.ENODATA)
                assert len(accel_events())==before
                process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                nodes[7]=Node(7,'5445535400000007',0x107,False);nodes[9]=Node(9,'5445535400000009',0x203,True);hardware()
                process=launch(with_policy=False,with_wifi=False)
                until(lambda s:all(s['onus'][id]['phase']=='inventory' for id in (7,9)))
                move(7,11)
                process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                checks.append('inventory-only move rejects unmanaged data resources; an unassigned ONU may move without a service policy')
                if args.auth:
                    nodes[7]=Node(7,'5445535400000007',0x107,False);nodes[9]=Node(9,'5445535400000009',0x203,True)
                    nodes[7].pool[65530,0][0x4000]=b'wrong'.ljust(24,b'\0');hardware()
                    process=launch(with_policy=False,with_wifi=False)
                    until(lambda s:s['onus'][7]['phase']=='auth-failed')
                    before=len(accel_events());move(7,11,error=errno.EBUSY)
                    assert len(accel_events())==before
                    process.send_signal(signal.SIGTERM);process.communicate(timeout=3)
                    checks.append('authentication failure without MIB inventory cannot bypass remote resource ownership checks')
            finally:
                if process.poll() is None:process.kill();process.wait()
                peer.close();os.close(grant_fd)
            args.output.write_text(json.dumps(dict(backend=args.backend,checks=checks,
                scope='Real controller/CLI/veth/bridge/VLAN, modeled MiniOLT and original OMCI encoders. No physical registration/packet retirement.'),indent=2)+'\n')
            print(json.dumps(checks,indent=2));return
        if args.auth:
            checks=['invalid, exposed or duplicate LOID policy fails before ownership and hardware writes']
            nodes[7].hold=False;nodes[7].wifi_hold=False;nodes[7].auth_hold=True;nodes[9].fail_upload_once=False
            def auth_status(state,id=7):return state.get('onus') and state['onus'][id]
            def no_service(id=7):
                row=status()['onus'][id]
                assert not row['service_configured'] and not row['datapath_configured'] and not row['wifi_configured']
                assert f'fttr-u{id}' not in [p['ifname'] for p in json.loads(subprocess.check_output(['ip','-j','link'],text=True))]
                grant=device.joinpath('tcont_state').read_bytes()[(id*8+physical_slot)*8:(id*8+physical_slot+1)*8]
                assert grant==bytes(8)
                assert not any(op in (0x44,0x48) and c not in (256,65530) for _,op,c,_ in nodes[id].operations)
            def replace(**attrs):
                replacement=Node(7,(int.from_bytes(nodes[7].serial,'big')+1).to_bytes(8,'big').hex(),0x307,True)
                for name,value in attrs.items():setattr(replacement,name,value)
                nodes[7]=replacement;hardware()
            # An earlier controller may have died without withdrawing DBA.
            # Seed both the selected data slot and a slot with no current policy.
            residual_slots=(0,) if args.backend=='fpga1' else (1,7)
            with device.joinpath('tcont_state').open('r+b') as f:
                for id in (7,11):
                    for slot in residual_slots:
                        f.seek((id*8+slot)*8);f.write(struct.pack('<II',50,100<<16|200))
            process=launch()
            try:
                until(lambda s:auth_status(s) and s['onus'][7]['phase']=='authentication' and s['onus'][9]['datapath_configured'])
                no_service()
                assert not any(device.joinpath('tcont_state').read_bytes()[11*64:12*64])
                for slot in residual_slots:
                    assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+slot)*8)==(0,0)
                checks.append('warm ownership clears residual data grants, including absent/unconfigured ONUs, before authentication')
                assert not status()['onus'][7]['loid_authenticated']
                nodes[7].auth_hold=False
                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                until(lambda s:s['onus'][7]['datapath_configured'])
                assert status()['onus'][7]['loid_authenticated'] and status()['onus'][7]['loid_result_verified']
                if args.wifi:
                    assert all(status()['onus'][id]['wifi_configured']==2 for id in (7,9))
                checks.append('held authentication does not create services or block the authenticated peer')
                for attribute,error,result in [('loid',-errno.EACCES,2),('key',-errno.EACCES,3)]:
                    replace()
                    nodes[7].pool[65530,0][0x4000 if attribute=='loid' else 0x2000]=b'wrong'.ljust(24 if attribute=='loid' else 12,b'\0')
                    until(lambda s:s['onus'][7]['phase']=='auth-failed')
                    row=status()['onus'][7]
                    assert row['loid_auth_error']==error and row['loid_result']==result and row['loid_result_verified']
                    assert not row['loid_authenticated'];no_service()
                checks.append('LOID/key rejection writes and verifies original2/3 result without provisioning')
                for first in (0x4000,0x2000):
                    replace(auth_ctc_fail=first)
                    until(lambda s:s['onus'][7]['datapath_configured'])
                    row=status()['onus'][7]
                    assert row['loid_authenticated'] and row['loid_fallback_error']==-errno.EREMOTEIO
                    assert any(c==256 for _,_,c,_ in nodes[7].operations)
                for second in (0x0040,0x0020):
                    replace(auth_ctc_fail=0x4000,auth_onug_fail=second)
                    until(lambda s:s['onus'][7]['phase']=='auth-failed')
                    assert not status()['onus'][7]['loid_acquired'] and status()['onus'][7]['loid_auth_error']==-errno.EREMOTEIO
                    no_service()
                checks.append('both original acquisition paths work; failed/partial fallback never authenticates')
                for attrs,error in [({'auth_bad_result':True},-errno.EUCLEAN),
                        ({'auth_reject_result':True},-errno.EREMOTEIO),({'auth_malformed':True},-errno.EOPNOTSUPP)]:
                    replace(**attrs)
                    until(lambda s:s['onus'][7]['phase']=='auth-failed')
                    assert status()['onus'][7]['loid_auth_error']==error,(attrs,error,status()['onus'][7]['loid_auth_error'])
                    no_service()
                replace(auth_drop=True)
                until(lambda s:s['onus'][7]['phase']=='auth-failed',seconds=16)
                assert status()['onus'][7]['loid_auth_error']==-errno.ETIMEDOUT
                assert len(nodes[7].operations)==6;no_service()
                checks.append('write rejection, bad readback, malformed response and bounded timeout fail explicitly')
                replace(auth_hold=True)
                until(lambda s:s['onus'][7]['phase']=='authentication' and nodes[7].held is not None)
                stale=nodes[7].held
                replace()
                # Deliver an old transaction before refresh observes the replacement.
                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+stale)
                until(lambda s:s['onus'][7]['datapath_configured'])
                assert status()['onus'][7]['loid_authenticated']
                checks.append('replacement invalidates old authentication and requires a fresh transaction')
                # Same-SN MIB loss must rerun authentication before any service rebuild.
                previous=status()['onus'][7]['mib_rescans']
                nodes[7].counter.value=0;nodes[7].auth_hold=True;nodes[7].operations=[]
                until(lambda s:s['onus'][7]['phase']=='authentication' and s['onus'][7]['mib_rescans']==previous+1)
                no_service()
                nodes[7].auth_hold=False
                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                until(lambda s:s['onus'][7]['datapath_configured'])
                checks.append('same-serial MIB reset withdraws service and reauthenticates before rebuilding')
                for path in store.iterdir():
                    if path.is_file():
                        content=path.read_bytes()
                        for value in (b'synthetic-loid',b'synthetic-pw'):
                            assert value not in content and value.hex().encode() not in content
                replace(auth_hold=True)
                until(lambda s:s['onus'][7]['phase']=='authentication')
                process.send_signal(signal.SIGTERM);_,errors=process.communicate(timeout=3)
                assert process.returncode==0,errors
                assert device.joinpath('tx_enable').read_text()=='0\n' and not ports()
                assert not any(row['loid_authenticated'] for row in status()['onus'])
                checks.append('journals contain no credentials; shutdown cancels authentication and withdraws peer services')
                # The original empty policy is explicit and does not assert authentication.
                auth_policy.write_bytes(b'TFL1'+bytes(38))
                replace();nodes[9].operations=[]
                process=launch()
                until(lambda s:all(s['onus'][id]['datapath_configured'] for id in (7,9)))
                for id in (7,9):
                    row=status()['onus'][id]
                    assert not row['loid_auth_required'] and not row['loid_authenticated']
                    assert not any(c in (256,65530) and op in (0x48,0x49) for _,op,c,_ in nodes[id].operations)
                process.send_signal(signal.SIGTERM);_,errors=process.communicate(timeout=3)
                assert process.returncode==0,errors
                checks.append('explicit disabled policy provisions without credential requests or authenticated status')
                auth_policy.write_bytes(good_auth)
                for case,expected in [('grant-false-success',-errno.EUCLEAN),('missing-grants',-errno.ENOENT)]:
                    for node in nodes.values():node.operations=[]
                    accel_log.write_text('')
                    if case=='missing-grants':device.joinpath('tcont_state').unlink()
                    else:
                        with device.joinpath('tcont_state').open('r+b') as f:
                            f.seek((7*8+physical_slot)*8);f.write(struct.pack('<II',50,100<<16|200))
                    process=launch(grant_case=case)
                    _,errors=process.communicate(timeout=3)
                    assert process.returncode==1 and status()['error']==expected,(case,errors,status())
                    assert device.joinpath('tx_enable').read_text()=='0\n' and not ports()
                    assert not any(enabled for enabled,_,_ in accel_events())
                    assert not select.select([peer],[],[],0)[0]
                checks.append('unreadable or false-success grant withdrawal stops TX before FE admission or OMCI')
            finally:
                if process.poll() is None:process.kill();process.wait()
                peer.close();os.close(grant_fd)
            args.output.write_text(json.dumps(dict(backend=args.backend,checks=checks,
                scope='Real daemon/veth/bridge/VLAN and original OMCI frame builders; modeled peer/sysfs; no hardware acceptance.'),indent=2)+'\n')
            print(json.dumps(checks,indent=2))
            return
        if args.wifi:
            def configured(state, ids=(7,9)):
                return state.get('onus') and all(state['onus'][id]['wifi_configured']==2 and
                    state['onus'][id]['mib_sync_valid'] for id in ids)
            def stop():
                process.send_signal(signal.SIGTERM)
                _, errors=process.communicate(timeout=3)
                assert process.returncode==0,errors
            process=launch()
            try:
                current=until(lambda s:configured(s,(9,)) and nodes[7].held is not None)
                assert current['onus'][7]['phase']=='wifi' and current['onus'][9]['service_configured']
                assert not any(op in (0x44,0x48) and c<0xff00 for _,op,c,_ in nodes[7].operations)
                nodes[7].wifi_hold=False
                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+nodes[7].held)
                current=until(lambda s:configured(s) and all(s['onus'][i]['service_configured'] for i in nodes))
                assert nodes[7].pool[0xff69,0x101][0x4000]==b'\x0b'
                assert nodes[9].pool[0xff69,0x101][0x4000]==b'\x06'
                assert current['onus'][7]['mib_rescans']==current['onus'][9]['mib_rescans']==0
                for node in nodes.values():
                    writes=[(op,c) for _,op,c,_ in node.operations if op in (0x44,0x48)]
                    assert all(c>=0xff00 for op,c in writes[:12])
                    assert writes[12][1]==262
                # Attribute drift without any standard data-sync change is detected.
                prior=current['onus'][7]['mib_rescans']
                healthy=sum(op==0x4d for _,op,_,_ in nodes[9].operations)
                nodes[7].pool[0xff69,0x201][0x2000]=b'changed'.ljust(24,b'\0')
                current=until(lambda s:configured(s) and s['onus'][7]['mib_rescans']==prior+1)
                assert nodes[7].pool[0xff69,0x201][0x2000].startswith(b'FTTR native research')
                assert sum(op==0x4d for _,op,_,_ in nodes[9].operations)==healthy
                # Replacement while a Wi-Fi Set has no reply.
                nodes[7]=Node(7,'5445535400000077',0x317,True)
                nodes[7].wifi_hold=True;hardware()
                until(lambda s:nodes[7].held is not None)
                old=nodes[7];old_tcis={t for t,_,_,_ in old.operations}
                nodes[7]=Node(7,'5445535400000777',0x327,True);hardware()
                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+old.held)
                current=until(lambda s:configured(s) and s['onus'][7]['mib_serial']==nodes[7].serial.hex())
                assert not old_tcis.intersection(t for t,_,_,_ in nodes[7].operations)
                assert journal(7,'wifi-old')[0]['error']==-errno.ESTALE
                assert any(g['touched'] for row in journal(7,'wifi-old')[1:] for g in row['groups'])
                # Replacement during restoration preserves primary and cleanup errors.
                nodes[7]=Node(7,'5445535400007777',0x337,True)
                nodes[7].wifi_reject=True;nodes[7].wifi_reject_at=9;nodes[7].wifi_restore_hold=True;hardware()
                until(lambda s:nodes[7].held is not None)
                old=nodes[7]
                nodes[7]=Node(7,'5445535400077777',0x347,True);hardware()
                peer.send(HOST+OLT+struct.pack('>HH',0x8888,7)+old.held)
                until(lambda s:configured(s) and s['onus'][7]['mib_serial']==nodes[7].serial.hex())
                previous=journal(7,'wifi-old')[0]
                assert previous['error']==-errno.EREMOTEIO and previous['cleanup_error']==-errno.ESTALE
                # Matching malformed reply restores that instance; the next instance still progresses.
                nodes[7]=Node(7,'5445535400707777',0x34f,True)
                nodes[7].wifi_malformed=True;hardware()
                current=until(lambda s:s.get('onus') and s['onus'][7]['wifi_error']==-errno.EOPNOTSUPP and
                    s['onus'][7]['service_configured'])
                assert current['onus'][7]['wifi_configured']==1 and not current['onus'][7]['wifi_cleanup_error']
                assert nodes[7].pool[0xff69,0x101][0x2000]==bytes([0x55])*24
                # Lost Set replies exhaust the original three attempts, then restoration can complete.
                nodes[7]=Node(7,'5445535400717777',0x34e,True)
                nodes[7].wifi_timeout=True;hardware()
                current=until(lambda s:s.get('onus') and s['onus'][7]['wifi_error']==-errno.ETIMEDOUT and
                    s['onus'][7]['service_configured'],seconds=12)
                assert current['onus'][7]['wifi_configured']==1 and not current['onus'][7]['wifi_cleanup_error']
                assert sum(t==nodes[7].wifi_lost_tci for t,_,_,_ in nodes[7].operations)==3
                # Missing capabilities remain a Wi-Fi failure without breaking another peer or wired service.
                nodes[7]=Node(7,'5445535400777777',0x357,True)
                nodes[7].pool.pop((0xff6a,0x201));hardware()
                current=until(lambda s:s.get('onus') and s['onus'][7]['wifi_error']==-errno.EOPNOTSUPP and
                    s['onus'][7]['service_configured'])
                assert current['onus'][7]['wifi_configured']==1 and current['onus'][9]['wifi_configured']==2
                stop()
                # Wi-Fi-only policies must not require a fictitious GEM service.
                nodes[7]=Node(7,'5445535400000007',0x107,False);hardware()
                process=launch(with_policy=False)
                until(configured)
                assert not ports() and all(not status()['onus'][id]['service_configured'] for id in nodes)
                prior=status()['onus'][7]['mib_rescans'];nodes[7].counter.value=0
                until(lambda s:configured(s) and s['onus'][7]['mib_rescans']==prior+1)
                prior=status()['onus'][7]['mib_rescans']
                nodes[7].pool[0xff69,0x101][0x2000]=b'reset'.ljust(24,b'\0')
                until(lambda s:configured(s) and s['onus'][7]['mib_rescans']==prior+1)
                assert not any(c==268 for _,_,c,_ in nodes[7].operations)
                # Periodic Wi-Fi probe timeout clears configuration and resumes from a fresh MIB.
                nodes[7].wifi_drop=True
                until(lambda s:s.get('onus') and s['onus'][7]['wifi_configured']==0 and
                    s['onus'][7]['mib_sync_error']==-errno.ETIMEDOUT,seconds=12)
                nodes[7].wifi_drop=False
                until(configured)
                # A duplicate per-serial instance fails before touching the running controller.
                valid=wifi_policy.read_text()
                wifi_policy.write_text(valid+f'* {root}/wifi-first\n')
                rejected=subprocess.Popen([str(args.daemon.resolve()),str(device),str(ni),str(store),
                    'fap-host','--wifi',str(wifi_policy)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                _,errors=rejected.communicate(timeout=2)
                assert rejected.returncode==1 and 'Wi-Fi policy' in errors
                assert status()['pid']==process.pid
                wifi_policy.write_text(valid)
                # Runtime files retain no names, keys or raw Wi-Fi values.
                for path in store.iterdir():
                    if path.is_file():
                        content=path.read_text()
                        for secret in (b'FTTR native research', b'test-passphrase-123', bytes([0x55])*24):
                            assert secret.hex() not in content and secret.decode() not in content,(path,secret)
                        assert not path.stat().st_mode&0o077
                stop()
                before={id:len(n.operations) for id,n in nodes.items()}
                process=launch(with_policy=False)
                until(configured)
                assert all(not any(op==0x48 for _,op,_,_ in nodes[id].operations[before[id]:]) for id in nodes)
                stop()
                process=launch(with_policy=False,with_wifi=False)
                until(lambda s:s.get('onus') and all(s['onus'][id]['phase']=='inventory' for id in nodes))
                assert not any(store.joinpath(f'onu-{id}.wifi').exists() for id in nodes)
                stop()
                # Shared transport failure must stop the entire controller, recording the pending Wi-Fi job.
                nodes[7]=Node(7,'5445535400007777',0x367,True);nodes[7].wifi_hold=True;hardware()
                process=launch(with_policy=False)
                until(lambda s:nodes[7].held is not None)
                subprocess.run(['ip','link','set','dev','fap-host','down'],check=True)
                _,errors=process.communicate(timeout=4)
                assert process.returncode==1 and status()['error'] in (-errno.ENETDOWN,-errno.EIO),errors
                assert status()['onus'][7]['wifi_configured']==0 and not ports()
                assert device.joinpath('tx_enable').read_text().startswith('0\n')
            finally:
                if process.poll() is None:process.terminate();process.communicate(timeout=3)
            checks=['two ONUs and two Wi-Fi instances progress independently before their own service/sync jobs',
                'exact serial overrides wildcard per actual instance; no Set-induced MIB rescan',
                'attribute drift is detected even when standard data-sync stays unchanged or zero',
                'replacement during Set/restoration stops old requests and retains primary/cleanup errors',
                'lost Set replies and matching malformed responses restore the affected instance without blocking another',
                'missing capability remains a Wi-Fi error while wired service and other ONUs continue',
                'Wi-Fi-only policy supports sync/recovery without a GEM probe',
                'three-attempt probe timeout revokes configuration, then re-inventory recovers',
                'duplicate policies fail before hardware IO; runtime diagnostics redact Wi-Fi credentials',
                'warm restart reuses verified values; omitted policy archives previous journals',
                'shared transport failure cancels jobs and disables optical TX']
            os.close(grant_fd)
            peer.close()
            report={'backend':args.backend,'master_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),
                'slave_sha256':hashlib.sha256(args.slave.read_bytes()).hexdigest(),
                'checks':checks, 'scope':'Native daemon over AF_PACKET/veth with original ARM64 descriptors, sync update/reset and VLAN replies; modeled peer MIB/sysfs; no optical or wireless acceptance.'}
            args.output.write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report,indent=2))
            return
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
            assert (7,physical_slot,0,0,0) in grants
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
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+physical_slot)*8)==(0,0)
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
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+physical_slot)*8)==(0,0)
            nodes[7]=node;hardware()
            until(lambda s:s.get('onus') and s['onus'][7]['phase']=='configured')
            datapath(7)
            assert not any(op in (0x44,0x48,0x46) for _,op,_,_ in node.operations[before:])
            assert journal(7,'service-old')[0]['grant_withdrawn']
            if args.admin_control:
                def admin(action, serial=None, error=0):
                    result=subprocess.run([str(args.admin_control.resolve()),str(store),'onu-admin','7',
                        nodes[7].serial.hex() if serial is None else serial,action],capture_output=True,text=True,timeout=5)
                    assert result.returncode==bool(error),(result.stdout,result.stderr)
                    if error: assert f'({-error})' in result.stderr,result.stderr
                    else:
                        row=json.loads(result.stdout)
                        assert row['blocked']==(action!='resume') and row['temporary']==(action=='deactivate')
                def admin_events():
                    return [line.split() for line in admin_log.read_text().splitlines()]
                def downstream(value, upstream=None):
                    data=bytearray(device.joinpath('onu_state').read_bytes())
                    struct.pack_into('<I',data,7*20,value)
                    if upstream is not None:struct.pack_into('<I',data,7*20+4,upstream)
                    device.joinpath('onu_state.new').write_bytes(data)
                    os.replace(device/'onu_state.new',device/'onu_state')
                    rows=[struct.unpack_from('<III8s',data,id*20) for id in range(64)]
                    device.joinpath('onu_table.new').write_text(''.join(
                        f'{id} {ds:08x} {us:08x} {eqd:08x} {sn.hex()}\n' for id,(ds,us,eqd,sn) in enumerate(rows)))
                    os.replace(device/'onu_table.new',device/'onu_table')
                event_start=len(accel_events())
                admin('block',serial='0000000000000000',error=errno.ESTALE)
                assert len(accel_events())==event_start and set(ports())=={'fttr-u7','fttr-u9'}
                # Truncated/oversized local datagrams cannot alter forwarding.
                client=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);client.bind('');client.settimeout(2)
                client.connect(str(store/'control.sock'))
                for payload in (b'',bytes(4),bytes(21),struct.pack('III8s',2,7,1,nodes[7].serial)):
                    client.send(payload);assert struct.unpack('i',client.recv(4))[0]==-errno.EINVAL
                client.close()
                for bad_id,bad_sn,bad_action in (('64',nodes[7].serial.hex(),'block'),
                                                ('7','x'*16,'block'),('7',nodes[7].serial.hex(),'bad')):
                    invalid=subprocess.run([str(args.admin_control.resolve()),str(store),'onu-admin',
                        bad_id,bad_sn,bad_action],capture_output=True,text=True,timeout=2)
                    assert invalid.returncode==1
                assert len(accel_events())==event_start
                old_port=datapath(7);healthy_port=datapath(9)
                admin('block')
                current=until(lambda s:s['onus'][7]['phase']=='blocked')
                assert set(ports())=={'fttr-u9'} and datapath(9)==healthy_port
                assert not current['onus'][7]['service_configured']
                assert not current['onus'][7]['datapath_configured']
                assert journal(7,'service-old')[0]['grant_withdrawn']
                assert accel_events()[event_start:]==[(0,old_port,healthy_port),(1,0,healthy_port)]
                assert not store.joinpath('control.sock').stat().st_mode&0o077
                admin('resume')
                # FPGA state3 clears registration; re-registration is a hardware event.
                if args.backend=='fpga1':hardware()
                until(lambda s:s['onus'][7]['phase']=='configured')
                datapath(7)
                # Driver-side serial recheck failure cancels the old service,
                # preserves the error and permits a fresh inventory afterward.
                admin_fault.write_text('stale')
                admin('block',error=errno.ESTALE);admin_fault.unlink()
                until(lambda s:s['onus'][7]['phase']=='configured')
                # State4 is temporary. FPGA must remain blocked while bit18
                # remains set; ASIC offline polling clears even that state.
                admin_hold.touch();event_start=len(admin_events());healthy_port=datapath(9)
                admin('deactivate')
                if args.backend=='fpga1':
                    current=until(lambda s:s['onus'][7]['temporary_deactivation'] and
                        len(admin_events())>=event_start+3)
                    assert current['onus'][7]['phase']=='blocked' and current['onus'][7]['hardware_registered']
                    assert set(ports())=={'fttr-u9'} and datapath(9)==healthy_port
                    downstream(0x10000)
                until(lambda s:not s['onus'][7]['temporary_deactivation'],seconds=10)
                assert any(action=='2' for _,action,_ in admin_events()[event_start:])
                admin_hold.unlink()
                if args.backend=='fpga1':hardware()
                until(lambda s:s['onus'][7]['phase']=='configured')
                # A permanent command supersedes a pending temporary release.
                admin_hold.touch();admin('deactivate');admin('block')
                event_start=len(admin_events())
                until(lambda s:not s['onus'][7]['temporary_deactivation'] and s['onus'][7]['phase']=='blocked')
                # Pump another successful peer sync after the next FPGA scan.
                before=len(nodes[9].operations)
                until(lambda s:len(nodes[9].operations)>before+2)
                assert not any(action=='2' for _,action,_ in admin_events()[event_start:])
                admin_hold.unlink();admin('resume')
                if args.backend=='fpga1':hardware()
                until(lambda s:s['onus'][7]['phase']=='configured')
                # Original upper state bits gate automatic recovery. Merely
                # setting a lower error field while high bits are set is inert.
                event_start=len(admin_events());before=len(nodes[9].operations)
                downstream(0x40000,0x40100001)
                until(lambda s:len(nodes[9].operations)>before+2)
                assert not any(action=='3' for _,action,_ in admin_events()[event_start:])
                assert set(ports())=={'fttr-u7','fttr-u9'}
                # Recovery is detected by the resident refresh, not a CLI
                # command; it must first retire the actual port and DBA owner.
                admin_hold.touch();event_start=len(admin_events());healthy_port=datapath(9)
                downstream(0x40000,0x00100001 if args.backend=='fpga1' else 0x01000001)
                until(lambda s:any(action=='3' for _,action,_ in admin_events()[event_start:]),seconds=10)
                assert datapath(9)==healthy_port and 'fttr-u7' not in ports()
                assert journal(7,'service-old')[0]['grant_withdrawn']
                if args.backend=='fpga1':downstream(0x10000,0)
                until(lambda s:not s['onus'][7]['temporary_deactivation'],seconds=10)
                admin_hold.unlink();hardware()
                until(lambda s:s['onus'][7]['phase']=='configured')
                # Explicit permanent block must never become temporary due to
                # a later upstream error sample.
                admin('block');event_start=len(admin_events());before=len(nodes[9].operations)
                downstream(0x10000,0x100001)
                until(lambda s:len(nodes[9].operations)>before+2)
                assert not any(action in ('2','3') for _,action,_ in admin_events()[event_start:])
                assert status()['onus'][7]['phase']=='blocked'
                downstream(0x10000,1);admin('resume');hardware()
                until(lambda s:s['onus'][7]['phase']=='configured')
                # Replacement cancels a pending release, with a changed serial
                # checked again by the driver if it wins the refresh race.
                admin_hold.touch();admin('deactivate')
                old_serial=nodes[7].serial
                nodes[7].serial=bytes.fromhex('5445535400770007');hardware()
                downstream(0x10000);event_start=len(admin_events())
                until(lambda s:not s['onus'][7]['temporary_deactivation'] and s['onus'][7]['serial']==nodes[7].serial.hex(),seconds=10)
                assert struct.unpack_from('<I',device.joinpath('onu_state').read_bytes(),7*20)[0]==0x10000
                assert all(sn==old_serial.hex() for _,_,sn in admin_events()[event_start:])
                nodes[7].serial=old_serial;hardware();downstream(0x10000)
                admin_hold.unlink();admin('resume')
                hardware();until(lambda s:s['onus'][7]['phase']=='configured')
                # Stop owns cancellation: no timer can write after shutdown.
                admin_hold.touch();admin('deactivate')
                process.send_signal(signal.SIGTERM);_,error=process.communicate(timeout=3)
                assert process.returncode==0,error
                event_start=len(admin_events())
                assert not status()['onus'][7]['temporary_deactivation'] and not ports()
                admin_hold.unlink();hardware();process=launch()
                until(lambda s:s.get('onus') and all(s['onus'][id]['phase']=='configured' for id in nodes))
                assert not any(action=='2' for _,action,_ in admin_events()[event_start:])
                # Hardware IO failure after withdrawal must stop TX and never
                # reopen FE admission, even if the request client exits.
                event_start=len(accel_events());admin_fault.write_text('io')
                admin('block',error=errno.EIO)
                _,error=process.communicate(timeout=3)
                assert process.returncode==1 and status()['error']==-errno.EIO,error
                assert all(mode==0 for mode,_,_ in accel_events()[event_start:])
                assert not ports() and device.joinpath('tx_enable').read_text().startswith('0\n')
                assert not store.joinpath('control.sock').exists()
                admin_fault.unlink();hardware();process=launch()
                until(lambda s:s.get('onus') and all(s['onus'][id]['phase']=='configured' for id in nodes))
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
            assert struct.unpack_from('<II',device.joinpath('tcont_state').read_bytes(),(7*8+physical_slot)*8)==(0,0)
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
                f.seek((7*8+physical_slot)*8);f.write(bytes(8))
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
    report={'backend':args.backend,'resident_onu_admin_checked':bool(args.admin_control),
        'master_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),
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
        'acceleration withdrawal occurs with the old VLAN still present, resumes after removal, and an unconfirmed withdrawal stops TX without resuming'] +
        (['resident administration rejects stale IDs and malformed commands before FE changes',
          'block/resume removes owned GEM VLAN and DBA before backend IO while retaining the healthy peer',
          'temporary release waits for FPGA deregistration; ASIC polling, superseding block, replacement and shutdown are checked',
          'upstream error recovery uses original backend/high-bit conditions and retires FE/VLAN/DBA; permanent blocks remain blocked',
          'driver-side stale serial permits fresh inventory; hardware IO failure stops TX without resuming FE'] if args.admin_control else []),
        'scope':'native daemon over AF_PACKET/veth in a private namespace with original ARM64 schemas and VLAN table replies; peer MIB and synchronous DBA sysfs are modeled; no optical traffic'}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
