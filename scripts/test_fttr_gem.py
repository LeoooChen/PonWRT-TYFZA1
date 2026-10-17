#!/usr/bin/env python3
"""Compare original GEM producers and exercise native ownership/cleanup over datagrams."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_mib import Stock, HOST, OLT


class Original(Stock):
    def __init__(self,binary):
        self.oracle=ElfOracle(binary,self.handler,('plr_log','plr_gponrm_pon_protect_is_standby_port',
            'plr_gponrm_onu_hw_id_get_by_lg_id','plr_gponomci_sync_api','plr_gponomci_me_high_prio_tx'))
        self.oracle.symbols.update(producer=0xdffd0,cleanup=0xdfe98,stock_create=0x6535c,stock_delete=0x659c0)
        self.oracle.hooks[0xdfb4c]='release_tracking'
        self.data=self.oracle.scratch+0x2000
        assert self.oracle.call('plr_omci_sw_crc32_table_init')==0

    def handler(self,name,a):
        u=self.oracle.uc
        if name in ('plr_log','plr_gponrm_pon_protect_is_standby_port'):return 0
        if name=='release_tracking':self.released=True;return 0
        if name=='plr_gponrm_onu_hw_id_get_by_lg_id':u.mem_write(a[1],struct.pack('<I',5));return 0
        if name=='plr_gponomci_sync_api':
            ram=bytes(u.mem_read(a[1],a[2]));action=struct.unpack_from('<I',ram,4)[0]
            status=2 if self.reject and a[0]==266 and action==3 else 7 if self.reject and action==4 else 0
            self.calls.append((a[0],ram,status))
            u.mem_write(a[3],bytes(a[4]));u.mem_write(a[3]+10,struct.pack('<I',status));return 0
        return super().handler(name,a)

    def producer(self,config,reject=False,release=False):
        o,p=self.oracle,self.data;data=bytearray(64)
        for off,value in [(8,config.entity),(10,config.tcont),(12,config.queue),(16,config.descriptor),
                          (44,config.profile),(46,config.gal)]:struct.pack_into('<H',data,off,value)
        struct.pack_into('<I',data,24,config.committed//125)
        struct.pack_into('<I',data,32,config.peak//125);data[42]=config.bridge
        o.uc.mem_write(p,bytes(1024));o.uc.mem_write(p,struct.pack('<HH',0,5));o.uc.mem_write(p+64,bytes(data))
        o.uc.mem_write(p+160,struct.pack('<Q',p+256))
        # dfe98 uses ffff for absent CTP/IW IDs, but zero for no descriptor.
        o.uc.mem_write(p+256,bytes(18)+struct.pack('<HHH',0xffff,0xffff,0))
        self.calls=[];self.reject=reject;self.released=False
        ret=o.call('producer',p,p+64,p+160)
        if release:o.call('cleanup',p,p+64,p+160)
        frames=[]
        for c,ram,status in self.calls:
            o.uc.mem_write(p+512,ram)
            action=struct.unpack_from('<I',ram,4)[0]
            args=[0x8123,c,p+512,struct.unpack_from('<H',ram,10)[0]] if action==4 else [0x8123,c,p+512,p+522,len(ram)-10]
            assert o.call('stock_delete' if action==4 else 'stock_create',*args)==0x80000000
            frames.append(self.body)
        return ret,frames


class Config(C.Structure):
    _fields_=[('entity',C.c_ushort),('tcont',C.c_ushort),('queue',C.c_ushort),('profile',C.c_ushort),
              ('gal',C.c_ushort),('descriptor',C.c_ushort),('committed',C.c_uint),('peak',C.c_uint),('bridge',C.c_bool)]


class Me(C.Structure):
    _fields_=[('state',C.c_int),('onu',C.c_ushort),('transaction',C.c_ushort),('mask',C.c_ushort),
              ('attempts',C.c_uint),('deadline',C.c_int64),('error',C.c_int),('replied',C.c_bool),
              ('result',C.c_ubyte),('request',C.c_ubyte*44),('reply',C.c_ubyte*44)]


class Object(C.Structure):
    _fields_=[('class_id',C.c_ushort),('instance',C.c_ushort),('mask',C.c_ushort),('size',C.c_ubyte),
              ('value',C.c_ubyte*20),('presence',C.c_int),('touched',C.c_bool)]


class Job(C.Structure):
    _fields_=[('me',Me),('failure',Me),('cleanup_failure',Me),('objects',Object*3),('serial',C.c_ubyte*8),
              ('phase',C.c_int),('count',C.c_int),('index',C.c_int),('error',C.c_int),('cleanup_error',C.c_int)]


class Link(C.Structure):
    _fields_=[('host',C.c_ubyte*6),('olt',C.c_ubyte*6),('ethertype',C.c_ushort),('length',C.c_bool)]


class Message(C.Structure):
    _fields_=[('onu',C.c_ushort),('body',C.c_void_p),('length',C.c_size_t)]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();stock=Original(args.stock)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory);source=root/'port/packages/tyfza1-fttr/src'
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-o',str(work/'gem.so')]+
            [str(source/n) for n in ('omci-gem.c','omci-me.c','omci-wire.c','omci-socket.c','miniolt.c')],check=True)
        lib=C.CDLL(str(work/'gem.so'))
        lib.fttr_gem_job_start.argtypes=[C.POINTER(Job),C.c_ushort,C.POINTER(C.c_ushort),C.POINTER(Config),C.c_char_p]
        lib.fttr_gem_job_update.argtypes=[C.POINTER(Job),C.POINTER(C.c_ushort)]
        lib.fttr_gem_job_release.argtypes=lib.fttr_gem_job_update.argtypes
        lib.fttr_me_job_send.argtypes=[C.POINTER(Me),C.c_int,C.POINTER(Link),C.c_int64]
        lib.fttr_me_job_timeout.argtypes=lib.fttr_me_job_send.argtypes
        lib.fttr_me_job_reply.argtypes=[C.POINTER(Me),C.POINTER(Message)]
        lib.fttr_omci_receive.argtypes=[C.c_int,C.c_ushort,C.c_void_p,C.c_size_t,C.POINTER(Message)]
        lib.fttr_gem_job_dump.argtypes=[C.POINTER(Job),C.c_void_p]
        lib.fttr_miniolt_gem_id.argtypes=[C.c_uint,C.c_uint];lib.fttr_miniolt_gem_id.restype=C.c_ushort
        link=Link((C.c_ubyte*6)(*HOST),(C.c_ubyte*6)(*OLT),0x8888,False)
        serial=bytes.fromhex('5445535401234567')

        def start(config):
            job,counter=Job(),C.c_ushort(0x8122)
            lib.fttr_gem_job_start(C.byref(job),5,C.byref(counter),C.byref(config),serial)
            return job,counter

        def drive(job,counter,objects,fault='',action=1):
            client,server=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
            client.setblocking(False);server.settimeout(1);sent=[];now=1000
            try:
                while action==1:
                    assert len(sent)<40
                    assert lib.fttr_me_job_send(C.byref(job.me),client.fileno(),C.byref(link),now)==0
                    assert lib.fttr_gem_job_update(C.byref(job),C.byref(counter))==0
                    frame=server.recv(2048);body=frame[16:60];sent.append(body)
                    assert frame[:16]==OLT+HOST+bytes.fromhex('88880005')
                    transaction,opcode,device,c,instance=struct.unpack('>HBBHH',body[:8]);key=c,instance
                    definition=next(x for x in job.objects[:job.count] if (x.class_id,x.instance)==key)
                    expected=bytes(definition.value[:definition.size]);status=0
                    if opcode==0x44:
                        if fault=='create-and-delete-reject' and c==266:status=2
                        else:objects[key]=body[8:8+len(expected)]
                        if fault=='lost-create-reply' and c==268:
                            for deadline in (now+2000,now+4000):
                                assert lib.fttr_me_job_timeout(C.byref(job.me),client.fileno(),C.byref(link),deadline)==0
                                assert server.recv(2048)==frame
                            assert lib.fttr_me_job_timeout(C.byref(job.me),client.fileno(),C.byref(link),now+6000)==-errno.ETIMEDOUT
                            action=lib.fttr_gem_job_update(C.byref(job),C.byref(counter));now+=6000;continue
                    elif opcode==0x46:
                        if fault=='create-and-delete-reject' and c==268:status=7
                        elif fault!='false-delete-success':objects.pop(key,None)
                    else:
                        assert opcode==0x49 and body[8:10]==struct.pack('>H',definition.mask)
                        if key not in objects:status=5
                    content=bytes([status])
                    if opcode==0x49 and not status:content+=body[8:10]+objects[key]
                    server.send(stock.frame(transaction,(opcode & 0x1f)|0x20,content,class_id=c,instance=instance))
                    buffer=C.create_string_buffer(1996);message=Message()
                    assert lib.fttr_omci_receive(client.fileno(),0x8888,buffer,len(buffer),C.byref(message))==0
                    assert lib.fttr_me_job_reply(C.byref(job.me),C.byref(message))!=0
                    action=lib.fttr_gem_job_update(C.byref(job),C.byref(counter));now+=1
                return action,sent
            finally:client.close();server.close()

        for bridge,descriptor in [(False,0),(True,0x1110)]:
            config=Config(0x117,0x8007,0x8102,0x1234,0x2345,descriptor,12500,25000,bridge)
            ret,original=stock.producer(config,release=True);assert ret==0
            job,counter=start(config);objects={}
            result,frames=drive(job,counter,objects)
            assert result==2 and job.phase==6 and not job.error and all(o.touched for o in job.objects[:job.count])
            action=lib.fttr_gem_job_release(C.byref(job),C.byref(counter))
            result,cleanup=drive(job,counter,objects,action=action)
            assert result==2 and job.phase==7 and not objects and not any(o.touched for o in job.objects[:job.count])
            actual=[x[2:] for x in frames+cleanup if x[2] in (0x44,0x46)]
            assert actual==[x[2:] for x in original], ([x.hex() for x in actual],[x[2:].hex() for x in original])

        config=Config(0x117,0x8007,0x8102,0x1234,0x2345,0x1110,12500,25000,True)
        ret,original=stock.producer(config,reject=True)
        assert ret==55 and stock.released and original[-2][2]==original[-1][2]==0x46
        job,counter=start(config);objects={}
        result,frames=drive(job,counter,objects,'create-and-delete-reject')
        assert result==-errno.EREMOTEIO and job.error==job.cleanup_error==-errno.EREMOTEIO
        assert job.failure.result==2 and job.cleanup_failure.result==7
        assert job.objects[0].touched and job.objects[1].touched and not job.objects[2].touched
        assert (280,0x1110) in objects and (268,0x117) in objects
        assert [struct.unpack('>H',x[4:6])[0] for x in frames if x[2]==0x46]==[268]

        job,counter=start(config)
        descriptor=job.objects[0];objects={(280,0x1110):bytes(descriptor.value[:descriptor.size])}
        result,frames=drive(job,counter,objects)
        assert result==2 and not job.objects[0].touched
        result,cleanup=drive(job,counter,objects,action=lib.fttr_gem_job_release(C.byref(job),C.byref(counter)))
        assert result==2 and set(objects)=={(280,0x1110)}

        job,counter=start(config)
        iw=job.objects[2];objects={(266,0x117):bytes(iw.value[:iw.size])}
        result,frames=drive(job,counter,objects)
        assert result==-errno.EUCLEAN and all(x[2]==0x49 for x in frames)

        config.descriptor=0;job,counter=start(config);objects={}
        result,frames=drive(job,counter,objects,'lost-create-reply')
        assert result==-errno.ETIMEDOUT and not objects and not any(o.touched for o in job.objects[:job.count])
        assert job.failure.attempts==3 and not job.failure.replied

        job,counter=start(config);objects={};assert drive(job,counter,objects)[0]==2
        result,frames=drive(job,counter,objects,'false-delete-success',lib.fttr_gem_job_release(C.byref(job),C.byref(counter)))
        assert result==-errno.EUCLEAN and job.cleanup_error==-errno.EUCLEAN
        assert set(objects)=={(268,0x117),(266,0x117)} and all(o.touched for o in job.objects[:job.count])
        libc=C.CDLL(None);libc.fopen.argtypes=[C.c_char_p,C.c_char_p];libc.fopen.restype=C.c_void_p
        libc.fclose.argtypes=[C.c_void_p];path=work/'state.json'
        file=libc.fopen(str(path).encode(),b'w')
        assert lib.fttr_gem_job_dump(C.byref(job),file)==0 and libc.fclose(file)==0
        state=json.loads(path.read_text());assert state['cleanup_failure']['error']==-errno.EUCLEAN
        assert state['serial']==serial.hex() and state['objects'][0]['touched']

        o,p=stock.oracle,stock.data;assert o.call('plr_gpon_const_val_init')==0
        o.hooks[0x918b8]='stock_alloc_unused'
        for id,ordinal in [(0,1),(5,7),(58,60)]:
            assert o.call('plr_ponsrv_port_id_get',0,id+1,ordinal,p)==0
            assert lib.fttr_miniolt_gem_id(id,ordinal)==struct.unpack('<H',o.uc.mem_read(p,2))[0]
        assert struct.unpack('<Q',o.uc.mem_read(0x169e00,8))[0]==5
        assert struct.unpack('<Q',o.uc.mem_read(0x169e20,8))[0]==7
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256':hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'both_original_associations_and_optional_descriptor_match':True,'original_ignored_delete_failure_reproduced':True,
        'native_retains_failed_cleanup_and_stops_dependent_delete':True,'existing_matching_descriptor_preserved':True,
        'incomplete_existing_dependency_rejected_before_create':True,'lost_create_reply_resolved_by_cleanup_probe':True,
        'false_delete_success_detected':True,'port_ids_match_original_allocator':True,
        'scope':'Actual original producers/wrappers/builders with explicit API responses; native actual datagram transport.',
        'optical_hardware_verified':False},indent=2)+'\n')
    print('GEM: original wire producers, ownership/readback, lost reply and real cleanup-failure tracking passed')


if __name__=='__main__':main()
