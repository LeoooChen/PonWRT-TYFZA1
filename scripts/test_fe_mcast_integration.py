#!/usr/bin/env python3
"""Execute production FE install/delete and native multicast tables together."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
native = (drivers / 'rtl9617c-fe.c').read_text()

def function(name):
    start = re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{', native, re.M).start()
    end = native.index('{', start)+1
    depth = 1
    while depth:
        depth += (native[end] == '{')-(native[end] == '}')
        end += 1
    return native[start:end]+'\n'

code = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include "rtl9617c-fe-pipeline.h"
#include "rtl9617c-fe-mcast-owner.h"
#include "rtl9617c-fe-mcast-route.h"
#include "rtl9617c-fe-mcast-profile.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32,__le32;typedef uint64_t u64;
#include "linux/rtl9617c-bridge.h"
#define ETH_ALEN 6
#define VLAN_CFI_MASK 4096
#define VLAN_PRIO_SHIFT 13
#define BIT(n) (1U<<(n))
#define HZ 100
#define system_wq 0
#define GFP_KERNEL 0
#define dma_wmb() ((void)0)
#define cpu_to_le32(v) (v)
#define kfree free
#define kzalloc(n,f) calloc(1,n)
static void *kmemdup(const void *p,size_t n,int f){(void)f;void *v=malloc(n);if(v)memcpy(v,p,n);return v;}
struct list_head {struct list_head *next,*prev;};
static void INIT_LIST_HEAD(struct list_head *h){h->next=h->prev=h;}
static void list_add_tail(struct list_head *n,struct list_head *h){n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n;}
static void list_del(struct list_head *n){n->prev->next=n->next;n->next->prev=n->prev;}
static bool list_empty(struct list_head *h){return h->next==h;}
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry_safe(p,n,h,m) for(struct list_head *c=(h)->next,*nc=c->next;c!=(h) && ((p)=container_of(c,__typeof__(*(p)),m),(n)=container_of(nc,__typeof__(*(n)),m),1);c=nc,nc=c->next)
static bool test_bit(unsigned i,unsigned long *b){return (b[i/64]>>(i%64))&1;}
static void __set_bit(unsigned i,unsigned long *b){b[i/64]|=1UL<<(i%64);}
static void __clear_bit(unsigned i,unsigned long *b){b[i/64]&=~(1UL<<(i%64));}
struct rb_node {int unused;};
struct net_device {bool running,carrier;};
struct rtl_fe;
struct rtl_fe_port {struct rtl_fe *fe;struct net_device *netdev;unsigned source,destination,llid,wifi_id;bool retired;};
struct rtl_fe_wifi_peer {unsigned destination;};
static bool netif_running(struct net_device *d){return d->running;}
static bool netif_carrier_ok(struct net_device *d){return d->carrier;}
static bool rtl_fttr_mcast_ready(struct net_device *d){return d && d->running && d->carrier;}
static void ether_addr_copy(u8 *a,const u8 *b){memcpy(a,b,6);}
static void put_unaligned_be32(u32 v,void *p){v=__builtin_bswap32(v);memcpy(p,&v,4);}
static unsigned long jiffies=100;
'''
for name in ('rtl_fe_mc_direct','rtl_fe_flow','rtl_fe_rule','rtl_fe_mc_rule'):
    code += re.search(r'struct '+name+r' \{.*?\n\};', native, re.S)[0]+'\n'
code += r'''
static int fe_mcast_direct_stage(struct rtl_fe *fe,struct rtl_fe_flow *flow,struct rtl_fe_mc_rule *mc){(void)fe;(void)flow;for(unsigned i=0;i<mc->count;i++)assert(!mc->direct[i].wifi_id);return 0;}
struct rtl_fe {
 struct net_device *fttr_netdev,*fttr_mcast;
 bool fttr_admitted,pon_requested,enabled;
 struct list_head flows,retries;
 struct rtl_fe_flow *indexed;
 unsigned int cookies,cleanup_work;
 unsigned long hashes[1024];void *memory;
 struct rtl_fe_mc_owner multicast;
 struct rtl_fe_mc_route mc_route;
 struct rtl_fe_mc_profile mc_profile;
 int error;
 unsigned long installs,deletes;
 unsigned int reg[0xb000/4],groups[256][2],actions[64][3],map[256],dbuf[8],elp[64][2];
 unsigned int keys[256][11],fib[1040][7],masks[128][4];
 unsigned int age,age_fail,cache_fail,ni_fail,events[64],event_count;
};
static struct rtl_fe_flow *fe_flow(struct rtl_fe *fe,unsigned long cookie){return fe->indexed && fe->indexed->cookie==cookie?fe->indexed:NULL;}
static void fe_index_flow(struct rtl_fe *fe,struct rtl_fe_flow *flow){assert(!fe->indexed);fe->indexed=flow;}
static void rb_erase(struct rb_node *n,unsigned *root){(void)n;(*root)++;}
static void mod_delayed_work(int q,unsigned *w,unsigned d){assert(q==0 && d==HZ);(*w)++;}
static unsigned int read_reg(void *p,unsigned reg){return ((struct rtl_fe *)p)->reg[reg/4];}
static void write_reg(void *p,unsigned reg,unsigned val){((struct rtl_fe *)p)->reg[reg/4]=val;}
static int indirect(void *p,unsigned reg,unsigned command){
 struct rtl_fe *f=p;unsigned *row,count,data;
 if(reg==0x3928){
  if(command&BIT(30)){
   f->age=read_reg(f,0x3938);f->events[f->event_count++]=f->age?1:2;
   if(f->age) {
    bool referenced=false;
    for(unsigned int g=1;g<256;g++)referenced|=f->groups[g][0] && f->multicast.groups[g].state==RTL_FE_MC_REFERENCED;
    assert(referenced);
   }
  } else write_reg(f,0x3938,f->age);
  if(f->age_fail){f->age_fail=0;return -ETIMEDOUT;}return 0;
 }
 if(reg==0x38b0){f->events[f->event_count++]=3;if(f->cache_fail){f->cache_fail=0;return -ETIMEDOUT;}return 0;}
 if(reg==0xaa64){
  row=f->groups[command&255];count=2;data=0xaa6c;
  if(command&BIT(30)){f->events[f->event_count++]=read_reg(f,data)?4:5;
   if(f->ni_fail && !read_reg(f,data)){f->ni_fail=0;return -ETIMEDOUT;}}
 } else if(reg==0x1634){row=f->actions[command&63];count=3;data=0x1640;}
 else if(reg==0x1654){row=&f->map[command&255];count=1;data=0x1658;}
 else if(reg==0x164c){row=&f->dbuf[command&7];count=1;data=0x1650;}
 else if(reg==0x3380){row=f->keys[command&255];count=11;data=0x33ac;}
 else if(reg==0x33b0){row=f->fib[command&2047];count=7;data=0x33cc;}
 else if(reg==0x3910){row=f->masks[command&127];count=4;data=0x3920;}
 else {assert(reg==0x1420);row=f->elp[command&63];count=2;data=0x1428;}
 for(unsigned i=0;i<count;i++){if(command&BIT(30))row[i]=read_reg(f,data-4*i);else write_reg(f,data-4*i,row[i]);}
 return 0;
}
static struct rtl_dq_io fe_io(struct rtl_fe *fe){return (struct rtl_dq_io){fe,read_reg,write_reg,indirect};}
static int fe_source_get(struct rtl_fe *f,const u8 *a,unsigned *i){(void)f;(void)a;(void)i;assert(0);return -1;}
static int fe_destination_get(struct rtl_fe *f,struct rtl_fe_flow *v,const u8 *a){(void)f;(void)v;(void)a;assert(0);return -1;}
static int fe_interface_get(struct rtl_fe *f,struct rtl_fe_flow *v){(void)f;(void)v;assert(0);return -1;}
static int fe_mtu_get(struct rtl_fe *f,struct rtl_fe_flow *v,unsigned n){(void)f;(void)v;(void)n;assert(0);return -1;}
static int fe_source_put(struct rtl_fe *f,unsigned i){(void)f;(void)i;assert(0);return -1;}
static int fe_destination_put(struct rtl_fe *f,struct rtl_fe_flow *v){(void)f;(void)v;assert(0);return -1;}
static int fe_interface_put(struct rtl_fe *f,struct rtl_fe_flow *v){(void)f;(void)v;assert(0);return -1;}
static void fe_mtu_put(struct rtl_fe *f,struct rtl_fe_flow *v){(void)f;(void)v;assert(0);}
static struct rtl_fe_wifi_peer *fe_wifi_peer(struct rtl_fe *f,struct rtl_fe_port *p,const u8 *a){(void)f;(void)p;(void)a;return NULL;}
'''
code += 'static void fe_mcast_host_remove(struct rtl_fe *f,struct rtl_fe_flow *v){(void)f;assert(!v->mc_host);}'
code += 'static int fe_mcast_host_publish(struct rtl_fe *f,struct rtl_fe_flow *v,struct rtl_fe_mc_host *h){(void)f;(void)v;(void)h;assert(0);return -1;}'
code += function('fe_flow_port')+function('fe_flow_fttr')+function('fe_delete')+function('fe_install')
code += r'''
int main(void){
 static struct rtl_fe fe;
 struct net_device lan={true,true},other={true,true},p6={true,true},shared={true,true};
 struct rtl_fe_port input={.fe=&fe,.netdev=&lan},out={.fe=&fe,.netdev=&other,.source=1,.destination=1},fttr={.fe=&fe,.netdev=&p6,.source=6,.destination=6};
 struct rtl_fe_mc_rule mc={.count=2,.replica={{.destination=6,.tags=1,.tag={{0x8100,4095}}},{.destination=1}},.port={&fttr,&out}};
 struct rtl_fe_rule rule={.bridge=true,.input=&input,.output=&out,.multicast=&mc,.ethernet_protocol=0x800,.key={.mask=61},.action={.bridge=1,.check_mask=61}};
 memcpy(rule.source,"\2\0\0\0\0\1",6);memcpy(rule.destination,"\1\0\x5e\1\2\3",6);
 fe.fttr_netdev=&p6;fe.fttr_mcast=&shared;fe.fttr_admitted=fe.enabled=true;
 fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);
 INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);
 for(unsigned i=0;i<4;i++)fe.reg[(0x1704+i*4)/4]=0x8100;
 fe.reg[0xaa60/4]=0x2f0ff;
 fe.reg[0xa1c0/4]=0x24009b;
 fe.map[1]=fe.map[129]=1;fe.map[70]=fe.map[198]=8;fe.reg[0x1600/4]=0x80;
 rule.action.vlan_set=true;rule.action.vlan_count=1;rule.action.vlan[0].priority=7;
 shared.running=false;assert(fe_install(&input,rule,7)==-EAGAIN && !fe.indexed);
 shared.running=true;fttr.retired=true;assert(fe_install(&input,rule,7)==-EAGAIN && !fe.indexed);fttr.retired=false;
 assert(!fe_install(&input,rule,7) && fe.indexed && fe.indexed->installed);
 struct rtl_fe_flow *flow=fe.indexed;
 assert(fe_flow_fttr(&fe,flow) && fe_flow_port(flow,&fttr)); /* P6 is a mirror, not terminal output. */
 unsigned int *a=fe.memory+RTL_FE_ACTION_OFFSET+flow->hash_index*RTL_FE_ACTION_BYTES;
 assert((a[0]&0x7ffff)==(1U<<18|1U<<8|0x70));
 assert(!a[4] && !(a[5]&31)); /* No common final-VLAN/priority edits. */
 assert(fe.dbuf[0]==0x1b && fe.groups[1][0]==3 && flow->replica_count==2);
 fe.event_count=0;fe.cache_fail=1;
 assert(fe_delete(&fe,flow)==-ETIMEDOUT && flow->published && fe.groups[1][0]==3);
 assert(fe.event_count==2 && fe.events[0]==2 && fe.events[1]==3);
 fe.ni_fail=1;fe.event_count=0;
 assert(fe_delete(&fe,flow)==-ETIMEDOUT && !flow->published && flow->mc_group==1);
 assert(fe.multicast.actions[0].users==1 && fe.groups[1][0]==3);
 assert(fe.events[0]==2 && fe.events[1]==3 && fe.events[2]==5);
 assert(!fe_delete(&fe,flow));fe.indexed=NULL;
 assert(fe.multicast.groups[1].state==RTL_FE_MC_RETIRED && !fe.groups[1][0]);
 assert(fe.multicast.actions[0].exposed && !fe.multicast.actions[0].users && fe.actions[0][1]);
 assert(list_empty(&fe.flows) && list_empty(&fe.retries));
 /* A referenced group is quarantined even if Hash publication completion
  * is lost. It must not become a different group on the next installation. */
 fe.age_fail=1;fe.event_count=0;
 assert(fe_install(&input,rule,8)==-ETIMEDOUT);fe.indexed=NULL;
 assert(fe.multicast.groups[2].state==RTL_FE_MC_RETIRED && !fe.groups[2][0]);
 assert(list_empty(&fe.flows) && list_empty(&fe.retries));
 rule.multicast_ip=true;rule.key.mask=55;rule.action.check_mask=55;
 rule.key.source=0x0a000001;rule.key.destination=0xef010203;rule.key.protocol=17;
 fe.event_count=0;
 fe.keys[7][10]=1U<<27;
 assert(fe_install(&input,rule,9)==-EBUSY);fe.indexed=NULL;
 assert(list_empty(&fe.flows) && !fe.mc_profile.owned && fe.keys[7][10]==(1U<<27));
 fe.keys[7][10]=0;
 assert(!fe_install(&input,rule,9) && fe.indexed->installed && fe.mc_profile.ready);
 assert(fe.keys[128][10]==(1U<<27) && fe.reg[0x372c/4]==3);
 assert(!fe_delete(&fe,fe.indexed));fe.indexed=NULL;
 assert(!rtl_fe_mc_profile_restore(&(struct rtl_dq_io){&fe,read_reg,write_reg,indirect},&fe.mc_profile));
 assert(!fe.keys[128][10] && !fe.reg[0x372c/4]);
 assert(!rtl_fe_mc_route_restore(&(struct rtl_dq_io){&fe,read_reg,write_reg,indirect},&fe.mc_route));
 free(fe.memory);
 puts("Production FE multicast: full recipients before Hash, P6 admission, cache/NI failures and quarantine passed");
}
'''
with tempfile.TemporaryDirectory(prefix='fe-mcast-') as name:
    work = Path(name)
    (work/'check.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined',
                    '-I'+str(drivers),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(work/'check.c'),'-o',str(work/'check')],check=True)
    subprocess.run([str(work/'check')],check=True)
