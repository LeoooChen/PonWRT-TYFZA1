#!/usr/bin/env python3
"""Compare original profile/UNI producers and verify native Create/Set/readback groups."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_gem import Job, ObjectsJob, Me, Message
from test_fttr_mib import Stock


class Profiles(C.Structure):
    _fields_=[('bridge',C.c_ushort),('mapper',C.c_ushort),('per_uni',C.c_bool),('use_mapper',C.c_bool)]


class Uni(C.Structure):
    _fields_=[('entity',C.c_ushort),('uni',C.c_ushort),('bridge',C.c_ushort),('ordinal',C.c_ushort),('veip',C.c_bool)]


class Ani(C.Structure):
    _fields_=[('entity',C.c_ushort),('bridge',C.c_ushort),('pointer',C.c_ushort),('vlans',C.c_ushort*12),
              ('port',C.c_ubyte),('count',C.c_ubyte),('mapper',C.c_bool)]


class Mapper(C.Structure):
    _fields_=[('entity',C.c_ushort),('interworking',C.c_ushort*8),('priorities',C.c_ubyte)]


class Original(Stock):
    def __init__(self,binary):
        self.oracle=ElfOracle(binary,self.handler,('plr_log','plr_gponomci_module_log','plr_gponrm_pon_protect_is_standby_port',
            'plr_gponrm_onu_hw_id_get_by_lg_id','plr_gponrm_dft_mbs_mgmt_get',
            'plr_gponrm_card_eth_uni_get_by_card_uni_id','plr_gponrm_card_veip_uni_get_by_card_uni_id',
            'plr_gponomci_sync_api','plr_gponomci_me_high_prio_tx'))
        self.oracle.symbols.update(shared=0xdd7a4,per_eth=0xdd260,per_veip=0xdd490,mapper=0xd7498,
            eth=0xfdc84,veip=0xfdf88,ani=0xd8320,mapper_update=0xd78d0,stock_create=0x6535c,stock_set=0x64f8c)
        self.data=self.oracle.scratch+0x2000
        assert self.oracle.call('plr_omci_sw_crc32_table_init')==0

    def handler(self,name,a):
        u=self.oracle.uc
        if name in ('plr_log','plr_gponomci_module_log','plr_gponrm_pon_protect_is_standby_port'):return 0
        if name=='plr_gponrm_onu_hw_id_get_by_lg_id':u.mem_write(a[1],struct.pack('<I',5));return 0
        if name=='plr_gponrm_dft_mbs_mgmt_get':return self.data+1500
        if name in ('plr_gponrm_card_eth_uni_get_by_card_uni_id','plr_gponrm_card_veip_uni_get_by_card_uni_id'):
            return self.data+1024
        if name=='plr_gponomci_sync_api':
            self.calls.append((a[0],bytes(u.mem_read(a[1],a[2]))));u.mem_write(a[3],bytes(a[4]));return 0
        return super().handler(name,a)

    def producer(self,name):
        o,p=self.oracle,self.data
        o.uc.mem_write(p,bytes(2048));o.uc.mem_write(p,struct.pack('<HH',0,5))
        cfg=bytearray(128);struct.pack_into('<I',cfg,0,7);struct.pack_into('<H',cfg,8,2);struct.pack_into('<H',cfg,16,3)
        o.uc.mem_write(p+64,bytes(cfg))
        o.uc.mem_write(p+1024+18,struct.pack('<H',0x107));o.uc.mem_write(p+1024+21,b'\xff\xff')
        if name=='per_veip':
            o.uc.mem_write(p+1024+16,struct.pack('<HH',0x107,0xffff))
        if name=='mapper':
            o.uc.mem_write(p+64,struct.pack('<HB',0x42,1));o.uc.mem_write(p+160,struct.pack('<Q',p+1024))
        if name in ('eth','veip'):
            o.uc.mem_write(p+1024+16,struct.pack('<H',0x107))
            o.uc.mem_write(p+1024+(55 if name=='eth' else 52),struct.pack('<H',0x321))
            o.uc.mem_write(p+160,struct.pack('<QQ',p+1024,p+1024))
            cfg=bytearray(32);struct.pack_into('<I',cfg,8,0 if name=='eth' else 2)
            struct.pack_into('<H',cfg,12,2);struct.pack_into('<H',cfg,16,0x8080);o.uc.mem_write(p+64,bytes(cfg))
        self.calls=[];assert o.call(name,p,p+64,p+160)==0
        frames=[]
        for c,ram in self.calls:
            o.uc.mem_write(p+4096,ram);action=struct.unpack_from('<I',ram,4)[0]
            assert o.call('stock_set' if action==1 else 'stock_create',0x8123,c,p+4096,p+4106,len(ram)-10)==0x80000000
            frames.append(self.body)
        return frames

    def encode_calls(self):
        o,p=self.oracle,self.data;frames=[]
        for c,ram in self.calls:
            o.uc.mem_write(p+4096,ram);action=struct.unpack_from('<I',ram,4)[0]
            assert o.call('stock_set' if action==1 else 'stock_create',0x8123,c,p+4096,p+4106,len(ram)-10)==0x80000000
            frames.append(self.body)
        return frames

    def ani(self,config):
        o,p=self.oracle,self.data;o.uc.mem_write(p,bytes(2048));o.uc.mem_write(p,struct.pack('<HH',0,5))
        cfg=bytearray(64);struct.pack_into('<HH',cfg,4,config.bridge,config.pointer)
        struct.pack_into('<H',cfg,10,config.count);struct.pack_into('<12H',cfg,12,*config.vlans)
        o.uc.mem_write(p+64,bytes(cfg));o.uc.mem_write(p+160,struct.pack('<QQ',p+1024,p+1280))
        o.uc.mem_write(p+1024+18,struct.pack('<H',config.pointer if config.mapper else 0xffff))
        o.uc.mem_write(p+1280+18,struct.pack('<HB',config.entity,config.port))
        self.calls=[];assert o.call('ani',p,p+64,p+160)==0
        return self.encode_calls()

    def mapping(self,config,explicit_clear=False):
        o,p=self.oracle,self.data;o.uc.mem_write(p,bytes(2048));o.uc.mem_write(p,struct.pack('<HH',0,5));self.calls=[]
        if explicit_clear:
            mask=sum(0x4000>>i for i in range(8) if config.priorities&(1<<i))
            ram=bytearray(51);struct.pack_into('<HHH',ram,0,config.entity,mask,mask)
            for i,value in enumerate(config.interworking):struct.pack_into('<H',ram,8+2*i,value)
            o.uc.mem_write(p+512,bytes(ram));assert o.call('plr_gponrm_pr_map_set',p,mask,p+512)==0
        else:
            cfg=bytearray(32)
            for i,value in enumerate(config.interworking):struct.pack_into('<H',cfg,4+2*i,value if config.priorities&(1<<i) else 0xffff)
            o.uc.mem_write(p+64,bytes(cfg));o.uc.mem_write(p+160,struct.pack('<Q',p+1024))
            o.uc.mem_write(p+1024+18,struct.pack('<H',config.entity))
            assert o.call('mapper_update',p,p+64,p+160)==0
        return self.encode_calls()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();stock=Original(args.stock)
    gal=stock.producer('plr_gponrm_gal_add');shared=stock.producer('shared');per=stock.producer('per_eth')
    assert per==stock.producer('per_veip')
    mapper=stock.producer('mapper');eth=stock.producer('eth');veip=stock.producer('veip')
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory);source=root/'port/packages/tyfza1-fttr/src'
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-o',str(work/'association.so')]+
            [str(source/n) for n in ('omci-association.c','omci-objects.c','omci-me.c','omci-wire.c','omci-socket.c','miniolt.c')],check=True)
        lib=C.CDLL(str(work/'association.so'))
        lib.fttr_profiles_job_start.argtypes=[C.POINTER(Job),C.c_ushort,C.POINTER(C.c_ushort),C.POINTER(Profiles),C.c_char_p]
        lib.fttr_uni_job_start.argtypes=[C.POINTER(Job),C.c_ushort,C.POINTER(C.c_ushort),C.POINTER(Uni),C.c_char_p]
        lib.fttr_ani_job_start.argtypes=[C.POINTER(Job),C.c_ushort,C.POINTER(C.c_ushort),C.POINTER(Ani),C.c_char_p]
        lib.fttr_mapper_job_start.argtypes=[C.POINTER(Job),C.c_ushort,C.POINTER(C.c_ushort),C.POINTER(Mapper),C.c_char_p]
        lib.fttr_objects_job_update.argtypes=[C.POINTER(ObjectsJob),C.POINTER(C.c_ushort)]
        lib.fttr_objects_job_release.argtypes=lib.fttr_objects_job_update.argtypes
        lib.fttr_me_job_reply.argtypes=[C.POINTER(Me),C.POINTER(Message)]
        serial=bytes.fromhex('5445535401234567')

        def start(config):
            job,counter=Job(),C.c_ushort(0x8122)
            constructor={Uni:lib.fttr_uni_job_start,Profiles:lib.fttr_profiles_job_start,
                         Ani:lib.fttr_ani_job_start,Mapper:lib.fttr_mapper_job_start}[type(config)]
            constructor(C.byref(job),5,C.byref(counter),C.byref(config),serial)
            return job,counter

        def drive(job,counter,existing=None,corrupt=False,action=1,restore_reject=False):
            # Actual native ME request/reply validation, with the peer's ME
            # storage modeled; AF_PACKET command integration is checked separately.
            pool={} if existing is None else dict(existing);frames=[];reads=[]
            while action==1:
                assert len(frames)<50
                body=bytes(job.transaction.me.request);frames.append(body)
                tci,op,device,c,instance=struct.unpack('>HBBHH',body[:8]);key=c,instance
                obj=next(x for x in job.objects[:job.transaction.count] if (x.class_id,x.instance)==key)
                group=obj.set if op==0x48 or (op==0x49 and body[8:10]==struct.pack('>H',obj.set.mask)) else obj.create
                status=0;content=b''
                if op==0x44:
                    job.transaction.me.attempts=1
                    assert lib.fttr_objects_job_update(C.byref(job.transaction),C.byref(counter))==0
                    pool[key]={obj.create.mask:body[8:8+obj.create.size]}
                elif op==0x48:
                    job.transaction.me.attempts=1
                    assert lib.fttr_objects_job_update(C.byref(job.transaction),C.byref(counter))==0
                    group=obj.create if obj.update_only else obj.set
                    assert body[8:10]==struct.pack('>H',group.mask)
                    if restore_reject and job.transaction.phase==7:status=6
                    else:pool[key][group.mask]=body[10:10+group.size]
                elif op==0x46:pool.pop(key,None)
                else:
                    assert op==0x49
                    if key not in pool:status=5
                    else:
                        data=pool[key].get(group.mask,bytes(group.size));reads.append(len(data))
                        if corrupt and ((c==171 and group.mask==0x3100) or (c==130 and job.transaction.phase==3)):
                            data=data[:-1]+bytes([data[-1]^1])
                        content=body[8:10]+data
                reply=stock.frame(tci,(op & 0x1f)|0x20,bytes([status])+content,class_id=c,instance=instance)[16:60]
                buffer=C.create_string_buffer(reply);message=Message(5,C.addressof(buffer),44)
                assert lib.fttr_me_job_reply(C.byref(job.transaction.me),C.byref(message))!=0
                action=lib.fttr_objects_job_update(C.byref(job.transaction),C.byref(counter))
            return action,frames,pool,reads

        for config,expected in [(Profiles(0x8080,0,False,False),gal+shared),
                                (Profiles(0x107,0,True,False),gal+per),
                                (Profiles(0x8080,0x42,False,True),gal+shared+mapper),
                                (Uni(0x321,0x107,0x8080,2,False),eth),(Uni(0x321,0x107,0x8080,2,True),veip)]:
            job,counter=start(config);action,frames,pool,reads=drive(job,counter)
            assert action==2 and job.transaction.phase==9
            assert [b[2:] for b in frames if b[2] in (0x44,0x48)]==[b[2:] for b in expected]
            if isinstance(config,Uni):assert 28 in reads
            assert drive(job,counter,pool,action=lib.fttr_objects_job_release(C.byref(job.transaction),C.byref(counter)))[2]=={}
        # GAL and bridge are independent: reuse the bridge while creating GAL.
        job,counter=start(Profiles(0x8080,0,False,False));bridge=job.objects[1]
        pool={(45,0x8080):{bridge.create.mask:bytes(bridge.create.value[:bridge.create.size])}}
        action,frames,pool,reads=drive(job,counter,pool)
        assert action==2 and not bridge.touched and [struct.unpack('>H',b[4:6])[0] for b in frames if b[2]==0x44]==[272]
        job,counter=start(Uni(0x321,0x107,0x8080,2,False))
        action,frames,pool,reads=drive(job,counter,corrupt=True)
        assert action==-errno.EUCLEAN and not pool and job.transaction.cleanup_error==0
        assert struct.unpack('>H',bytes(job.transaction.failure.request)[8:10])[0]==0x3100
        for use_mapper,count in [(False,0),(False,2),(True,2)]:
            config=Ani(0x322,0x8080,0x42 if use_mapper else 0x117,(C.c_ushort*12)(100,200),17,count,use_mapper)
            expected=stock.ani(config);job,counter=start(config)
            action,frames,pool,reads=drive(job,counter)
            assert action==2 and [b[2:] for b in frames if b[2]==0x44]==[b[2:] for b in expected]
            if count:assert 26 in reads
        for mask in (1,0xa5,0xff):
            config=Mapper(0x42,(C.c_ushort*8)(*[0x117+i for i in range(8)]),mask)
            expected=stock.mapping(config);job,counter=start(config);obj=job.objects[0]
            previous=b'\xff'*obj.create.size;pool={(130,0x42):{obj.create.mask:previous}}
            action,frames,pool,reads=drive(job,counter,pool)
            assert action==2 and obj.touched and obj.have_previous
            assert [b[2:] for b in frames if b[2]==0x48]==[b[2:] for b in expected]
            action,frames,pool,reads=drive(job,counter,pool,action=lib.fttr_objects_job_release(C.byref(job.transaction),C.byref(counter)))
            assert action==2 and obj.presence==4 and not obj.touched and pool[130,0x42][obj.create.mask]==previous
        config=Mapper(0x42,(C.c_ushort*8)(*[0xffff]*8),0xa5)
        expected=stock.mapping(config,explicit_clear=True);job,counter=start(config);obj=job.objects[0]
        pool={(130,0x42):{obj.create.mask:b'\x01\x17'*4}}
        action,frames,pool,reads=drive(job,counter,pool)
        assert action==2 and [b[2:] for b in frames if b[2]==0x48]==[b[2:] for b in expected]
        config=Mapper(0x42,(C.c_ushort*8)(*[0x117]*8),0xff)
        job,counter=start(config);obj=job.objects[0]
        action,frames,pool,reads=drive(job,counter,{(130,0x42):{obj.create.mask:b'\x01\x17'*8}})
        assert action==2 and not obj.touched and all(b[2]==0x49 for b in frames)
        for reject in (False,True):
            job,counter=start(config);obj=job.objects[0];previous=b'\xff'*16
            pool={(130,0x42):{obj.create.mask:previous}}
            action,frames,pool,reads=drive(job,counter,pool,corrupt=True,restore_reject=reject)
            assert action==-errno.EUCLEAN and job.transaction.error==-errno.EUCLEAN
            assert obj.touched==reject and obj.have_previous
            if reject:assert job.transaction.cleanup_error==-errno.EREMOTEIO
            else:assert obj.presence==4 and pool[130,0x42][obj.create.mask]==previous
        # Original Get parser limits attributes to 25 bytes even though this
        # selected group occupies 28. It returns success with DSCP unchanged.
        o,p=stock.oracle,stock.data
        body=stock.frame(0x8123,0x29,b'\0\x31\0'+bytes.fromhex('81008100')+bytes(24),class_id=171,instance=0x321)[16:60]
        o.uc.mem_write(p,body);o.uc.mem_write(p+4096,b'\xa5'*256)
        assert o.call('plr_gponomci_me_getcont_parse',171,p,p+4096,256)==0x80000000
        parsed=bytes(o.uc.mem_read(p+4096,128));offset=parsed.index(bytes.fromhex('00810081'))+4
        assert parsed[offset:offset+24]==b'\xa5'*24
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256':hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'GAL_shared_per_uni_and_mapper_match_original':True,'ethernet_and_VEIP_create_set_match_original':True,
        'independent_profile_reuse_verified':True,'native_verifies_all_28_set_attribute_bytes':True,
        'original_25_byte_get_limit_false_success_reproduced':True,
        'ANI_direct_mapper_and_VLAN_lists_match_original':True,'partial_all_and_clear_Mapper_masks_match_original':True,
        'Mapper_previous_values_restored_on_failure_or_release':True,'Mapper_restore_failure_retains_mutation_record':True,
        'scope':'Original producers/wrappers/encoders with cache, logging and API boundary modeled; native protocol job and ME validation.',
        'optical_hardware_verified':False},indent=2)+'\n')
    print('Association: original profiles/UNI/ANI/Mapper bytes, complete readback and attribute restoration passed')


if __name__=='__main__':main()
