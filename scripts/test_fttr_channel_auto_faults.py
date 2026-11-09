#!/usr/bin/env python3
"""Run native discovery and channel-trigger summaries through allocation failures."""
import ast
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1];src=root/'port/packages/tyfza1-fttr/src'
text=(src/'fttr-sync.c').read_text()
def function(name):
    a=re.search(r'^static int '+name+r'\([^;]*?\)\s*\{',text,re.M).start();b=text.index('{',a)+1;depth=1
    while depth:depth+=(text[b]=='{')-(text[b]=='}');b+=1
    return text[a:b]+'\n'
fixture=ast.parse((root/'scripts/test_fttr_conflicts_faults.py').read_text())
prefix=next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='code' for t in n.targets)).split('static int decode(',1)[0]
code=prefix+r'''
#include <poll.h>
#include <sys/socket.h>
#include "sync-peer.h"
struct json_object *__real_json_object_new_int(int value);
struct json_object *__wrap_json_object_new_int(int value){return fail()?NULL:__real_json_object_new_int(value);}
static unsigned operation;
static int64_t clock_ms;static unsigned received,requests[3];static int drain_error,exchange_error;
static int64_t now_ms(void){return clock_ms;}
static int test_poll(struct pollfd *fd,unsigned n,int timeout){assert(n==1);clock_ms+=timeout<100?timeout:100;fd->revents=POLLIN;return 1;}
static ssize_t test_send(int fd,const void *p,size_t n,int flags){(void)fd;(void)p;(void)flags;assert(n==16);return n;}
static ssize_t test_recv(int fd,void *buffer,size_t size,int flags){
 (void)fd;(void)size;(void)flags;uint8_t *p=buffer;
 const uint8_t frame[16]={1,0x10,0xec,0,0,1,2,0,0,0,0,1,0x88,0xaa,1,0};
 memcpy(p,frame,16);p[11]+=received++%3;return 16;
}
#define poll test_poll
#define send test_send
#define recv test_recv
static int drain(int fd){(void)fd;return drain_error;}
static int exchange(int fd,const uint8_t source[6],const uint8_t peer[6],uint16_t type,uint8_t slot,unsigned settings,struct json_object *cfg,bool *ack,struct json_object **summary,struct json_object **observed){
 (void)fd;(void)source;(void)summary;assert(type==0x88aa&&slot<3&&settings==operation&&!cfg&&!observed&&peer[5]==slot+1);
 assert(!requests[slot]++);*ack=!exchange_error;return exchange_error;
}
'''+function('discovery')+function('sync_trigger')+function('sync_trigger_all')+r'''
int main(void){
 const uint8_t source[6]={2,3,4,5,6,7},peer[6]={2,0,0,0,0,1};unsigned faults=0;
 for(operation=20;operation<=22;operation++)for(unsigned all=0;all<2;all++)for(unsigned n=1;n<128;n++){
  clock_ms=0;received=0;memset(requests,0,sizeof(requests));calls=0;fail_at=n;
  struct json_object *summary=NULL;
  int ret=all?sync_trigger_all(3,source,0x88aa,operation,&summary):sync_trigger(3,source,peer,0x88aa,0,operation,&summary);
  unsigned count=calls;fail_at=0;
  if(count<n){assert(!ret&&summary);if(all)assert(json_object_array_length(summary)==3);json_object_put(summary);break;}
  assert(ret==-ENOMEM&&!summary);faults++;assert(n<127);
 }
 operation=20;struct json_object *summary=NULL;memset(requests,0,sizeof(requests));drain_error=-EBUSY;
 assert(sync_trigger(3,source,peer,0x88aa,0,operation,&summary)==-EBUSY&&!summary&&!requests[0]);
 drain_error=0;exchange_error=-ETIMEDOUT;
 assert(sync_trigger(3,source,peer,0x88aa,0,operation,&summary)==-ETIMEDOUT&&requests[0]==1);
 struct json_object *field;assert(json_object_object_get_ex(summary,"ack_seen",&field)&&!json_object_get_boolean(field));
 assert(json_object_object_get_ex(summary,"channel_verified",&field)&&!json_object_get_boolean(field));json_object_put(summary);
 printf("Channel/reboot/recovery triggers: %u allocation failures across real discovery/single/all helpers; no partial summary or duplicate trigger ASan/UBSan PASS\n",faults);
}
'''
with tempfile.TemporaryDirectory(prefix='channel-auto-faults-') as tmp:
    d=Path(tmp);(d/'test.c').write_text(code)
    wrapped=('json_object_new_array','json_object_new_object','json_object_new_string','json_object_new_int',
             'json_object_new_boolean','json_object_object_add','json_object_array_add')
    subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(src),str(d/'test.c'),
                    *[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],'-ljson-c',
                    *[f'-Wl,--wrap={n}' for n in wrapped],'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
