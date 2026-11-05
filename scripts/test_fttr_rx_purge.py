#!/usr/bin/env python3
"""Execute native FTTR withdrawal with held GRO, normal batches and RX fragments."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--revision', help='exercise an earlier revision to reproduce retained packets')
args = p.parse_args()
path = Path('port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c')
text = subprocess.check_output(['git', 'show', f'{args.revision}:{path}'], cwd=ROOT, text=True) if args.revision else (ROOT/path).read_text()


def function(name):
    start = re.search(r'^static (?:void|bool) ' + name + r'\(', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


common = r'''
#include <assert.h>
#include <arpa/inet.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define GRO_HASH_BUCKETS 8
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
struct list_head {struct list_head *next,*prev;};
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry_safe(p,n,h,m) \
 for(struct list_head *c=(h)->next,*nc=c->next;c!=(h) && \
 ((p)=container_of(c,__typeof__(*(p)),m),(n)=container_of(nc,__typeof__(*(n)),m),1);c=nc,nc=c->next)
static void init(struct list_head *p){p->next=p->prev=p;}
static void add(struct list_head *p,struct list_head *h){p->prev=h->prev;p->next=h;h->prev->next=p;h->prev=p;}
static void del(struct list_head *p){p->prev->next=p->next;p->next->prev=p->prev;init(p);}
static void __clear_bit(unsigned i,unsigned long *p){*p&=~(1UL<<i);}
struct net_device {struct {unsigned rx_dropped;}stats;};
struct sk_buff {struct list_head list;struct net_device *dev;unsigned protocol;bool vlan;unsigned id;};
struct gro_node {unsigned long bitmask;struct {struct list_head list;int count;}hash[GRO_HASH_BUCKETS];struct list_head rx_list;unsigned rx_count;};
struct napi_struct {struct gro_node gro;};
struct rtl_fttr_rx {struct sk_buff *skb;};
struct rtl_xram {struct net_device *fttr;unsigned engine_users,fttr_omci_type;struct napi_struct napi;struct {struct rtl_fttr_rx fttr_rx[8];}qm;};
static struct rtl_xram shared;
static struct net_device fttr,lan;
static unsigned freed[256],delivered[256],disable_calls,enable_calls,schedule_calls,sync_calls,resets;
static bool napi_enabled,poll_tail,bh,locked;
#define ASSERT_RTNL() assert(locked)
static bool skb_vlan_tag_present(const struct sk_buff *s){return s->vlan;}
static void skb_list_del_init(struct sk_buff *s){del(&s->list);}
static void kfree_skb(struct sk_buff *s){assert(bh && !napi_enabled && !poll_tail && !freed[s->id] && !delivered[s->id]);freed[s->id]++;free(s);}
static void napi_disable(struct napi_struct *n){assert(n==&shared.napi && napi_enabled && !bh);disable_calls++;napi_enabled=false;poll_tail=true;}
static void synchronize_net(void){assert(locked && !bh);sync_calls++;poll_tail=false;}
static void napi_enable(struct napi_struct *n){assert(n==&shared.napi && !napi_enabled && !bh && !poll_tail);enable_calls++;napi_enabled=true;}
static void napi_schedule(struct napi_struct *n){assert(n==&shared.napi && napi_enabled && !bh);schedule_calls++;}
static void local_bh_disable(void){assert(!bh && !napi_enabled && !poll_tail);bh=true;}
static void local_bh_enable(void){assert(bh);bh=false;}
static void netif_receive_skb_list(struct list_head *head){
 struct sk_buff *s,*n;
 assert(bh && !napi_enabled && !poll_tail);
 list_for_each_entry_safe(s,n,head,list){
  assert(s->dev!=&fttr || (!s->vlan && s->protocol==htons(shared.fttr_omci_type)));
  assert(!freed[s->id] && !delivered[s->id]);delivered[s->id]++;skb_list_del_init(s);free(s);
 }
}
static void rtl_fttr_rx_reset(struct rtl_fttr_rx *rx){resets++;if(rx->skb)kfree_skb(rx->skb);rx->skb=NULL;}
static struct sk_buff *packet(unsigned id,struct net_device *dev,unsigned proto,bool vlan){
 struct sk_buff *s=calloc(1,sizeof(*s));assert(s);init(&s->list);s->id=id;s->dev=dev;s->protocol=htons(proto);s->vlan=vlan;return s;
}
static void held(unsigned bucket,unsigned id,struct net_device *dev,unsigned proto,bool vlan){
 struct gro_node *gro=&shared.napi.gro;add(&packet(id,dev,proto,vlan)->list,&gro->hash[bucket].list);
 gro->hash[bucket].count++;gro->bitmask|=1UL<<bucket;
}
static void normal(unsigned id,struct net_device *dev,unsigned proto,bool vlan,unsigned segments){
 add(&packet(id,dev,proto,vlan)->list,&shared.napi.gro.rx_list);shared.napi.gro.rx_count+=segments;
}
'''
code = common
if 'static void rtl_fttr_rx_purge(' in text:
    code += function('rtl_fttr_gro_business') + function('rtl_fttr_rx_purge')
else:
    code += 'static void rtl_fttr_rx_purge(struct rtl_xram *p){(void)p;synchronize_net();}\n'
code += r'''
int main(void){
 for(unsigned users=0;users<=2;users+=2)for(unsigned custom=0;custom<2;custom++){
  memset(&shared,0,sizeof(shared));memset(&fttr,0,sizeof(fttr));
  memset(freed,0,sizeof(freed));memset(delivered,0,sizeof(delivered));
  disable_calls=enable_calls=schedule_calls=sync_calls=resets=0;
  shared.fttr=&fttr;shared.engine_users=users;shared.fttr_omci_type=custom?0x9999:0x8888;
  napi_enabled=!!users;locked=true;bh=poll_tail=false;
  for(unsigned i=0;i<GRO_HASH_BUCKETS;i++)init(&shared.napi.gro.hash[i].list);
  init(&shared.napi.gro.rx_list);
  for(unsigned i=0;i<GRO_HASH_BUCKETS;i++){
   held(i,10+i,&fttr,i&1?0x8100:0x0800,false);
   if(!(i&1))held(i,30+i,&lan,0x8100,false);
   shared.qm.fttr_rx[i].skb=packet(80+i,&fttr,0x8100,false);
  }
  held(0,50,&fttr,shared.fttr_omci_type,false);
  held(0,51,&fttr,shared.fttr_omci_type,true);
  normal(60,&fttr,0x8100,false,4);
  normal(61,&lan,0x0800,false,7);
  normal(62,&fttr,shared.fttr_omci_type,false,1);
  normal(63,&fttr,shared.fttr_omci_type,true,1);
  normal(64,&fttr,0x88a8,false,3);
  rtl_fttr_rx_purge(&shared);
  for(unsigned i=0;i<GRO_HASH_BUCKETS;i++){
   assert(freed[10+i]==1 && freed[80+i]==1 && !shared.qm.fttr_rx[i].skb);
   assert(shared.napi.gro.hash[i].count==(i==0?2:i&1?0:1));
   if(!(i&1))assert(!freed[30+i] && !delivered[30+i]);
  }
  assert(shared.napi.gro.bitmask==0x55 && !freed[50] && !delivered[50]);
  assert(freed[51] && freed[60] && freed[63] && freed[64] && delivered[61] && delivered[62]);
  assert(shared.napi.gro.rx_count==0 && shared.napi.gro.rx_list.next==&shared.napi.gro.rx_list);
  assert(fttr.stats.rx_dropped==12 && resets==8 && sync_calls==1);
  assert(disable_calls==!!users && enable_calls==!!users && schedule_calls==!!users && napi_enabled==!!users);
  /* Repeated withdrawal must neither redeliver survivors nor damage buckets. */
  rtl_fttr_rx_purge(&shared);
  assert(fttr.stats.rx_dropped==12 && delivered[61]==1 && delivered[62]==1 && resets==16);
  assert(shared.napi.gro.bitmask==0x55 && !shared.napi.gro.rx_count);
  /* Cleanup the preserved objects under the same NAPI exclusion. */
  napi_enabled=false;bh=true;
  for(unsigned i=0;i<GRO_HASH_BUCKETS;i++){
   struct sk_buff *s,*n;
   list_for_each_entry_safe(s,n,&shared.napi.gro.hash[i].list,list){skb_list_del_init(s);kfree_skb(s);}
  }
  bh=false;
 }
 puts("PASS: held FTTR GRO/batches/fragments retired; management and shared-port GRO survive, poll tail synchronized, stopped engine not enabled");
}
'''
with tempfile.TemporaryDirectory() as directory:
    work = Path(directory)
    (work/'test.c').write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-Wno-unused-but-set-variable', '-fsanitize=address,undefined', str(work/'test.c'), '-o', str(work/'test')], check=True)
    subprocess.run([str(work/'test')], check=True)
