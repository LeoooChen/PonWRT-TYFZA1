#!/usr/bin/env python3
"""Execute native gate callbacks and actual bridge writer lock pairs offline."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DRIVERS=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel',type=Path,required=True)
args=parser.parse_args()


def function(text,name):
    match=re.search(r'^(?:static )?[^\n]*\b'+name+r'\([^;]*?\)\s*\{',text,re.M)
    start=match.start();end=text.index('{',start)+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]+'\n'


source=(DRIVERS/'rtl9617c-fe-bridge.h').read_text()
bridge=(args.kernel/'net/bridge/br_multicast.c').read_text()
private=(args.kernel/'net/bridge/br_private.h').read_text()
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "rtl9617c-fe-mcast-gate.h"
#include "linux/rtl9617c-mcast-pe.h"
#define CONFIG_ARCH_RTL9617C 1
#define CONFIG_BRIDGE_IGMP_SNOOPING 1
#include "linux/rtl9617c-bridge.h"
#define IS_ENABLED(v) 0
#define CONFIG_IPV6 0
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,n) ((v)=(n))
#define system_wq 0
struct net_device {int unused;};
struct rtl_fe_bridge;
struct rtl_fe {struct rtl_fe_mc_gate mc_gate;int mc_lock,mc_flush;unsigned int profile,writes;bool lost;struct rtl_fe_bridge *bridge;
 struct {struct rtl_mc_pe_table *table;}mc_pe[2];};
struct rtl_fe_bridge {struct rtl_fe *fe;bool stopping;unsigned int work;};
static unsigned int rd(void *p,unsigned int r){assert(r==0x372c);return ((struct rtl_fe *)p)->profile;}
static void wr(void *p,unsigned int r,unsigned int v){struct rtl_fe *f=p;assert(r==0x372c);f->writes++;if(!f->lost)f->profile=v;}
static struct rtl_dq_io fe_io(struct rtl_fe *f){return (struct rtl_dq_io){f,rd,wr,NULL};}
#define spin_lock_irqsave(p,f) do {assert(!*(p));*(p)=1;(f)=0;} while(0)
#define spin_unlock_irqrestore(p,f) do {assert(*(p)&&!(f));*(p)=0;} while(0)
static void atomic_set(int *p,int v){*p=v;}
static void mod_delayed_work(int q,unsigned int *w,unsigned int d){assert(!q&&!d);(*w)++;}
static void dma_wmb(void){}
'''
code+=function((DRIVERS/'rtl9617c-fe-mcast-pe.h').read_text(),'fe_mcast_pe_close')
code+=function(source,'fe_mcast_change_begin')+function(source,'fe_mcast_change_end')
code+=r'''
static struct rtl_fe state;
static struct rtl_fe_bridge owner={.fe=&state};
static const struct rtl9617c_bridge_mcast_ops ops={.context=&owner,.change_begin=fe_mcast_change_begin,.change_end=fe_mcast_change_end};
static unsigned int rcu;
const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_mcast_begin(struct net_device *d){rcu++;ops.change_begin(ops.context,d);return &ops;}
void rtl9617c_bridge_mcast_end(const struct rtl9617c_bridge_mcast_ops *o){assert(rcu&&o==&ops);o->change_end(o->context);rcu--;}
struct timer_list {bool pending;};
struct bridge_mcast_own_query {int unused;};
struct net_bridge {struct net_device *dev;int multicast_lock;const struct rtl9617c_bridge_mcast_ops *native_mcast_writer;};
struct net_bridge_mcast {struct net_bridge *br;unsigned int multicast_querier,multicast_query_response_interval;
 struct {struct timer_list timer,delay_timer;} ip4_other_query;struct bridge_mcast_own_query ip4_own_query;};
static void spin_lock_bh(int *p){assert(!*p);*p=1;}
static void spin_unlock_bh(int *p){assert(*p);*p=0;}
static bool timer_pending(struct timer_list *t){return t->pending;}
static unsigned long jiffies=100;
static void mod_timer(struct timer_list *t,unsigned long expiry){assert(expiry==110);assert(state.profile==3&&!state.mc_gate.active&&state.mc_gate.writers==1);t->pending=true;}
static void br_multicast_start_querier(struct net_bridge_mcast *m,struct bridge_mcast_own_query *q){(void)m;(void)q;assert(state.mc_gate.writers==1&&state.profile==3);}
'''
code+=private[private.index('static inline void br_multicast_write_begin'):private.rfind('#endif')]
code+=function(bridge,'br_multicast_set_querier')
code+=r'''
struct net_bridge_port {struct net_bridge *br;struct net_device *dev;unsigned long flags;};
#undef WRITE_ONCE
#define WRITE_ONCE(v,n) do {assert(state.mc_gate.writers==1&&state.profile==3&&rcu==1);(v)=(n);} while(0)
'''
code+=function(private,'br_port_flags_set')
code+=r'''
struct nlattr {unsigned value;};
static unsigned nla_get_u8(struct nlattr *n){return n->value;}
struct netlink_ext_ack {char *_msg;};
static int flag_error;static unsigned changed;
static int br_switchdev_set_port_flag(struct net_bridge_port *p,unsigned long f,unsigned long m,struct netlink_ext_ack *a){(void)p;(void)f;(void)m;(void)a;assert(!rcu&&!state.mc_gate.writers);return flag_error;}
static void br_port_flags_change(struct net_bridge_port *p,unsigned long mask){assert(!rcu&&!state.mc_gate.writers);changed|=mask;(void)p;}
#define netdev_err(...) ((void)0)
'''
code+=function((args.kernel/'net/bridge/br_netlink.c').read_text(),'br_set_port_flag')
code+=function((args.kernel/'net/bridge/br_sysfs_if.c').read_text(),'store_flag')
code+=function(bridge,'br_multicast_query_delay_expired')
code+=r'''
int main(void){
 struct rtl_dq_io io=fe_io(&state);struct net_device dev;
 struct net_bridge br={.dev=&dev};struct net_bridge_mcast m={.br=&br,.multicast_query_response_interval=10};
 state.profile=3;
 struct rtl_mc_pe_table pe_table={.epoch=7};state.mc_pe[0].table=&pe_table;
 assert(!rtl_fe_mc_gate_open(&io,&state.mc_gate,0)&&state.profile==4);
 assert(!br_multicast_set_querier(&m,1));
 assert(!pe_table.epoch);
 assert(state.profile==3&&!state.mc_gate.active&&state.mc_gate.epoch==2&&!state.mc_gate.writers&&!rcu&&!br.multicast_lock);
 assert(state.mc_flush&&owner.work==1&&m.multicast_querier);
 assert(rtl_fe_mc_gate_open(&io,&state.mc_gate,0)==-ESTALE);
 assert(!rtl_fe_mc_gate_open(&io,&state.mc_gate,2));
 /* No-op writer still brackets generation and closes before its early exit. */
 assert(!br_multicast_set_querier(&m,1)&&state.profile==3&&state.mc_gate.epoch==4&&!rcu);
 assert(!rtl_fe_mc_gate_open(&io,&state.mc_gate,4));
 const struct rtl9617c_bridge_mcast_ops *a=rtl9617c_bridge_mcast_begin(&dev);
 unsigned long long epoch=state.mc_gate.epoch;
 assert(rtl_fe_mc_gate_open(&io,&state.mc_gate,epoch)==-ESTALE);
 const struct rtl9617c_bridge_mcast_ops *b=rtl9617c_bridge_mcast_begin(&dev);
 rtl9617c_bridge_mcast_end(a);
 assert(state.mc_gate.writers==1&&rtl_fe_mc_gate_open(&io,&state.mc_gate,state.mc_gate.epoch)==-ESTALE);
 rtl9617c_bridge_mcast_end(b);
 assert(!state.mc_gate.writers&&!rcu&&!rtl_fe_mc_gate_open(&io,&state.mc_gate,state.mc_gate.epoch));
 /* Real netlink and sysfs flag publications close lookup before assignment,
  * and never pin an RCU callback across the sleeping switchdev validation. */
 struct net_bridge_port port={.br=&br,.dev=&dev,.flags=8};struct nlattr off={0},on={1};struct nlattr *tb[1]={&off};
 epoch=state.mc_gate.epoch;br_set_port_flag(&port,tb,0,8);
 assert(!port.flags&&state.mc_gate.epoch==epoch+2&&!state.mc_gate.active&&!rcu);
 epoch=state.mc_gate.epoch;br_set_port_flag(&port,tb,0,8);assert(state.mc_gate.epoch==epoch);
 assert(!rtl_fe_mc_gate_open(&io,&state.mc_gate,epoch));tb[0]=&on;br_set_port_flag(&port,tb,0,8);
 assert(port.flags==8&&!state.mc_gate.active&&!rcu);
 assert(!rtl_fe_mc_gate_open(&io,&state.mc_gate,state.mc_gate.epoch));
 flag_error=-EOPNOTSUPP;epoch=state.mc_gate.epoch;assert(store_flag(&port,0,8)==-EOPNOTSUPP);
 assert(port.flags==8&&state.mc_gate.epoch==epoch&&state.mc_gate.active);
 flag_error=0;assert(!store_flag(&port,0,8)&&!port.flags&&changed==8&&!state.mc_gate.active);
 assert(!rtl_fe_mc_gate_open(&io,&state.mc_gate,state.mc_gate.epoch));
 epoch=state.mc_gate.epoch;br_multicast_query_delay_expired(NULL);
 assert(state.mc_gate.epoch==epoch+2&&!state.mc_gate.active&&!rcu);
 assert(!rtl_fe_mc_gate_open(&io,&state.mc_gate,state.mc_gate.epoch));
 state.lost=true;fe_mcast_change_begin(&owner,&dev);fe_mcast_change_end(&owner);
 assert(state.mc_gate.error==-EIO&&state.mc_gate.active);
 state.lost=false;rtl_fe_mc_gate_close(&io,&state.mc_gate);
 assert(!state.mc_gate.active&&state.profile==3&&rtl_fe_mc_gate_open(&io,&state.mc_gate,state.mc_gate.epoch)==-EIO);
 puts("Bridge multicast gate: actual querier mutation/early exit, overlapping writers, stale publication and direct-IO failure passed");
}
'''
with tempfile.TemporaryDirectory(prefix='mcast-gate-') as name:
    root=Path(name);(root/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-fsanitize=address,undefined',
        '-I'+str(DRIVERS),'-I'+str(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/include'),
        '-Du8=uint8_t','-Du16=uint16_t','-Du32=uint32_t','-Du64=uint64_t',str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)

# Check the patch's resulting source against the explicit writer inventory.
writers={
 'br_multicast.c': ['br_multicast_group_expired','br_multicast_port_group_expired','br_multicast_group_src_expired',
  'br_multicast_add_group','br_multicast_router_expired','br_multicast_local_router_expired','br_multicast_querier_expired',
  'br_multicast_port_query_expired','br_multicast_port_group_rexmit','br_multicast_port_ctx_deinit','br_multicast_del_port',
  'br_multicast_enable_port_ctx','br_multicast_disable_port_ctx','br_ip4_multicast_igmp3_report','br_ip6_multicast_mld2_report',
  'br_ip4_multicast_query','br_ip6_multicast_query','br_multicast_leave_group','br_multicast_pim','br_ip4_multicast_mrd_rcv',
  'br_ip6_multicast_mrd_rcv','br_multicast_query_expired','br_multicast_toggle_one_vlan','br_multicast_dev_del',
  'br_multicast_set_router','br_multicast_set_port_router','br_multicast_toggle','br_multicast_set_querier',
  'br_multicast_set_igmp_version','br_multicast_set_mld_version'],
 'br_mdb.c':['__br_mdb_add','__br_mdb_del','br_mdb_flush'],
 'br_multicast_eht.c':['br_multicast_eht_set_entry_expired','br_multicast_eht_set_expired','br_multicast_eht_set_hosts_limit']}
for filename,names in writers.items():
    text=(args.kernel/'net/bridge'/filename).read_text()
    for name in names:
        body=function(text,name)
        assert 'br_multicast_write_lock' in body and 'br_multicast_write_unlock' in body,name
        assert not re.search(r'spin_(?:un)?lock(?:_bh)?\(&.*multicast_lock',body),name
print('PASS:36 multicast writer entry points use paired native invalidation')
netlink=(args.kernel/'net/bridge/br_netlink.c').read_text()
assert function(netlink,'br_setport').count('br_port_flags_set(p, old_flags)')==2
assert not re.search(r'p->flags\s*[|&]?=',function(netlink,'br_setport'))
print('PASS: netlink flags, both rollback branches and sysfs publication use the native gate')
