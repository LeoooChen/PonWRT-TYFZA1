#!/usr/bin/env python3
"""Compare native optical transaction to original master/ONU wire functions."""
import argparse,ctypes as C,errno,subprocess,tempfile
from pathlib import Path
from trace_stock_fttr_onu_optical import Optical,SlaveOptical
p=argparse.ArgumentParser(description=__doc__)
for n in ('relay','fpga-relay','slave'):p.add_argument('--'+n,type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[1];src=root/'port/packages/tyfza1-fttr/src'
shim=r'''
#include "omci-optical.h"
static struct fttr_optical_job job;static uint16_t counter;
const void *start(unsigned onu,unsigned instance){counter=0x8122;fttr_optical_start(&job,onu,instance,&counter);job.me.attempts=1;job.me.deadline=2000;return job.me.request;}
int reply(const void *data,unsigned size,unsigned onu){struct fttr_omci_message m={.onu=onu,.body=data,.length=size};return fttr_optical_reply(&job,&m);}
int tick(long now){return fttr_optical_tick(&job,now);}
int error(void){return job.error;}int done(void){return job.done;}int valid(void){return job.valid;}
unsigned raw(unsigned i){return job.raw[i];}
void cancel(void){fttr_optical_cancel(&job,-116);}
'''
with tempfile.TemporaryDirectory() as folder:
 f=Path(folder);(f/'shim.c').write_text(shim)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-ffunction-sections','-Wl,--gc-sections','-I'+str(src),str(f/'shim.c'),str(src/'omci-optical.c'),str(src/'omci-me.c'),str(src/'omci-socket.c'),str(src/'omci-wire.c'),str(src/'miniolt.c'),'-o',str(f/'native.so')],check=True)
 lib=C.CDLL(str(f/'native.so'));lib.start.restype=C.c_void_p;lib.reply.argtypes=[C.c_void_p,C.c_uint,C.c_uint]
 cases=0
 for binary in (a.relay,a.fpga_relay):
  stock=Optical(binary);slave=SlaveOptical(a.slave)
  for instance in (0,0x8000,0x8001,65535):
   for missing in ((),(8,),(5,7,6,4), (5,8,7,6,4)):
    for first in ('ack','result'):
     request=C.string_at(lib.start(7,instance),44);assert request==stock.request(instance)
     ack=request[:2]+b'\x32\x0a'+request[4:8]+bytes(32)+b'\0\0\0\x28'
     result=slave.result(request,missing)
     ordered=[ack,result] if first=='ack' else [result,ack]
     for packet in ordered:lib.reply(packet,44,7)
     assert lib.done() and lib.error()==(-errno.ENODATA if len(missing)==5 else 0)
     expected=sum(1<<i for i,t in enumerate((5,8,7,6,4)) if t not in missing)
     assert lib.valid()==expected
     if expected:
      raw=stock.parse(result)
      for i in range(5):
       if expected&(1<<i):assert lib.raw(i)==raw[i]
     cases+=1
  for value in (0,1,255,256,1000,13500,16500,32767,32768,65535):
   expected=stock.convert([value]*5)
   for i in range(5):
    native=lib.fttr_optical_value(i,value)
    assert (native+32768)%65536-32768==expected[i]
    if i==0:assert native==(value if value<32768 else value-65536)*2
   cases+=1
  request=C.string_at(lib.start(7,0x8000),44);result=slave.result(request)
  ack=request[:2]+b'\x32\x0a'+request[4:8]+bytes(32)+b'\0\0\0\x28'
  for fault in ('wrong-tci','wrong-onu','short','class','instance','type','trailer','id','zero-with-value','conflicting'):
   lib.start(7,0x8000);bad=bytearray(result);onu=7;size=44
   if fault=='wrong-tci':bad[1]^=1
   if fault=='wrong-onu':onu=8
   if fault=='short':size=30
   if fault=='class':bad[5]^=1
   if fault=='instance':bad[7]^=1
   if fault=='type':bad[2]=0x29
   if fault=='trailer':bad[43]=0
   if fault=='id':bad[8]=2
   if fault=='zero-with-value':bad[8]=0
   if fault=='conflicting':lib.reply(result,44,7);bad[10]^=1
   ret=lib.reply(bytes(bad),size,onu)
   if fault in ('wrong-tci','wrong-onu'):assert not ret and not lib.done()
   else:assert ret==1 and lib.error()==-errno.EPROTO and not lib.valid()
   cases+=1
  lib.start(7,0x8000);lib.reply(ack,44,7);assert not lib.tick(1999);assert lib.tick(2000) and lib.error()==-errno.ETIMEDOUT
  lib.start(7,0x8000);lib.reply(result,44,7);assert lib.tick(2000) and lib.error()==-errno.ETIMEDOUT and not lib.valid()
  lib.start(7,0x8000);bad=bytearray(ack);bad[8]=2;lib.reply(bytes(bad),44,7);assert lib.error()==-errno.EREMOTEIO
  lib.start(7,0x8000);lib.cancel();assert not lib.reply(result,44,7) and lib.error()==-errno.ESTALE
  cases+=4
 print(f'ONU optical: {cases} original-wire, ACK/result ordering, missing-value, malformed/stale/timeout/cancel cases passed')
