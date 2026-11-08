#!/usr/bin/env python3
"""Production alarm allocator failures, snapshot expiry and owner cleanup under sanitizers."""
from pathlib import Path
import os,subprocess,tempfile
r=Path(__file__).resolve().parents[1];src=r/'port/packages/tyfza1-fttr/src'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "omci-alarm.h"
static unsigned allocations,fail_at;
static void *allocate(size_t n,size_t size){if(++allocations==fail_at)return NULL;return calloc(n,size);}
static void *resize(void *p,size_t size){if(++allocations==fail_at)return NULL;return realloc(p,size);}
#define calloc allocate
#define realloc resize
#include "omci-alarm.c"
#undef calloc
#undef realloc
static void header(struct fttr_alarm_state *s,unsigned char p[44],unsigned n){
 memcpy(p,s->me.request,44);p[2]=s->header?0x2b:0x2c;p[8]=n>>8;p[9]=n;
}
static void notification(unsigned char p[44],unsigned seq,unsigned instance){
 memset(p,0,44);p[2]=0x10;p[3]=10;p[4]=1;p[5]=7;p[6]=instance>>8;p[7]=instance;p[8]=128;p[39]=seq;p[43]=40;
}
int main(void){
 struct fttr_alarm_state s={.enabled=true};uint16_t counter=0;unsigned char bytes[44];
 struct fttr_omci_message m={.onu=7,.body=bytes,.length=44};
 for(unsigned failure=1;failure<=3;failure++){
  fttr_alarm_clear(&s);s.enabled=true;allocations=0;fail_at=failure;
  fttr_alarm_start(&s,7,&counter,0);s.me.attempts=1;header(&s,bytes,1);
  assert(fttr_alarm_reply(&s,&m,&counter,100)==(failure==1?1:2));
  if(failure==1){assert(s.error==-ENOMEM&&!s.active&&!s.valid&&!s.snapshot);continue;}
  notification(bytes,1,0x8001);assert(fttr_alarm_notify(&s,&m,200)==1);
  if(failure==2){assert(s.error==-ENOMEM&&!s.active&&!s.valid&&!s.updates&&!s.snapshot);continue;}
  header(&s,bytes,263);bytes[10]=128;bytes[11]=0;s.me.attempts=1;
  assert(fttr_alarm_reply(&s,&m,&counter,300)==1);
  assert(s.error==-ENOMEM&&!s.valid&&!s.active&&!s.snapshot&&!s.updates);
 }
 fail_at=0;fttr_alarm_clear(&s);s.enabled=true;fttr_alarm_start(&s,7,&counter,0);s.me.attempts=1;
 header(&s,bytes,0);assert(fttr_alarm_reply(&s,&m,&counter,55000)==1&&s.error==-ETIMEDOUT&&!s.valid);
 fttr_alarm_start(&s,7,&counter,0);s.me.attempts=1;header(&s,bytes,0);assert(fttr_alarm_reply(&s,&m,&counter,100)==1&&s.valid);
 for(unsigned i=0;i<FTTR_ALARM_MAX;i++){
  notification(bytes,1+i%255,i);assert(fttr_alarm_notify(&s,&m,200)==1&&s.valid);
 }
 notification(bytes,1+FTTR_ALARM_MAX%255,5000);assert(fttr_alarm_notify(&s,&m,300)==1&&s.error==-E2BIG&&!s.valid);
 fttr_alarm_clear(&s);assert(!s.rows&&!s.snapshot&&!s.updates&&!s.count&&!s.enabled);
 puts("Alarm faults: snapshot/delta/merge allocation failures, total expiry, 4096-row bound and cleanup passed");
}
'''
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-I'+str(src),str(p/'test.c'),str(src/'omci-me.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True,env=dict(os.environ,UBSAN_OPTIONS='halt_on_error=1',ASAN_OPTIONS='detect_leaks=1'))
