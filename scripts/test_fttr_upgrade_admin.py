#!/usr/bin/env python3
"""Execute the real admin request across upgrade-hold release failure boundaries."""
from pathlib import Path
import subprocess
import tempfile
from test_fttr_fpga_boot import function
ROOT=Path(__file__).resolve().parents[1]
source=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fttr-control.h"
#define FTTR_ONUS 64
#define FTTR_ASIC9690 3
enum {ONU_ABSENT,ONU_BLOCKED};
struct fttr_onu_state {uint32_t downstream,upstream;uint8_t serial[8];};
struct onu {struct fttr_onu_state hardware;bool admin_pending,upgrade_held,upgrade_marked;
 int phase;uint8_t admin_serial[8];int64_t admin_timeout;};
struct controller {int directory,upgrade_store,backend;struct onu onu[64];
 struct {bool active;struct fttr_admin_request request;} upgrade,reboot,move;};
static struct controller c;
static int events[16],count,reads,fail_at;
static bool receipt,replace_after_driver;
static int event(int kind){assert(count<16);events[count++]=kind;return kind==fail_at?-EIO:0;}
static int fttr_miniolt_one(int directory,unsigned id,struct fttr_onu_state *s){
 (void)directory;assert(id==7);int ret=event(++reads==1?1:5);memset(s,0,sizeof(*s));
 memcpy(s->serial,"TEST0007",8);if(reads>1 && replace_after_driver)s->serial[7]='8';return ret;
}
static int fttr_upgrade_store_remove(int directory,const uint8_t *serial){
 (void)directory;assert(!memcmp(serial,"TEST0007",8));int ret=event(6);if(!ret)receipt=false;return ret;
}
static bool fttr_miniolt_recovery_needed(int backend,uint32_t upstream){(void)backend;(void)upstream;return true;}
static int forwarding(struct controller *ctl,bool enable){assert(ctl==&c);return event(enable?7:2);}
static int cancel(struct controller *ctl,unsigned id,bool paused){assert(ctl==&c && id==7 && paused);return event(3);}
static int fttr_miniolt_admin(int directory,unsigned id,unsigned action,const uint8_t *serial){
 (void)directory;assert(id==7 && action<=3 && !memcmp(serial,"TEST0007",8));return event(4);
}
static int publish(struct controller *ctl,const char *state,int result){assert(ctl==&c && !strcmp(state,"running") && !result);return event(8);}
static int64_t now_ms(void){return 1000;}
'''
source+=function((ROOT/'port/packages/tyfza1-fttr/src/fttrd.c').read_text(),'admin_request(struct controller')
source+=r'''
static void reset(int failure){memset(&c,0,sizeof(c));c.backend=FTTR_ASIC9690;c.upgrade_store=9;
 c.onu[7].upgrade_held=c.onu[7].upgrade_marked=receipt=true;reads=count=0;fail_at=failure;replace_after_driver=false;}
int main(void){
 struct fttr_admin_request r={.version=1,.id=7,.action=FTTR_ADMIN_RESUME};memcpy(r.serial,"TEST0007",8);
 int fatal;
 for(int failure=1;failure<=5;failure++){
  reset(failure);assert(admin_request(&c,&r,false,&fatal)==-EIO);
  assert(receipt && c.onu[7].upgrade_held && c.onu[7].upgrade_marked);
  for(int n=0;n<count;n++)assert(events[n]!=6 && events[n]!=7);
 }
 reset(0);replace_after_driver=true;assert(admin_request(&c,&r,false,&fatal)==-ESTALE);
 assert(receipt && c.onu[7].upgrade_held);
 reset(6);assert(admin_request(&c,&r,false,&fatal)==-EIO && fatal==-EIO);
 assert(receipt && c.onu[7].upgrade_held && events[count-1]==6);
 reset(0);assert(!admin_request(&c,&r,false,&fatal));
 assert(!receipt && !c.onu[7].upgrade_held && !c.onu[7].upgrade_marked && count==8);
 for(int n=0;n<8;n++)assert(events[n]==n+1);
 for(int action=FTTR_ADMIN_BLOCK;action<=FTTR_ADMIN_DEACTIVATE;action++){
  reset(0);r.action=action;assert(!admin_request(&c,&r,false,&fatal));assert(receipt && c.onu[7].upgrade_held);
  assert(c.onu[7].admin_pending==(action==FTTR_ADMIN_DEACTIVATE));
 }
 puts("Upgrade admin: failed teardown/driver/readback and replacement retain record; successful resume releases after hardware confirmation");
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(source)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'port/packages/tyfza1-fttr/src'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
