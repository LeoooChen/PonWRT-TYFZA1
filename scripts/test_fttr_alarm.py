#!/usr/bin/env python3
"""Production alarm synchronization against original ONU snapshot/notification wire."""
import argparse,ctypes as C,errno,subprocess,tempfile
from pathlib import Path
from trace_stock_fttr_alarm import AlarmSlave,request
p=argparse.ArgumentParser(description=__doc__)
for n in ('relay','fpga-relay','slave'):p.add_argument('--'+n,type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[1];src=root/'port/packages/tyfza1-fttr/src'
shim=r'''
#include <string.h>
#include "omci-alarm.h"
static struct fttr_alarm_state state;static uint16_t counter;
const void *start(void){fttr_alarm_clear(&state);state.enabled=true;counter=0x8122;fttr_alarm_start(&state,7,&counter,0);state.me.attempts=1;return state.me.request;}
const void *pending(void){state.me.attempts=1;return state.me.request;}
int response(const void*p,unsigned size){struct fttr_omci_message m={7,p,size};return fttr_alarm_reply(&state,&m,&counter,100);}
int notify(const void*p,unsigned size){struct fttr_omci_message m={7,p,size};return fttr_alarm_notify(&state,&m,200);}
int valid(void){return state.valid;}int active(void){return state.active;}int error(void){return state.error;}unsigned count(void){return state.count;}int sequence(void){return state.sequence;}
int bitmap(unsigned cls,unsigned instance,unsigned byte){for(unsigned i=0;i<state.count;i++)if(state.rows[i].class_id==cls&&state.rows[i].instance==instance)return state.rows[i].bitmap[byte];return -1;}
void invalidate(void){fttr_alarm_invalidate(&state,-5,200);}
void clear(void){fttr_alarm_clear(&state);}
'''
with tempfile.TemporaryDirectory() as folder:
 f=Path(folder);(f/'shim.c').write_text(shim)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(src),str(f/'shim.c'),*[str(src/(n+'.c')) for n in ('omci-alarm','omci-me','omci-socket','omci-wire','miniolt')],'-o',str(f/'native.so')],check=True)
 lib=C.CDLL(str(f/'native.so'));lib.start.restype=lib.pending.restype=C.c_void_p
 lib.response.argtypes=lib.notify.argtypes=[C.c_void_p,C.c_uint]
 def frame():return C.string_at(lib.pending(),44)
 def respond(slave,rows):return lib.response(slave.reply_alarm(frame(),rows)[:44],44)
 checks=0
 for binary in (a.relay,a.fpga_relay):
  slave=AlarmSlave(a.slave)
  for count in (0,1,3,32):
   rows=[(263,0x8000+i,bytes([i+1])+bytes(27)) for i in range(count)]
   assert C.string_at(lib.start(),44)==request(binary)
   ret=respond(slave,rows)
   for i in range(count):
    assert ret==2 and not lib.valid() and frame()[8:10]==i.to_bytes(2,'big')
    ret=respond(slave,rows)
   assert ret==1 and lib.valid() and lib.count()==count and not lib.active()
   for i in range(count):assert lib.bitmap(263,0x8000+i,0)==i+1
   # Original sequence starts at1 after the snapshot, wraps255->1.
   for seq in range(1,258):
    raw=bytes([seq&255])+bytes(27);wire=slave.notify(263,0x8000,raw)[:44]
    assert lib.notify(wire,44)==1 and lib.valid() and lib.bitmap(263,0x8000,0)==seq&255
    assert lib.notify(wire,44)==0 and lib.valid()
   checks+=1
  rows=[(263,0x8000,b'\x80'+bytes(27)),(256,0,bytes(28))]
  lib.start();assert respond(slave,rows)==2
  new=slave.notify(263,0x8000,b'\x40'+bytes(27),1)[:44]
  assert lib.notify(new,44)==1 and not lib.valid()
  assert respond(slave,rows)==2;assert respond(slave,rows)==1
  assert lib.valid() and lib.bitmap(263,0x8000,0)==64
  gap=slave.notify(263,0x8000,bytes(28),3)[:44];assert lib.notify(gap,44)==1 and not lib.valid() and lib.error()==-errno.EPIPE
  checks+=1
  for fault in ('duplicate-row','count','header','short','unsolicited','conflict'):
   lib.start()
   if fault=='count':
    wire=bytearray(slave.reply_alarm(frame(),[])[:44]);wire[8:10]=(4097).to_bytes(2,'big')
    assert lib.response(bytes(wire),44)==1 and lib.error()==-errno.E2BIG
   elif fault in ('header','short'):
    wire=bytearray(slave.reply_alarm(frame(),[])[:44]);wire[7]=1 if fault=='header' else 0
    assert lib.response(bytes(wire),43 if fault=='short' else 44)==1 and lib.error()==-errno.EPROTO
   elif fault=='unsolicited':
    assert lib.notify(slave.notify(263,0x8000,bytes(28),1)[:44],44)==1 and lib.error()==-errno.EAGAIN
   elif fault=='duplicate-row':
    dup=[rows[0],rows[0]];assert respond(slave,dup)==2;assert respond(slave,dup)==2
    assert respond(slave,dup)==1 and lib.error()==-errno.EPROTO
   else:
    assert respond(slave,[])==1
    assert lib.notify(slave.notify(263,0x8000,bytes(28),1)[:44],44)==1
    assert lib.notify(slave.notify(263,0x8000,b'\x80'+bytes(27),1)[:44],44)==1 and lib.error()==-errno.EPROTO
   assert not lib.valid();checks+=1
  lib.clear()
 print(f'Alarm state: {checks} snapshot/race/error groups, 2056 original updates with duplicate/replay and sequence wrap passed')
