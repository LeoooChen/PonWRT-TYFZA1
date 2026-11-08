#!/usr/bin/env python3
"""Drive the production temporary-ONU dispatcher through timing/cancellation/error boundaries."""
from pathlib import Path
import subprocess
import tempfile

from test_fttr_fpga_boot import function

ROOT = Path(__file__).resolve().parents[1]
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
#define FTTR_ONUS 64
enum { FTTR_FPGA1=1,FTTR_FPGA2,FTTR_ASIC9690 };
enum { ONU_ABSENT,ONU_BLOCKED,ONU_REBOOT,ONU_REBOOT_WAIT,ONU_UPGRADE,ONU_UPGRADE_WAIT };
enum { FTTR_ADMIN_DEACTIVATE=2 };
struct fttr_onu_state {uint32_t downstream,upstream;unsigned char serial[8];};
struct fttr_admin_request {uint32_t version,id,action;unsigned char serial[8];uint32_t target;};
struct onu { bool admin_pending,service_started,grant; void *wifi;
 struct {int index;} ports[7]; unsigned char admin_serial[8]; int64_t admin_timeout;
 int phase,hardware; };
struct controller { int backend,directory; int64_t admin_scan_due; struct onu onu[FTTR_ONUS];
 struct {bool active;struct fttr_admin_request request;} move; };
static int calls,readbacks,publishes,io_error,read_error,publish_error;
static int table_error,recoveries,recovery_error,recovery_fatal;
static int64_t clock_sample,io_duration;
static int64_t now_ms(void){return clock_sample+io_duration;}
static struct fttr_onu_state samples[FTTR_ONUS];
static int fttr_miniolt_table(int directory,struct fttr_onu_state *table) {
 (void)directory;memcpy(table,samples,sizeof(samples));return table_error;
}
static bool fttr_miniolt_recovery_needed(int backend,uint32_t upstream) {
 (void)backend;return upstream==0x100000;
}
static int admin_request(struct controller *c,const struct fttr_admin_request *r,bool recovery,int *fatal) {
 assert(recovery && r->id==7 && r->version==1 && r->action==2 && r->serial[0]==0x54);
 recoveries++;*fatal=recovery_fatal;if(recovery_error)return recovery_error;
 c->onu[7].admin_pending=true;return 0;
}
static unsigned int target;
static int fttr_miniolt_admin(int directory,unsigned id,unsigned action,const unsigned char *serial) {
 (void)directory;assert(id==target);assert(action==2);assert(serial[0]==0x54);calls++;return io_error;
}
static int fttr_miniolt_one(int directory,unsigned id,int *hardware) {
 (void)directory;assert(id==target);*hardware=42;readbacks++;return read_error;
}
static int publish(struct controller *c,const char *state,int error) {
 (void)c;assert(!strcmp(state,"running") && !error);publishes++;return publish_error;
}
'''
source += function((ROOT / 'port/packages/tyfza1-fttr/src/fttrd.c').read_text(), 'admin_tick')
source += r'''
static struct controller c;
static int tick(struct controller *c,int64_t now) {
 clock_sample=now;return admin_tick(c,now);
}
static void reset(int backend,int64_t due) {
 memset(&c,0,sizeof(c));c.backend=backend;c.admin_scan_due=due;
 calls=readbacks=publishes=io_error=read_error=publish_error=0;target=7;
 table_error=recoveries=recovery_error=recovery_fatal=0;memset(samples,0,sizeof(samples));samples[7].serial[0]=0x54;
 io_duration=0;
 c.onu[7].admin_pending=true;c.onu[7].admin_serial[0]=0x54;
 c.onu[7].admin_timeout=15000;c.onu[7].phase=ONU_BLOCKED;
}
int main(void) {
 for(int backend=1;backend<=3;backend++) {
  reset(backend,100);assert(!admin_tick(&c,99) && !calls);
  io_error=-EAGAIN;assert(!admin_tick(&c,100));assert(calls==1 && !readbacks && !publishes);
  assert(c.onu[7].admin_pending && c.onu[7].phase==ONU_BLOCKED);
  int64_t next=backend!=1?8100:200;
  assert(c.admin_scan_due==next);
  assert(!admin_tick(&c,next-1) && calls==1);
  io_error=0;assert(!admin_tick(&c,next));
  assert(calls==2 && readbacks==1 && publishes==1 && !c.onu[7].admin_pending);
  assert(c.onu[7].phase==ONU_ABSENT && c.onu[7].hardware==42);
  assert(!admin_tick(&c,next+16000) && calls==2);
  reset(backend,100);io_error=-ESTALE;assert(!admin_tick(&c,100));
  assert(!c.onu[7].admin_pending && c.onu[7].phase==ONU_ABSENT && publishes==1);
  reset(backend,100);io_error=-EIO;assert(admin_tick(&c,100)==-EIO && !readbacks && !publishes);
  reset(backend,100);read_error=-EIO;assert(admin_tick(&c,100)==-EIO && !publishes);
  reset(backend,100);publish_error=-ENOSPC;assert(admin_tick(&c,100)==-ENOSPC);
  reset(backend,100);c.onu[7].admin_pending=false;
  assert(!admin_tick(&c,20000) && !calls); /* later block/resume/stop cancels it */
  for(int owner=0;owner<4;owner++) {
   reset(backend,100);
   if(owner==0)c.onu[7].ports[0].index=3;
   if(owner==1)c.onu[7].service_started=true;
   if(owner==2)c.onu[7].grant=true;
   if(owner==3)c.onu[7].wifi=&c;
   assert(admin_tick(&c,100)==-EUCLEAN && !calls);
  }
  reset(backend,20000);
  assert(!admin_tick(&c,14999) && !calls);
  assert(!admin_tick(&c,15000));assert(calls==(backend!=1));
  if(backend==1)assert(c.onu[7].admin_pending); /* no guessed FPGA timeout */
  reset(backend,100);table_error=-EIO;assert(admin_tick(&c,100)==-EIO && !calls);
  reset(backend,100);io_duration=500;
  assert(!admin_tick(&c,100));assert(c.admin_scan_due==600+(backend!=1?8000:100));
  reset(backend,100);c.onu[7].admin_pending=false;samples[7].upstream=0x100000;
  assert(!admin_tick(&c,99) && !recoveries);
  assert(!admin_tick(&c,100) && recoveries==1 && calls==(backend!=1));
  if(backend==1)assert(c.onu[7].admin_pending); /* release in next pass */
  reset(backend,100);c.onu[7].admin_pending=false;samples[7].upstream=0x100000;samples[7].downstream=0x10000;
  assert(!admin_tick(&c,100) && !recoveries && !calls);
  for(int failure=0;failure<3;failure++) {
   reset(backend,100);c.onu[7].admin_pending=false;samples[7].upstream=0x100000;
   recovery_error=failure==0?-ESTALE:failure==1?-EAGAIN:-EIO;
   recovery_fatal=failure==2?-EIO:0;
   assert(admin_tick(&c,100)==(failure==2?-EIO:0) && !calls && recoveries==1);
  }
 }
 puts("Temporary ONU dispatcher: scan/timer boundaries, stale/cancel, ownership and error exits passed");
 return 0;
}
'''
# The production dispatcher sees a controllable monotonic clock; the caller
# also models a slow MDIO pass without changing kernel/user timing code.
start = source.index('int main(void)')
source = source[:start] + source[start:].replace('admin_tick(', 'tick(')
with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    (folder / 'native.c').write_text(source)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(folder / 'native.c'),
                    '-o', str(folder / 'test')], check=True)
    subprocess.run([str(folder / 'test')], check=True)
