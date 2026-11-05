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
    start = re.search(r'^(?:static )?int '+name+r'\([^;]*?\)\n\{', source, re.M).start()
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
#define BIT(n) (1U<<(n))
#define FE_LAN_PORTS 5
#define FE_LAN_RX_CTRL(p) (0xa5f8+0x90*(p))
struct mutex { int held; };
struct net_device { int id; };
struct rtl_fe_port { struct net_device *netdev; unsigned int source; bool retired; };
struct rtl_fe_flow { struct rtl_fe_flow *next; struct rtl_fe_port *input,*output; int id; bool deleting; };
struct rtl_fe {
 struct mutex lock;
 struct rtl_fe_flow *flows;
 struct net_device *fttr_netdev;
 struct rtl_fttr_cf fttr_cf;
 bool fttr_live,fttr_requested,fttr_admitted,enabled,prepared,pon_requested;
 unsigned int lan_saved[5],prepares,restores;
 int prepare_error,restore_error;
 unsigned int reg[0x10000/4],tcam[1024][3],action[512][3];
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

shim += r'''
static unsigned int fe_read(void *f,unsigned int reg){return read_reg(f,reg);}
static void fe_write(void *f,unsigned int reg,unsigned int value){write_reg(f,reg,value);}
static void fe_collect_ports(struct rtl_fe *f){(void)f;}
static void usleep_range(unsigned int a,unsigned int b){assert(a==1000 && b==2000);}
static int fe_prepare(struct rtl_fe *f){assert(!f->prepared);f->prepares++;f->prepared=true;return f->prepare_error;}
static int fe_restore(struct rtl_fe *f){if(f->restore_error)return f->restore_error;f->restores++;f->prepared=false;return 0;}
static int fe_enable_locked(struct rtl_fe *fe);
static int fe_disable_locked(struct rtl_fe *fe);
'''

install = function('fe_install')
admission = install[install.index(' if(port->retired)'):install.index(' if(fe_flow(fe,cookie))')]
shim += r'''
#define netif_running(d) ((d)!=NULL)
#define netif_carrier_ok(d) ((d)!=NULL)
struct rtl_fe_rule {struct rtl_fe_port *input,*output;};
static int admission_check(struct rtl_fe *fe,struct rtl_fe_port *input,struct rtl_fe_port *output) {
 struct rtl_fe_port *port=input;
 struct rtl_fe_rule data={input,output};
'''+admission+r'''
 return 0;
}
'''

check = r'''
int main(void)
{
 static struct rtl_fe f;
 struct net_device p6={6},lan={1},pon={7};
 struct rtl_fe_port fp6={.netdev=&p6,.source=6},flan={.netdev=&lan,.source=1},fpon={.netdev=&pon,.source=7};
 struct rtl_fe_flow a={.input=&fp6,.output=&flan,.id=1};
 struct rtl_fe_flow b={.input=&flan,.output=&fp6,.id=2};
 struct rtl_fe_flow c={.input=&flan,.output=&fpon,.id=3};
 f.fttr_netdev=&p6;
 assert(!rtl_fe_fttr_open(&f) && f.fttr_live && f.fttr_cf.destination==16);
 assert(!rtl_fe_fttr_set(&f,true) && f.fttr_requested && f.fttr_admitted && f.enabled);
 assert(f.prepares==1 && !f.pon_requested);
 assert(!admission_check(&f,&fp6,&flan) && !admission_check(&f,&flan,&fp6));
 assert(admission_check(&f,&fpon,&flan)==-EAGAIN && admission_check(&f,&flan,&fpon)==-EAGAIN);
 assert(!rtl_fe_enable(&f) && f.pon_requested && f.prepares==1);
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
 assert(f.flows==&c && !c.deleting && !f.lock.held && f.enabled);
 assert(!rtl_fe_disable(&f) && !f.enabled && !f.prepared && !f.flows);
 /* A pending FTTR request opens FE only after the parent opens. */
 assert(!rtl_fe_fttr_set(&f,true) && !f.enabled);
 unsigned int prepares=f.prepares;
 assert(!rtl_fe_fttr_open(&f) && f.enabled && f.fttr_admitted && f.prepares==prepares+1);
 assert(!rtl_fe_enable(&f) && f.pon_requested && f.prepares==prepares+1);
 /* Removing PON preserves the FTTR path and withdraws both PON directions. */
 struct rtl_fe_flow d={.input=&fpon,.output=&flan,.id=4};
 a.next=&c;c.next=&d;d.next=NULL;f.flows=&a;c.deleting=d.deleting=false;
 assert(!rtl_fe_disable(&f) && !f.pon_requested && f.enabled && f.fttr_admitted);
 assert(f.flows==&a && !a.next && c.deleting && d.deleting);
 assert(!admission_check(&f,&fp6,&flan));
 assert(admission_check(&f,&fpon,&flan)==-EAGAIN && admission_check(&f,&flan,&fpon)==-EAGAIN);
 /* A PON deletion error remains visible and blocks an enable retry. */
 assert(!rtl_fe_enable(&f));
 a.next=&c;c.next=NULL;c.deleting=false;f.delete_error_id=3;
 assert(rtl_fe_disable(&f)==-ETIMEDOUT && !f.pon_requested && f.enabled && f.fttr_admitted);
 assert(rtl_fe_enable(&f)==-ETIMEDOUT && !f.pon_requested);
 f.delete_error_id=0;
 assert(!rtl_fe_enable(&f) && f.pon_requested && !a.next);
 assert(!rtl_fe_disable(&f) && !f.pon_requested && f.flows==&a);
 assert(!rtl_fe_fttr_close(&f) && !f.enabled && !f.prepared && !f.flows);
 /* Failed standalone prepare/rollback keeps its snapshot until it can recover. */
 f.prepare_error=-EIO;f.restore_error=-ETIMEDOUT;
 assert(rtl_fe_fttr_open(&f)==-EIO && !f.enabled && !f.fttr_live && f.prepared);
 prepares=f.prepares;
 assert(rtl_fe_fttr_open(&f)==-ETIMEDOUT && !f.fttr_live && f.prepares==prepares);
 f.prepare_error=f.restore_error=0;
 assert(!rtl_fe_fttr_open(&f) && f.enabled && f.fttr_admitted && f.prepares==prepares+1);
 f.restore_error=-ETIMEDOUT;
 assert(rtl_fe_fttr_set(&f,false)==-ETIMEDOUT && !f.enabled && f.prepared && !f.fttr_admitted);
 f.restore_error=0;
 assert(!rtl_fe_fttr_set(&f,true) && f.enabled && f.fttr_admitted);
 assert(!rtl_fe_fttr_close(&f) && !f.enabled && !f.prepared);
 /* Both endpoints on P6 still participate in the existing withdrawal barrier. */
 assert(!rtl_fe_fttr_open(&f) && f.fttr_admitted);
 a.input=a.output=&fp6;a.next=NULL;f.flows=&a;
 assert(!admission_check(&f,&fp6,&fp6));
 assert(!rtl_fe_fttr_set(&f,false) && !f.flows && !f.fttr_admitted);
 assert(!rtl_fe_fttr_close(&f));
 puts("P6 FE: standalone/shared enable, both-direction PON/P6 admission and withdrawal, failed cleanup/re-enable, prepare rollback and last-owner restore");
}
'''

with tempfile.TemporaryDirectory(dir=root/'artifacts/fttr') as directory:
    directory = Path(directory)
    (directory/'check.c').write_text(shim+'\n'+'\n'.join(function(name) for name in
        ['fe_fttr_route_locked','fe_fttr_quiesce_locked','fe_disable_locked','fe_enable_locked',
         'fe_pon_quiesce_locked','rtl_fe_enable','rtl_fe_disable','rtl_fe_fttr_open','rtl_fe_fttr_close','rtl_fe_fttr_set'])+'\n'+check)
    subprocess.run(['cc','-O1','-g','-fsanitize=address,undefined','-Wall','-Wextra','-Werror','-I'+str(headers),str(directory/'check.c'),'-o',str(directory/'check')],check=True)
    subprocess.run([str(directory/'check')],check=True)
