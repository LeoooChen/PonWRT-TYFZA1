#!/usr/bin/env python3
"""Execute the real rebuild boundary with teardown/identity/storage failures."""
from pathlib import Path
import subprocess,tempfile
from test_fttr_fpga_boot import function
root=Path(__file__).resolve().parents[1]
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#define FTTR_SERVICES_RELEASED 4
#define ONU_ABSENT 0
struct fttr_onu_state {unsigned downstream;uint8_t serial[8];};
struct onu {struct fttr_onu_state hardware;uint8_t identity[8];bool service_rebuild;struct {int stage;}services;int phase;};
struct controller {struct onu onu[8];int directory,service_store;};
static bool stopping,record;static int fault,events[4],count;
static int event(int n){events[count++]=n;return fault==n?-EIO:0;}
static int cancel(struct controller *c,unsigned id,bool paused){assert(id==7&&!paused&&record);int r=event(1);memset(&c->onu[id],0,sizeof(c->onu[id]));return r;}
static int fttr_miniolt_same(int d,unsigned id,const struct fttr_onu_state *expected){(void)d;assert(id==7&&!memcmp(expected->serial,"TEST0007",8));return event(2);}
static int fttr_service_store_remove(int d,const uint8_t *sn){(void)d;assert(record&&!memcmp(sn,"TEST0007",8));int r=event(3);if(!r)record=false;return r;}
static int start_mib(struct controller *c,unsigned id){assert(id==7&&!record&&c->onu[id].phase==ONU_ABSENT);return event(4);}
'''
code+=function((root/'port/packages/tyfza1-fttr/src/fttrd.c').read_text(),'service_rebuild(struct controller *c, unsigned int id)\n{')
code+=r'''
int main(void){
 for(fault=0;fault<=4;fault++){
  struct controller c={0};c.onu[7].service_rebuild=true;c.onu[7].services.stage=4;memcpy(c.onu[7].identity,"TEST0007",8);
  record=true;count=0;assert(service_rebuild(&c,7)==(fault?-EIO:0));
  assert(record==(fault>0&&fault<4));assert(count==(fault?fault:4));for(int i=0;i<count;i++)assert(events[i]==i+1);
 }
 struct controller c={0};record=true;count=0;fault=0;stopping=true;c.onu[7].service_rebuild=true;c.onu[7].services.stage=4;memcpy(c.onu[7].identity,"TEST0007",8);
 assert(service_rebuild(&c,7)==-EINTR&&record&&count==2);
 c.onu[7].service_rebuild=true;c.onu[7].services.stage=0;count=0;assert(service_rebuild(&c,7)==-EINVAL&&!count);
 puts("Initial rebuild: teardown/identity precede durable removal, cancellation and storage failures prevent restart; explicit releases refuse auto-rebuild");
}
'''
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
