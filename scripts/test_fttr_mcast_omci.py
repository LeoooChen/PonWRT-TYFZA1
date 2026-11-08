#!/usr/bin/env python3
"""Compare native multicast transactions with original producers and inject failures."""
import argparse
import copy
import ctypes as C
import errno
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from test_fttr_gem import ObjectsJob, Object, Me, Message
from test_fttr_mib import Stock
from trace_stock_fttr_multicast import Multicast

ROOT = Path(__file__).resolve().parents[1]


class Job(C.Structure):
    _fields_ = [('transaction',ObjectsJob),('objects',Object*7),('table',Me),('failure',Me),
        ('table_length',C.c_uint),('table_row',C.c_ubyte*12),('error',C.c_int),('cleanup_error',C.c_int),
        ('prior_empty',C.c_bool),('table_sent',C.c_bool),('doubled',C.c_bool),('configured',C.c_bool),
        ('releasing',C.c_bool),('defer_apply',C.c_bool),('recover_release',C.c_bool),('stage',C.c_int)]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--fpga',action='store_true',help='use the target FPGA1 relay producers')
    args=p.parse_args()
    original,wire=Multicast(args.stock,args.fpga),Stock(args.stock)
    cases=[]
    with tempfile.TemporaryDirectory() as directory:
        src=ROOT/'port/packages/tyfza1-fttr/src'
        libfile=Path(directory)/'mcast.so'
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-o',str(libfile)]+
            [str(src/n) for n in ('omci-mcast.c','omci-objects.c','omci-me.c','omci-socket.c','omci-wire.c','miniolt.c')],check=True)
        lib=C.CDLL(str(libfile))
        lib.fttr_mcast_start.argtypes=[C.POINTER(Job),C.c_ushort,C.POINTER(C.c_ushort),C.c_ushort,C.c_ushort,C.c_ubyte,C.c_bool,C.c_char_p]
        lib.fttr_mcast_pending.argtypes=[C.POINTER(Job)];lib.fttr_mcast_pending.restype=C.POINTER(Me)
        for name in ('fttr_mcast_update','fttr_mcast_release','fttr_mcast_apply'):
            getattr(lib,name).argtypes=[C.POINTER(Job),C.POINTER(C.c_ushort)]
        lib.fttr_mcast_cancel.argtypes=[C.POINTER(Job),C.c_int]
        lib.fttr_me_job_reply.argtypes=[C.POINTER(Me),C.POINTER(Message)]
        lib.fttr_me_job_timeout.argtypes=[C.POINTER(Me),C.c_int,C.c_void_p,C.c_int64]
        row=bytes.fromhex('0fff0000e0000100efffffff')

        def start(extended=False,defer=False):
            job,counter=Job(),C.c_ushort(0x8122)
            lib.fttr_mcast_start(C.byref(job),5,C.byref(counter),uni_port,10,10,extended,b'TEST\0\0\0\5')
            job.defer_apply=defer
            return job,counter

        def drive(job,counter,initial=None,doubled=False,fail=0,cleanup_fail=False,false_set=False,release=False,drift=False,cancel=0,timeout=0,object_drift=False):
            pool,table=copy.deepcopy(initial) if initial else ({},b'')
            frames=[];writes=0
            action=lib.fttr_mcast_release(C.byref(job),C.byref(counter)) if release else lib.fttr_mcast_update(C.byref(job),C.byref(counter))
            while action==1:
                assert len(frames)<180,(job.stage,frames)
                me=lib.fttr_mcast_pending(C.byref(job));assert me
                body=bytes(me.contents.request);frames.append(body)
                tci,op,_,c,i=struct.unpack('>HBBHH',body[:8]);key=c,i
                mask=struct.unpack_from('>H',body,8)[0]
                obj=next(x for x in job.objects if (x.class_id,x.instance)==key)
                status=0;content=b''
                mutation=op in (0x44,0x48,0x46)
                if mutation:
                    writes+=1;me.contents.attempts=1
                    if writes==cancel:
                        # Cancel immediately after transport send, before the
                        # next state-machine update marks resource ownership.
                        lib.fttr_mcast_cancel(C.byref(job),-errno.ESTALE)
                        return -errno.ESTALE,pool,table,frames,writes
                    assert lib.fttr_mcast_update(C.byref(job),C.byref(counter))==0
                    if writes==fail or (cleanup_fail and (op==0x46 or op==0x48 and c==281 and body[14:22]==bytes(8))):status=6
                if not status:
                    if op==0x44:pool[key]={obj.create.mask:body[8:8+obj.create.size]}
                    elif op==0x48:
                        if c==281:
                            if not false_set:
                                if body[14:22]!=bytes(8):table=body[10:22]
                                else:
                                    assert me.contents.no_retry
                                    # Original ONU inserts a zero row if this
                                    # delete is redundantly sent while empty.
                                    table=b'' if table else body[10:22]
                        else:pool[key][obj.set.mask]=body[10:10+obj.set.size]
                    elif op==0x46:pool.pop(key,None)
                    elif key not in pool:status=5
                    elif c==281 and mask==0x80:
                        data=table+(b'\xa5'*len(table) if doubled else b'')
                        if drift:data=bytes.fromhex('0fff0000e1000100efffffff')
                        content=struct.pack('>H',mask)+(struct.pack('>I',len(data)) if op==0x49 else data)
                    else:
                        content=struct.pack('>H',mask)+pool[key][mask]
                        if object_drift and c==309:content=content[:-1]+bytes([content[-1]^1])
                if mutation and writes==timeout:
                    # A lost response after a successful write must restore the
                    # original state just like an explicit refusal.
                    if me.contents.no_retry:
                        assert lib.fttr_me_job_timeout(me,-1,None,me.contents.deadline)==-errno.ETIMEDOUT
                    else:me.contents.state=2;me.contents.error=-errno.ETIMEDOUT
                    action=lib.fttr_mcast_update(C.byref(job),C.byref(counter))
                    continue
                reply=wire.frame(tci,(op&31)|32,bytes([status])+content,onu=5,class_id=c,instance=i)[16:60]
                buf=C.create_string_buffer(reply);msg=Message(5,C.cast(buf,C.c_void_p),44)
                lib.fttr_me_job_reply(me,C.byref(msg))
                action=lib.fttr_mcast_update(C.byref(job),C.byref(counter))
            return action,pool,table,frames,writes

        for uni_port in ((1,9) if args.fpga else (1,)):
            for extended in (False,True):
                for doubled in (False,True):
                    job,counter=start(extended,defer=True)
                    result,pool,table,frames,writes=drive(job,counter,doubled=doubled)
                    assert result==2 and not job.configured and not writes and not pool and not table
                    assert not lib.fttr_mcast_pending(C.byref(job))
                    assert lib.fttr_mcast_apply(C.byref(job),C.byref(counter))==1
                    result,pool,table,frames,writes=drive(job,counter,doubled=doubled)
                    assert result==2 and job.configured and job.doubled==doubled and table==row
                    expected=original.gem()['events']+[e for e in original.run(veip_count=int(uni_port==9),extended=extended,uni_type=(2 if uni_port==9 else 0) if args.fpga else None)['events'] if e['operation']=='omci']
                    actual=sorted(b[2:] for b in frames if b[2] in (0x44,0x48))
                    assert actual==sorted(bytes.fromhex(e['wire'])[2:] for e in expected),(extended,actual)
                    # Reload the committed recipe after an interrupted release.
                    # The target's delete-when-empty defect is modeled in drive;
                    # correct recovery must never send that Set a second time.
                    for removed in range(9):
                        recovered=Job.from_buffer_copy(bytes(job))
                        recovered.transaction.objects=C.cast(recovered.objects,C.POINTER(Object))
                        recovered.recover_release=True
                        recovered.transaction.recover_release=True
                        partial=copy.deepcopy(pool);remaining_table=table
                        if removed:
                            remaining_table=b''
                            for o in list(recovered.objects)[::-1][:removed-1]:partial.pop((o.class_id,o.instance),None)
                        r,after,after_table,sent,changed=drive(recovered,C.c_ushort(counter.value),initial=(partial,remaining_table),doubled=doubled,release=True)
                        assert r==2 and not after and not after_table,(removed,r)
                        deletes=sum(b[2]==0x48 and b[4:6]==b'\x01\x19' for b in sent)
                        assert deletes==(removed==0)
                        assert changed==8 if removed==0 else changed==8-removed
                    reused,rc=start(extended)
                    r,_,_,reads,changed=drive(reused,rc,initial=(pool,table),doubled=doubled)
                    assert not changed
                    assert r==(-errno.ENOTRECOVERABLE if doubled else 2)
                    result,empty,table,_,_=drive(job,counter,initial=(pool,table),doubled=doubled,release=True)
                    assert result==2 and not empty and not table and not any(o.touched for o in job.objects)
                    job,counter=start(extended)
                    result,pool,table,_,_=drive(job,counter,doubled=doubled)
                    result,empty,table,frames,_=drive(job,counter,initial=(pool,table),doubled=doubled,release=True,timeout=1)
                    assert result==2 and not empty and not table and not job.cleanup_error
                    assert sum(b[2]==0x48 and b[4:6]==b'\x01\x19' for b in frames)==1
                    job,counter=start(extended)
                    result,pool,table,_,_=drive(job,counter,doubled=doubled)
                    result,remaining,_,frames,_=drive(job,counter,initial=(pool,table),doubled=doubled,
                        release=True,timeout=1,false_set=True)
                    assert result==-errno.EUCLEAN and job.cleanup_error and remaining==pool
                    assert not any(b[2]==0x46 for b in frames)
                    cases.append(dict(uni_port=uni_port,extended=extended,doubled=doubled,wire_match=True,release=True))
                    for fail in range(1,writes+1):
                        job,counter=start(extended)
                        result,pool,table,_,_=drive(job,counter,doubled=doubled,fail=fail)
                        assert result==-errno.EREMOTEIO and not job.configured and not job.cleanup_error,(extended,doubled,fail,result,job.cleanup_error)
                        assert not pool and not table,(fail,pool,table)
                    for timeout in range(1,writes+1):
                        job,counter=start(extended)
                        result,pool,table,_,_=drive(job,counter,doubled=doubled,timeout=timeout)
                        assert result==-errno.ETIMEDOUT and not job.cleanup_error and not pool and not table,(extended,doubled,timeout,result,job.cleanup_error,pool,table)
                    for cancel in (1,writes):
                        job,counter=start(extended)
                        result,_,_,_,_=drive(job,counter,doubled=doubled,cancel=cancel)
                        assert result==-errno.ESTALE and not job.configured and not lib.fttr_mcast_pending(C.byref(job))
                        assert job.objects[0].touched if cancel==1 else job.table_sent
                    job,counter=start(extended)
                    result,pool,table,_,_=drive(job,counter,doubled=doubled,false_set=True)
                    assert result==-errno.EUCLEAN and not pool and not table and not job.cleanup_error
                    job,counter=start(extended)
                    result,pool,table,_,_=drive(job,counter,doubled=doubled)
                    result,remaining,_,frames,_=drive(job,counter,initial=(pool,table),doubled=doubled,release=True,drift=True)
                    assert result==-errno.EUCLEAN and job.cleanup_error and remaining==pool
                    assert not any(b[2] in (0x44,0x48,0x46) for b in frames)
                    job,counter=start(extended)
                    result,pool,table,_,_=drive(job,counter,doubled=doubled,fail=writes,cleanup_fail=True)
                    assert result==-errno.EREMOTEIO and job.cleanup_error and pool
                    assert not job.configured
        job,counter=start()
        result,pool,table,_,_=drive(job,counter)
        result,remaining,remaining_table,frames,writes=drive(job,counter,initial=(pool,table),release=True,object_drift=True)
        assert result==-errno.EUCLEAN and job.cleanup_error and not writes and remaining==pool and remaining_table==table
        args.output.write_text(json.dumps(dict(backend="fpga1" if args.fpga else "asic/fpga2",cases=cases,checks=[
            'Native ordinary/extended profile, subscriber, GEM/bridge/filter and IPv4 Set bytes match actual original producers',
            'Deferred preflight sends no writes; each mutation refusal, applied-write timeout and false-success Set restores owned objects and tables; failed cleanup retains dependencies',
            'Transport cancellation immediately after send retains uncertain object/table ownership and exposes no pending request',
            'Original non-idempotent IPv4 row deletion is never sent for an empty table; a lost reply causes readback without retransmitting the Set',
            'Standard table adoption is read-only; ambiguous doubled existing table rejects; newly owned empty-to-one doubled table verifies only initialized bytes',
            'Release checks all object groups and the live table before removing its row and owned objects; foreign object/table drift performs no cleanup writes',
            'Nine partial-release snapshots per ordinary/CTC and single/doubled table recover without replaying delete against an empty table'],
            scope='Actual original producers/encoders and native asynchronous transactions with modeled ONU storage; no optical traffic'),indent=2)+'\n')
        print('PASS: native multicast original-wire matching, all mutation failures, table readback/adoption, cleanup and drift')


if __name__=='__main__':main()
