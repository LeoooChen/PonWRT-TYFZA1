#!/usr/bin/env python3
"""Execute target FTTR MIB status restore/reset; distinguish it from service policy."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from elftools.elf.elffile import ELFFile
from unicorn.arm64_const import UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_SP
from arm64_elf_oracle import ElfOracle


def metadata(binary):
    with binary.open('rb') as stream:
        elf=ELFFile(stream);symbols=elf.get_section_by_name('.dynsym')
        def read(address,size):
            for segment in elf.iter_segments():
                offset=address-segment['p_vaddr']
                if segment['p_type']=='PT_LOAD' and 0<=offset and offset+size<=segment['p_filesz']:
                    return segment.data()[offset:offset+size]
            raise AssertionError(('unmapped metadata',hex(address),size))
        table=symbols.get_symbol_by_name('mib_chain_record_table')[0]
        entries=[]
        for pos in range(0,table['st_size'],80):
            data=read(table['st_value']+pos,80)
            if struct.unpack_from('<I',data)[0]!=3209:continue
            name=data[8:40].split(b'\0')[0].decode()
            size,limit=struct.unpack_from('<Ii',data,40)
            desc=struct.unpack_from('<Q',data,72)[0]
            fields=[]
            for i in range(32):
                field=read(desc+i*56,56);label=field[:36].split(b'\0')[0]
                if not label:break
                kind,offset,width=struct.unpack_from('<III',field,36)
                assert offset+width<=size
                fields.append(dict(name=label.decode(),type=kind,offset=offset,size=width))
            entries.append(dict(id=3209,name=name,size=size,limit=limit,fields=fields))
        assert len(entries)==1 and entries[0]['name']=='FTTR_MASTER_ONU_TBL'
        assert (entries[0]['size'],entries[0]['limit'])==(107,16)
        return entries[0]


class Status:
    def __init__(self,binary):
        copies={}
        with binary.open('rb') as stream:
            elf=ELFFile(stream);symbols=elf.get_section_by_name('.dynsym')
            for section in elf.iter_sections():
                if section['sh_type']!='SHT_RELA':continue
                for relocation in section.iter_relocations():
                    if relocation['r_info_type']==1024:
                        symbol=symbols.get_symbol(relocation['r_info_sym']);copies[symbol.name]=bytes(symbol['st_size'])
        self.rows=[];self.events=[];self.failure=0;self.missing=-1
        self.oracle=ElfOracle(binary,self.boundary,copy_symbols=copies)
        self.oracle.symbols.update(load_status=0x406208,reset_status=0x4062a4)
        # External port0 name is supplied by an earlier platform initializer.
        self.oracle.uc.mem_write(self.oracle.scratch+0x8000,b'eth0.2\0')
        self.oracle.uc.mem_write(0x430c68,struct.pack('<Q',self.oracle.scratch+0x8000))
        assert self.oracle.uc.mem_read(0x406208,4)==bytes.fromhex('fd7bb7a9')
        assert self.oracle.uc.mem_read(0x4062a4,4)==bytes.fromhex('ff8306d1')

    def string(self,address):
        return bytes(self.oracle.uc.mem_read(address,256)).split(b'\0')[0].decode()

    def boundary(self,name,a):
        u=self.oracle.uc
        if name=='mib_chain_total':assert a[0]==3209;return len(self.rows)
        if name=='mib_chain_get':
            assert a[0]==3209
            self.events.append(['read',a[1]])
            if a[1]==self.missing:return 0
            u.mem_write(a[2],self.rows[a[1]]);return 1
        if name=='mib_chain_update':
            assert a[0]==3209
            self.rows[a[2]]=bytes(u.mem_read(a[1],107));self.events.append(['update',a[2]]);return 1
        if name=='memset':u.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
        if name in ('memcpy','strncpy'):
            data=bytes(u.mem_read(a[1],a[2]))
            if name=='strncpy':data=data.split(b'\0')[0].ljust(a[2],b'\0')
            u.mem_write(a[0],data);return a[0]
        if name=='rtk_fc_external_switch_rgmii_port_get':return 0
        if name=='snprintf':
            assert self.string(a[2])=='%s %d %d %s %s %d'
            x6,x7=u.reg_read(UC_ARM64_REG_X6),u.reg_read(UC_ARM64_REG_X7)
            ident=struct.unpack('<I',u.mem_read(u.reg_read(UC_ARM64_REG_SP),4))[0]
            value=f'{self.string(a[3])} {a[4]} {a[5]} {self.string(x6)} {self.string(x7)} {ident}'.encode()
            assert len(value)<a[1];u.mem_write(a[0],value+b'\0');return len(value)
        if name=='va_cmd':
            assert self.string(a[0])=='/bin/sh' and self.string(a[3])=='-c'
            self.events.append(['command',self.string(a[4])]);return self.failure
        raise AssertionError(('unmodeled saved-state call',name,a))

    def run(self,rows,reset=False,failure=0,missing=-1):
        self.rows=list(rows);self.events=[];self.failure=failure;self.missing=missing
        self.oracle.uc.mem_write(0x4335c0,bytes(16*264))
        ret=self.oracle.call('reset_status' if reset else 'load_status')
        return ret,list(self.events),bytes(self.oracle.uc.mem_read(0x4335c0,16*264)),list(self.rows)


def record(active,ident=6):
    row=bytearray(107)
    struct.pack_into('<I',row,0,ident);row[4:10]=bytes.fromhex('020304050607')
    row[10:14]=b'test';row[35:47]=b'TEST00000006';row[51]=active
    struct.pack_into('<I',row,52,4006);row[56:62]=b'ext0.6';row[86]=1
    struct.pack_into('<Q',row,26,123456);struct.pack_into('<Q',row,87,654321);struct.pack_into('<Q',row,95,987654)
    struct.pack_into('<I',row,103,19)
    return bytes(row)


def tcont_memory_save(binary):
    """Run the complete named save routine and real list/counter/mask helpers."""
    events=[];next_node=0;missing=False;allocation_fail=False
    def boundary(name,a):
        nonlocal next_node
        u=oracle.uc
        if name=='plr_ponsrv_cfg_onu_data_get':return 0 if missing else storage
        if name=='plr_mem_alloc':
            assert a[0]==34
            if allocation_fail:return 0
            node=oracle.scratch+0x10000+next_node*128;next_node+=1
            u.mem_write(node,bytes(128));events.append(['allocate',34]);return node
        if name=='plr_mem_free':events.append(['free']);return 0
        if name=='plr_gpon_maxpon_get':return 1
        raise AssertionError(('unexpected save IO',name,a))
    oracle=ElfOracle(binary,boundary,('plr_ponsrv_cfg_onu_data_get','plr_mem_alloc','plr_mem_free','plr_gpon_maxpon_get'))
    storage,pon,profile=(oracle.scratch+i for i in (0x2000,0x4000,0x8000))
    oracle.uc.mem_write(0x175cf8,struct.pack('<Q',pon))
    cases=[]
    for slot in (1,7):
        for fixed,assured,maximum in ((0,50,100),(10,20,500)):
            for fault in ('none','missing-onu','allocation'):
                missing=fault=='missing-onu';allocation_fail=fault=='allocation';events.clear()
                oracle.uc.mem_write(storage,bytes(1024));oracle.uc.mem_write(pon,bytes(8192));oracle.uc.mem_write(profile,bytes(128))
                oracle.uc.mem_write(profile+52,struct.pack('<4I',3,fixed,assured,maximum))
                alloc=256+slot
                # Packed10-byte T-CONT save argument: Alloc-ID then profile pointer.
                packed=struct.pack('<HQ',alloc,profile).ljust(16,b'\0')
                args=struct.unpack('<QQ',packed)
                result=oracle.call('plr_ponsrv_tcont_data_save',1,slot,*args)
                assert result==(0 if fault=='none' else 1)
                if fault=='none':
                    node=struct.unpack('<Q',oracle.uc.mem_read(storage+245,8))[0]
                    assert node and struct.unpack('<I',oracle.uc.mem_read(node+16,4))[0]==slot
                    assert bytes(oracle.uc.mem_read(node+20,10))==packed[:10]
                    assert struct.unpack('<II',oracle.uc.mem_read(pon+134,8))==(fixed+assured,fixed)
                    assert oracle.uc.mem_read(pon+142+alloc//8,1)[0] & (1<<(alloc%8))
                    before=bytes(oracle.uc.mem_read(pon,8192))
                    # A duplicate node fails and frees its new allocation, not flash.
                    assert oracle.call('plr_ponsrv_tcont_data_save',1,slot,*args)==1
                    assert events==[['allocate',34],['allocate',34],['free']]
                    assert bytes(oracle.uc.mem_read(pon,8192))==before
                    assert struct.unpack('<I',oracle.uc.mem_read(profile+48,4))[0]==1
                else:assert not events
                cases.append(dict(slot=slot,profile=[fixed,assured,maximum],fault=fault,result=result,events=list(events)))
    return dict(sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),cases=cases,
        scope='Actual save, ordered-list insertion, reference counter, used-bandwidth accounting and Alloc-ID bitmap. Heap and configured-ONU lookup are explicit boundaries; no file/MIB persistence call occurred in these executed paths.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--configd',type=Path,required=True);p.add_argument('--caller',type=Path,required=True)
    p.add_argument('--relay',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    desc=metadata(args.configd)
    assert {f['name']:(f['offset'],f['size']) for f in desc['fields']}=={
        'onuId':(0,4),'mac':(4,6),'lastDownCause':(10,16),'lastDyingGaspTimeTick':(26,8),
        'optErrStatus':(34,1),'onuSn':(35,16),'active':(51,1),'intVID':(52,4),'intDevName':(56,30),
        'isRuleAdded':(86,1),'lastActiveTimeTick':(87,8),'lastDeActiveTimeTick':(95,8),'sfuId':(103,4)}
    stock=Status(args.caller);cases=[]
    for active in (0,1):
        for ident in (0,1,16):
            raw=record(active,ident);ret,events,out,rows=stock.run([raw])
            assert ret==1 and events==[['read',0]] and rows==[raw]
            assert out[0]==active and out[1:7]==raw[4:10] and out[7:23]==raw[35:51]
            assert struct.unpack_from('<I',out,24)[0]==(ident if active else 0)
            assert struct.unpack_from('<H',out,40)[0]==4006 and out[42:62]==raw[56:76]
            assert out[68]==1 and out[72:80]==raw[87:95] and out[80:88]==raw[26:34] and out[88:96]==raw[95:103]
            cases.append(dict(operation='status-load',active=active,id=ident,service_calls=0))
    for failure in (0,1,127):
        initial=[record(0),record(1)]
        ret,events,out,rows=stock.run(initial,True,failure)
        assert ret==0 and rows[0]==initial[0]
        expected=bytearray(initial[1])
        for offset,size in ((0,4),(51,1),(52,4),(56,30),(86,1)):expected[offset:offset+size]=bytes(size)
        assert rows[1]==bytes(expected)
        assert [e[0] for e in events]==['read','read','command','update']
        command=events[2][1];assert command.startswith('/etc/scripts/onu_setup.sh 2 4006 ') and command.endswith(' ext0.6 6')
        cases.append(dict(operation='reset-old-interface',command_error=failure,events=events,
            result=ret,clears_active_despite_command_error=True))
    ret,events,out,rows=stock.run([record(1)]*17)
    assert ret==16 and events==[['read',i] for i in range(16)]
    ret,events,out,rows=stock.run([record(1),record(1)],missing=0)
    assert ret==2 and not any(out[:264]) and out[264]==1
    report=dict(tcont_memory_save=tcont_memory_save(args.relay),table=desc,cases=cases,loader_bound=16,missing_record_skipped=True,
        caller_sha256=hashlib.sha256(args.caller.read_bytes()).hexdigest(),configd_sha256=hashlib.sha256(args.configd.read_bytes()).hexdigest(),
        finding='FTTR_MASTER_ONU_TBL is persisted operational status, not authentication/service intent. The status loader restores observations only. The separate reset path invokes inactive-interface cleanup and clears active/VID/interface fields even when its shell command fails.',
        scope='Target configd descriptors and complete target ONU status loader/reset functions, with synthetic rows and command boundary only. No shell command, filesystem mutation or device operation was executed by the target. This does not prove absence of other factory policy sources.')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print('Original saved state:13 field descriptors,6 status loads,3 reset outcomes,16-row bound;12 memory-only T-CONT save cases passed')
if __name__=='__main__':main()
