#!/usr/bin/env python3
"""Compare native Wi-Fi groups with original frames and exercise readback/undo."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from trace_stock_fttr_wifi import WifiOriginal, config
from test_fttr_gem import ObjectsJob, Object, Me, Message


class Job(C.Structure):
    _fields_=[('transaction',ObjectsJob),('objects',Object*9)]


WRAPPER = r'''
#include "omci-wifi.h"
int start(struct fttr_wifi_job *job, uint16_t *counter, const uint8_t *input,
          unsigned int ssid_size, unsigned int key_size, unsigned int present)
{
 struct fttr_mib_entity security={.class_id=0xff6a,.instance=0x101};
 struct fttr_mib_entity radio={.class_id=0xff69,.instance=0x101,.next=present&2?&security:NULL};
 struct fttr_mib_model model={.entities=present&1?&radio:NULL};
 const uint8_t serial[8]={'T','E','S','T',0,0,0,5};
 struct fttr_wifi_config cfg={.instance=0x101,.mode=input[3],.channel=input[4],.bandwidth=input[37],
  .security=input[46],.encryption=input[242],.key_auth=input[111],.key_index=input[113],
  .ssid_enabled=input[245],.radio_enabled=input[246],.ssid=input+5,.ssid_size=ssid_size,
  .shared_key=input+47,.shared_key_size=key_size,
  .keys={input+114,input+146,input+178,input+210},.key_size={26,26,26,26}};
 return fttr_wifi_job_start(job,5,counter,&model,&cfg,serial);
}
int dump(struct fttr_wifi_job *job,const char *path)
{
 FILE *file=fopen(path,"w");
 int ret=fttr_wifi_job_dump(job,file);
 return fclose(file)?-1:ret;
}
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--master',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    stock=WifiOriginal(args.master)
    root=Path(__file__).resolve().parents[1]
    src=root/'port/packages/tyfza1-fttr/src'
    with tempfile.TemporaryDirectory(dir=root/'artifacts/fttr') as directory:
        directory=Path(directory);(directory/'wifi.c').write_text(WRAPPER)
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-I'+str(src),
            str(directory/'wifi.c'),*[str(src/name) for name in ('omci-wifi.c','omci-objects.c','omci-me.c',
            'omci-socket.c','omci-wire.c','miniolt.c')],'-o',str(directory/'wifi.so')],check=True)
        native=C.CDLL(str(directory/'wifi.so'))
        native.start.argtypes=[C.POINTER(Job),C.POINTER(C.c_ushort),C.c_void_p,C.c_uint,C.c_uint,C.c_uint]
        native.fttr_objects_job_update.argtypes=[C.POINTER(ObjectsJob),C.POINTER(C.c_ushort)]
        native.fttr_me_job_reply.argtypes=[C.POINTER(Me),C.POINTER(Message)]
        native.dump.argtypes=[C.POINTER(Job),C.c_char_p]

        def run(cfg, fail_set=0, fail_restore=False, absent_group=-1, old=None, bad_readback=False):
            job,counter=Job(),C.c_ushort(0x8122)
            assert native.start(C.byref(job),C.byref(counter),cfg,24,24,3)==0
            groups={(obj.class_id,obj.instance,obj.create.mask):bytes([0x55])*obj.create.size
                    for obj in job.objects[:job.transaction.count]} if old is None else dict(old)
            before=dict(groups);writes=[];operations=[];action=1;count=0
            while action==1:
                body=bytes(job.transaction.me.request)
                tci,op,dev,cls,instance,mask=struct.unpack('>HBBHHH',body[:10])
                assert dev==10 and op in (0x48,0x49)
                obj=job.objects[job.transaction.index];key=cls,instance,mask
                restoring=job.transaction.error!=0
                job.transaction.me.attempts=1
                status=0;payload=b''
                operations.append(op)
                if op==0x49:
                    if job.transaction.index==absent_group:status=5
                    payload=struct.pack('>H',mask)+groups[key]
                else:
                    if not restoring:
                        count+=1;writes.append(body)
                    status=2 if (not restoring and count==fail_set) or (restoring and fail_restore) else 0
                    if not status:
                        value=body[10:10+obj.create.size]
                        groups[key]=b'\xff'*len(value) if bad_readback and not restoring and count==2 else value
                reply=stock.frame(tci,(op&31)|32,bytes([status])+payload,5,cls,instance)[16:60]
                buffer=C.create_string_buffer(reply)
                message=Message(5,C.cast(buffer,C.c_void_p),44)
                assert native.fttr_me_job_reply(C.byref(job.transaction.me),C.byref(message))!=0
                action=native.fttr_objects_job_update(C.byref(job.transaction),C.byref(counter))
                assert len(operations)<100
            if fail_set or bad_readback:
                assert action<0 and job.transaction.error<0
                if fail_restore:
                    assert job.transaction.cleanup_error<0 and any(o.touched for o in job.objects)
                else:
                    assert groups==before and not job.transaction.cleanup_error
                    assert not any(o.touched for o in job.objects)
            elif absent_group>=0:
                assert action<0 and not writes
            else:
                assert action==2 and not job.transaction.error
                assert operations[:job.transaction.count]==[0x49]*job.transaction.count
            return job,writes,groups

        for mode,security in ((0,0),(3,1),(7,3),(4,5),(6,6)):
            cfg=config(mode,security);expected=stock.run_wifi(cfg)
            job,writes,values=run(cfg)
            assert [b[2:] for b in writes]==[b[2:] for b in expected], (mode,security,
                [(a.hex(),b.hex()) for a,b in zip(writes,expected) if a[2:]!=b[2:]])
            _,repeated,_=run(cfg,old=values)
            assert not repeated
        run(config(),fail_set=3)
        run(config(),bad_readback=True)
        failed,_,_=run(config(),fail_set=3,fail_restore=True)
        run(config(),absent_group=5)
        assert native.dump(C.byref(failed),str(directory/'state.json').encode())==0
        report_text=(directory/'state.json').read_text();diagnostic=json.loads(report_text)
        assert diagnostic['error'] and diagnostic['cleanup_error'] and 'reply' not in diagnostic
        assert 'test-passphrase' not in report_text and 'FTTR native research' not in report_text and 'previous' not in report_text
        for present,ssid_size,key_size,error in [(1,24,24,-errno.EOPNOTSUPP),
            (3,25,24,-errno.EMSGSIZE),(3,24,25,-errno.EMSGSIZE)]:
            job,counter=Job(),C.c_ushort(0x8122)
            assert native.start(C.byref(job),C.byref(counter),config(),ssid_size,key_size,present)==error
            assert counter.value==0x8122 and not job.transaction.count and not any(job.transaction.me.request)
    result={'stock_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),
        'checks':['five original radio/security combinations match native Set wire bytes',
            'all selected groups are read before writes; unchanged configuration sends no Set',
            'missing MIB instance or oversized SSID/key rejects before requests',
            'missing selected peer group stops before writes; NACK/readback mismatch restores prior groups',
            'failed restoration retains original and cleanup errors plus unresolved ownership',
            'diagnostic output omits SSIDs, keys, prior values and raw frames'],
        'scope':'Native constructors/ME transaction and original master producers/encoders. Peer MIB/replies modeled; no downstream gateway or wireless acceptance.'}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
