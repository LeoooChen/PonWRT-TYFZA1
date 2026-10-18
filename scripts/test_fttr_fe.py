#!/usr/bin/env python3
"""Execute production P6 FE admission/withdrawal with modeled tables and flows."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
headers = root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (headers/'rtl9617c-fe.c').read_text()


def function(name):
    start = re.search(r'^(?:static )?int '+name+r'\(', source, re.M).start()
    pos = source.index('{', start)+1
    depth = 1
    while depth:
        depth += (source[pos] == '{')-(source[pos] == '}')
        pos += 1
    return source[start:pos]


shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fttr-cf.h"
struct mutex { int held; };
struct net_device { int id; };
struct rtl_fe_port { struct net_device *netdev; };
struct rtl_fe_flow { struct rtl_fe_flow *next; struct rtl_fe_port *input,*output; int id; bool deleting; };
struct rtl_fe {
 struct mutex lock;
 struct rtl_fe_flow *flows;
 struct net_device *fttr_netdev;
 struct rtl_fttr_cf fttr_cf;
 bool fttr_live,fttr_requested,fttr_admitted,enabled;
 unsigned int reg[0x2000/4],tcam[1024][3],action[512][3];
 bool tcam_used[1024],action_used[512];
 unsigned int epoch;
 int error,delete_error_id,fttr_cleanup_error;
};
#define list_for_each_entry_safe(p,n,h,m) for(p=*(h);p && ((n=p->next),1);p=n)
static void mutex_lock(struct mutex *m) { assert(!m->held);m->held=1; }
static void mutex_unlock(struct mutex *m) { assert(m->held);m->held=0; }
static void fe_bridge_invalidate(struct rtl_fe *f) { f->epoch++; }
static unsigned int read_reg(void *context,unsigned int reg) { return ((struct rtl_fe *)context)->reg[reg/4]; }
static void write_reg(void *context,unsigned int reg,unsigned int value) { ((struct rtl_fe *)context)->reg[reg/4]=value; }
static int indirect(void *context,unsigned int reg,unsigned int command)
{
 struct rtl_fe *f=context;
 bool act=reg==0x1e14;
 unsigned int index=command&(act?511:1023),data=act?0x1e20:0x1e10;
 unsigned int *words=act?f->action[index]:f->tcam[index];
 bool *used=act?&f->action_used[index]:&f->tcam_used[index];
 assert(reg==0x1e04 || act);
 if(command&(1U<<30)) {
  for(unsigned int i=0;i<3;i++)words[i]=f->reg[(data-i*4)/4];
  *used=true;
 } else {
  for(unsigned int i=0;i<3;i++)f->reg[(data-i*4)/4]=*used?words[i]:(act?0:(i==2?0xfffeffff:~0U));
 }
 return 0;
}
static struct rtl_dq_io fe_io(struct rtl_fe *f) { return (struct rtl_dq_io){f,read_reg,write_reg,indirect}; }
static int fe_delete(struct rtl_fe *f,struct rtl_fe_flow *flow)
{
 struct rtl_fe_flow **p=&f->flows;
 flow->deleting=true;
 if(flow->id==f->delete_error_id)return -ETIMEDOUT;
 while(*p!=flow)p=&(*p)->next;
 *p=flow->next;
 return 0;
}
'''

check = r'''
int main(void)
{
 static struct rtl_fe f;
 struct net_device p6={6},lan={1},pon={7};
 struct rtl_fe_port fp6={&p6},flan={&lan},fpon={&pon};
 struct rtl_fe_flow a={.input=&fp6,.output=&flan,.id=1};
 struct rtl_fe_flow b={.input=&flan,.output=&fp6,.id=2};
 struct rtl_fe_flow c={.input=&flan,.output=&fpon,.id=3};
 f.fttr_netdev=&p6;
 assert(!rtl_fe_fttr_open(&f) && f.fttr_live && f.fttr_cf.destination==16);
 assert(!rtl_fe_fttr_set(&f,true) && f.fttr_requested && !f.fttr_admitted);
 f.enabled=true;
 assert(!rtl_fe_fttr_set(&f,true) && f.fttr_admitted && f.fttr_cf.destination==25);
 a.next=&b;b.next=&c;f.flows=&a;
 unsigned int epoch=f.epoch;
 assert(!rtl_fe_fttr_set(&f,false) && !f.fttr_admitted && f.fttr_cf.destination==16);
 assert(f.epoch>epoch && f.flows==&c && !c.deleting);
 assert(!rtl_fe_fttr_set(&f,true) && f.fttr_admitted);
 a.next=&c;a.deleting=false;f.flows=&a;f.delete_error_id=1;
 assert(rtl_fe_fttr_set(&f,false)==-ETIMEDOUT && !f.fttr_admitted);
 assert(f.fttr_cleanup_error==-ETIMEDOUT);
 assert(f.flows==&a && f.fttr_cf.owned && f.fttr_cf.destination==16);
 /* A failed cache withdrawal cannot be bypassed by an enable request. */
 assert(rtl_fe_fttr_set(&f,true)==-ETIMEDOUT && !f.fttr_admitted && f.flows==&a);
 f.delete_error_id=0;
 assert(!rtl_fe_fttr_set(&f,true) && f.fttr_admitted && f.flows==&c);
 assert(!f.fttr_cleanup_error);
 assert(!rtl_fe_fttr_close(&f) && !f.fttr_live && !f.fttr_cf.owned && !f.fttr_admitted);
 assert(f.flows==&c && !c.deleting && !f.lock.held);
 puts("P6 FE: verified admission, both-direction cache withdrawal, independent LAN/PON flow, failed-withdrawal ownership and retry");
}
'''

with tempfile.TemporaryDirectory(dir=root/'artifacts/fttr') as directory:
    directory = Path(directory)
    (directory/'check.c').write_text(shim+'\n'+'\n'.join(function(name) for name in
        ['fe_fttr_route_locked','fe_fttr_quiesce_locked','rtl_fe_fttr_open','rtl_fe_fttr_close','rtl_fe_fttr_set'])+'\n'+check)
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-I'+str(headers),str(directory/'check.c'),'-o',str(directory/'check')],check=True)
    subprocess.run([str(directory/'check')],check=True)
