#!/usr/bin/env python3
"""Run actual Linux unknown multicast selection into production FE installation."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel',type=Path,required=True)
args=parser.parse_args()
source=(args.kernel/'net/bridge/br_forward.c').read_text()
def function(text,name):
    start=re.search(r'^(?:static )?[^\n]*\b'+name+r'\([^;]*?\)\s*\{',text,re.M).start()
    end=text.index('{',start)+1;depth=1
    while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]+'\n'

learning=(root/'scripts/test_bridge_mcast_learning.py').read_text()
setup=learning.split("code+=r'''\nint main(void)",1)[0]
scope={'__file__':str(root/'scripts/test_bridge_mcast_learning.py')}
exec(compile(setup,'complete-multicast-fixture','exec'),scope)
code=scope['code'].replace('bool gso,rtl9617c_fttr_ingress;','bool cloned,gso,rtl9617c_fttr_ingress;')
code+=r'''
#define CONFIG_ARCH_RTL9617C 1
#define BR_HAIRPIN_MODE 1
#define BR_FLOOD 2
#define BR_MCAST_FLOOD 4
#define BR_BCAST_FLOOD 8
#define BR_PROXYARP 16
#define BR_PROXYARP_WIFI 32
#define BR_BPDU_FILTER 64
#define BR_MCAST_DIR_TX 1
#define unlikely(v) (v)
#define DEV_STATS_INC(dev,field) ((void)(dev))
#define IS_ERR(v) ((uintptr_t)(v)>(uintptr_t)-4096)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(v) ((intptr_t)(v))
enum skb_drop_reason {SKB_DROP_REASON_NO_TX_TARGET,SKB_DROP_REASON_NOMEM,SKB_DROP_REASON_NOT_SPECIFIED};
enum br_pkt_type {BR_PKT_UNICAST,BR_PKT_MULTICAST,BR_PKT_BROADCAST};
struct net_bridge_port;
struct net_bridge {struct net_device *dev;struct net_bridge_port *port_list;};
struct net_bridge_vlan_group {bool allowed;};
struct net_bridge_port {struct net_bridge *br;struct net_device *dev;unsigned flags,state;struct net_bridge_vlan_group vlan;bool isolated;struct net_bridge_port *next;};
#define list_for_each_entry_rcu(p,head,member) for((p)=*(head);(p);(p)=(p)->next)
#define BR_INPUT_SKB_CB(s) (&(struct {bool proxyarp_replied;struct net_device *brdev;}){false,devices[10]})
#define eth_hdr(s) (&(struct {u8 h_dest[6];}){{(s)->frame.destination[0],0,0x5e,1,2,3}})
static struct rtl_fe_bridge *owner;
static unsigned delivered[16],failed_clone,clone_calls,drops;
u64 rtl9617c_bridge_mcast_capture(struct sk_buff *s,bool local){return fe_mcast_capture_begin(owner,s,local,++fe_mcast_ticket);}
void rtl9617c_bridge_mcast_select(struct sk_buff *s,struct net_device *d){fe_mcast_capture_select(owner,s,d);}
void rtl9617c_bridge_mcast_complete(u64 ticket,bool success){fe_mcast_capture_end(owner,ticket,success);}
static struct net_bridge_vlan_group *nbp_vlan_group_rcu(const struct net_bridge_port *p){return (void *)&p->vlan;}
static bool br_mst_is_enabled(const struct net_bridge_port *p){(void)p;return false;}
static bool br_allowed_egress(struct net_bridge_vlan_group *v,const struct sk_buff *s){(void)s;return v->allowed;}
static bool nbp_switchdev_allowed_egress(const struct net_bridge_port *p,const struct sk_buff *s){(void)p;(void)s;return true;}
static bool br_skb_isolated(const struct net_bridge_port *p,const struct sk_buff *s){(void)s;return p->isolated;}
static u8 br_multicast_igmp_type(struct sk_buff *s){(void)s;return 0;}
static void nbp_switchdev_frame_mark_tx_fwd_to_hwdom(struct net_bridge_port *p,struct sk_buff *s){(void)p;(void)s;}
static void br_multicast_count(struct net_bridge *b,struct net_bridge_port *p,struct sk_buff *s,u8 t,int d){(void)b;(void)p;(void)s;(void)t;(void)d;}
static struct sk_buff *skb_clone(struct sk_buff *s,int f){(void)f;if(++clone_calls==failed_clone)return NULL;struct sk_buff *n=malloc(sizeof(*n));assert(n);*n=*s;n->cloned=true;return n;}
static void __br_forward(const struct net_bridge_port *p,struct sk_buff *s,bool local){(void)local;delivered[p->dev->ifindex]++;s->dev=p->dev;fe_mcast_capture_output(owner,s);if(s->cloned)free(s);}
static void kfree_skb_reason(struct sk_buff *s,enum skb_drop_reason r){assert(!s->cloned);(void)r;drops++;}
static bool br_is_neigh_suppress_enabled(struct net_bridge_port *p,u16 vid){(void)p;(void)vid;return false;}
static void br_tc_skb_miss_set(struct sk_buff *s,bool miss){(void)s;(void)miss;}
'''
for name in ('should_deliver','deliver_clone','maybe_deliver','br_flood'):code+=function(source,name)
# Reuse the original known-good NI/FE owner initialization verbatim.
initial=learning.split("code+=r'''\nint main(void){",1)[1].split(' struct nf_hook_entries ours=',1)[0]
code+='int main(void){'+initial+r'''
 owner=&bridge;fdb_source=&lan;
 struct net_bridge linux_bridge={.dev=&br};
 struct net_bridge_port ports[3];linux_bridge.port_list=ports;
 struct net_device *outputs[]={&lan,&out,&shared};
 for(unsigned i=0;i<3;i++)ports[i]=(struct net_bridge_port){.br=&linux_bridge,.dev=outputs[i],.state=BR_STATE_FORWARDING,.flags=BR_MCAST_FLOOD,.vlan={true},.next=i<2?&ports[i+1]:NULL};
 struct rtl9617c_bridge_frame frame=skb.saved;
 for(unsigned scenario=0;scenario<8;scenario++) {
  fe.event_count=0;memset(delivered,0,sizeof(delivered));clone_calls=failed_clone=drops=0;
  ports[1].flags=ports[2].flags=BR_MCAST_FLOOD;ports[1].isolated=false;ports[1].vlan.allowed=true;
  if(scenario==1)ports[1].flags=0;
  if(scenario==2)ports[1].isolated=true;
  if(scenario==3)ports[1].vlan.allowed=false;
  if(scenario==4)ports[1].flags=ports[2].flags=0;
  if(scenario==5)failed_clone=1;
  if(scenario==6)failed_clone=2;
  skb=(struct sk_buff){.dev=&lan,.saved=frame,.frame=frame};skb.saved.multicast_epoch=fe.mc_gate.epoch;
  br_flood(&linux_bridge,&skb,BR_PKT_MULTICAST,true,false,0);fe_mcast_process(&bridge);
  if(failed_clone)assert(!fe.indexed&&!fe.mc_gate.active);
  else {
   unsigned expected=1+delivered[2]+delivered[7];
   assert(fe.indexed&&fe.indexed->replica_count==expected&&fe.mc_gate.active);
   unsigned group=fe.indexed->mc_group,bitmap=0;
   for(unsigned a=0;a<64;a++)if(fe.multicast.groups[group].bitmap[a/32]&BIT(a%32)) {
    unsigned d=fe.actions[a][1]>>24&63;assert(d==1||d==6||d==16);bitmap|=BIT(d);
   }
   assert(bitmap==(BIT(16)|(delivered[2]?BIT(1):0)|(delivered[7]?BIT(6):0)));
   struct sk_buff copy={.frame=frame};assert(!fe_mcast_host_receive(&bridge,&copy,group,0));
   copy.dev=&lan;assert(fe_mcast_host_local(&bridge,&copy,&br,0)==1);
   fe_mcast_invalidate(&fe);assert(fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);
   assert(!fe_delete(&fe,fe.indexed));
  }
  assert(!net.refs);
 }
 fe_mcast_purge(&bridge);free(fe.memory);
 puts("Unknown multicast: actual Linux flood selection through complete capture, FE/NI publication, CPU local copy, port/VLAN/isolation, host-only, clone failures and withdrawal passed");
}
'''
with tempfile.TemporaryDirectory(prefix='bridge-mcast-flood-') as name:
    tmp=Path(name);(tmp/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-g','-fsanitize=address,undefined',
                    '-I'+str(scope['DRIVERS']),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),
                    str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
