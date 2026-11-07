#!/usr/bin/env python3
"""Execute native FTTR generations and actual Linux queue/reinject/copy boundaries."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--kernel', type=Path, required=True)
a = p.parse_args()

def function(text, name):
    start = re.search(r'^(?:static )?(?:inline )?[^\n]*\b' + name + r'\([^;]*?\)\s*\{', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'

base = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18'
core = base.joinpath('net/core/rtl9617c-fttr.c').read_text()
header = base.joinpath('include/linux/rtl9617c-fttr.h').read_text()
dev = a.kernel.joinpath('net/core/dev.c').read_text()
queue = a.kernel.joinpath('net/netfilter/nfnetlink_queue.c').read_text()
code = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>
typedef uint64_t u64;typedef uint8_t u8;typedef uint16_t u16;typedef uint16_t __be16;typedef uint32_t u32;
#define U64_MAX UINT64_MAX
#define READ_ONCE(v) (v)
#define unlikely(v) (v)
#define EXPORT_SYMBOL_GPL(v)
#define ASSERT_RTNL() assert(rtnl)
#define ATOMIC64_INIT(v) (v)
typedef u64 atomic64_t;
static unsigned rtnl;
static u64 atomic64_read_acquire(atomic64_t *p){return *p;}
static void atomic64_set_release(atomic64_t *p,u64 v){*p=v;}
struct net_device {unsigned rtl9617c_fttr_egress,rtl9617c_fttr_omci,dropped,hard_header_len;bool vlan;struct net_device *parent;};
struct list_head {struct list_head *next;};
struct rb_node {struct rb_node *rb_left,*rb_right;};struct rb_root {struct rb_node *rb_node;};
struct ethhdr {u8 dst[6],src[6];__be16 h_proto;};
struct sk_buff {u8 *data;bool mac_set;struct list_head list;struct rb_node rbnode;struct sk_buff *next;u64 rtl9617c_fttr_generation;bool rtl9617c_fttr_ingress,consumed,vlan,busy;
 unsigned protocol,pkt_type,priority,skb_iif,mark,hash,vlan_all,len;bool slow_gro;
 struct {int same_flow;} gro;struct {int ip_defrag_offset,frag_run_len;} frag;
 void *sk;struct net_device *dev;};
static bool skb_vlan_tag_present(const struct sk_buff *s){return s->vlan;}
static bool is_vlan_dev(struct net_device *d){return d->vlan;}
static struct net_device *vlan_dev_real_dev(struct net_device *d){return d->parent;}
static bool eth_type_vlan(unsigned p){return p==htons(0x8100)||p==htons(0x88a8);}
static bool skb_mac_header_was_set(const struct sk_buff *s){return s->mac_set;}
static const void *skb_mac_header(const struct sk_buff *s){return s->mac_set ? s->data : (const u8 *)"0123456789abcd";}
static const void *skb_header_pointer(const struct sk_buff *s,int o,unsigned len,void *b){(void)b;return o+len<=s->len?s->data+o:NULL;}
u64 rtl9617c_fttr_generation_read(void);
'''
code += function(header, 'rtl9617c_fttr_stale') + function(header, 'rtl9617c_fttr_differs') + function(header, 'rtl9617c_fttr_copy')
code += re.sub(r'^#include .*\n', '', core, flags=re.M)
code += r'''
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define SKB_DROP_REASON_DEV_READY 1
#define SKB_DROP_REASON_NETFILTER_DROP 2
struct netdev_queue {bool stopped;};
static unsigned sent,freed;
static void skb_mark_not_on_list(struct sk_buff *s){s->next=NULL;}
static void dev_core_stats_tx_dropped_inc(struct net_device *d){d->dropped++;}
static void kfree_skb_reason(struct sk_buff *s,int reason){(void)reason;assert(!s->consumed);s->consumed=true;freed++;}
static void kfree_skb(struct sk_buff *s){kfree_skb_reason(s,0);}
static int xmit_one(struct sk_buff *s,struct net_device *d,struct netdev_queue *q,bool more){
 (void)d;(void)q;(void)more;if(s->busy)return NETDEV_TX_BUSY;assert(!s->consumed);s->consumed=true;sent++;return 0;}
static bool dev_xmit_complete(int rc){return rc==0;}
static bool netif_tx_queue_stopped(struct netdev_queue *q){return q->stopped;}
'''
code += function(dev, 'dev_hard_start_xmit')
code += r'''
#define NF_DROP 0
#define NF_ACCEPT 1
#define NF_STOLEN 2
#define NF_QUEUE 3
#define NF_REPEAT 4
#define NF_STOP 5
#define NF_VERDICT_MASK 0xff
#define NF_VERDICT_QBITS 16
#define NF_VERDICT_FLAG_QUEUE_BYPASS 0x8000
struct net {int unused;};struct sock {int unused;};
struct nf_hook_state {struct net *net;struct sock *sk;unsigned pf,hook;struct net_device *out;
 int (*okfn)(struct net *,struct sock *,struct sk_buff *);};
struct nf_hook_entry {unsigned unused;};
struct nf_hook_entries {unsigned num_hook_entries;struct nf_hook_entry hooks[1];};
struct nf_queue_entry {struct sk_buff *skb;struct nf_hook_state state;unsigned hook_index;bool released;};
static struct nf_hook_entries hooks={.num_hook_entries=1};
static unsigned delivered,repeated,queued;
static struct nf_hook_entries *nf_hook_entries_head(const struct net *n,unsigned pf,unsigned hook){(void)n;(void)pf;(void)hook;return &hooks;}
static void nf_queue_entry_free(struct nf_queue_entry *e){assert(!e->released);e->released=true;}
static unsigned nf_hook_entry_hookfn(const struct nf_hook_entry *h,struct sk_buff *s,struct nf_hook_state *st){(void)h;(void)s;(void)st;repeated++;return NF_ACCEPT;}
static int nf_reroute(struct sk_buff *s,struct nf_queue_entry *e){(void)s;(void)e;return 0;}
static unsigned nf_iterate(struct sk_buff *s,struct nf_hook_state *st,const struct nf_hook_entries *h,unsigned *i){(void)s;(void)st;(void)h;(void)i;return NF_ACCEPT;}
static void local_bh_disable(void){}
static void local_bh_enable(void){}
static int __nf_queue(struct sk_buff *s,struct nf_hook_state *st,unsigned i,unsigned verdict){(void)s;(void)st;(void)i;(void)verdict;queued++;return 0;}
static int okfn(struct net *n,struct sock *sk,struct sk_buff *s){(void)n;(void)sk;assert(!s->consumed);s->consumed=true;delivered++;return 0;}
'''
code += function(a.kernel.joinpath('net/netfilter/nf_queue.c').read_text(), 'nf_queue')
code += function(queue, 'nf_reinject')
code += r'''
#define skb_dst_drop(s) ((void)(s))
#define skb_dst_copy(a,b) ((void)(a),(void)(b))
#define skb_dst_set(a,b) ((void)(a),(void)(b))
#define skb_dst(s) (s)
#define dst_clone(s) (s)
#define skb_copy_hash(a,b) ((void)(a),(void)(b))
#define nf_copy(a,b) ((void)(a),(void)(b))
#define skb_ext_copy(a,b) ((void)(a),(void)(b))
#define skb_copy_secmark(a,b) ((void)(a),(void)(b))
#define IS_ENABLED(v) 0
'''
code += function(a.kernel.joinpath('net/ipv4/ip_output.c').read_text(), 'ip_copy_metadata')
code += function(a.kernel.joinpath('net/ipv6/ip6_output.c').read_text(), 'ip6_copy_metadata')
code += r'''
struct ieee80211_hw {int unused;};struct ieee80211_tx_control {int unused;};
struct ieee80211_ops {void (*tx)(struct ieee80211_hw *,struct ieee80211_tx_control *,struct sk_buff *);};
struct ieee80211_local {struct ieee80211_hw hw;struct ieee80211_ops *ops;};
static void ieee80211_free_txskb(struct ieee80211_hw *h,struct sk_buff *s){(void)h;kfree_skb(s);}
static void radio_tx(struct ieee80211_hw *h,struct ieee80211_tx_control *c,struct sk_buff *s){(void)h;(void)c;sent++;s->consumed=true;}
'''
code += function(a.kernel.joinpath('net/mac80211/driver-ops.h').read_text(), 'drv_tx')
code += r'''
#define ETH_HLEN 14
#define container_of(p,t,m) ((t *)((char *)(p)-__builtin_offsetof(t,m)))
#define list_for_each_entry(p,h,m) for(struct list_head *n=(h)->next;n!=(h)&&((p)=container_of(n,struct sk_buff,m),1);n=n->next)
#define NAPI_GRO_CB(s) (&(s)->gro)
static unsigned skb_get_hash_raw(const struct sk_buff *s){return s->hash;}
static bool skb_metadata_differs(const struct sk_buff *a,const struct sk_buff *b){(void)a;(void)b;return false;}
static unsigned compare_ether_header(const void *a,const void *b){return memcmp(a,b,14);}
static unsigned skb_metadata_dst_cmp(const struct sk_buff *a,const struct sk_buff *b){(void)a;(void)b;return 0;}
static unsigned skb_get_nfct(const struct sk_buff *a){(void)a;return 0;}
static unsigned gro_list_prepare_tc_ext(const struct sk_buff *a,const struct sk_buff *b,unsigned d){(void)a;(void)b;(void)d;return 0;}
static unsigned __psp_skb_coalesce_diff(const struct sk_buff *a,const struct sk_buff *b,unsigned d){(void)a;(void)b;(void)d;return 0;}
#define FRAG_CB(s) (&(s)->frag)
#define IPFRAG_OK 0
#define IPFRAG_DUP 1
#define IPFRAG_OVERLAP 2
struct inet_frag_queue {struct sk_buff *fragments_tail;struct rb_root rb_fragments;};
static void skb_gso_reset(struct sk_buff *s){(void)s;}
static void fragrun_create(struct inet_frag_queue *q,struct sk_buff *s){q->fragments_tail=s;}
static void fragrun_append_to_last(struct inet_frag_queue *q,struct sk_buff *s){q->fragments_tail=s;}
static struct sk_buff *rb_to_skb(struct rb_node *p){return container_of(p,struct sk_buff,rbnode);}
static void fragcb_clear(struct sk_buff *s){memset(&s->frag,0,sizeof(s->frag));}
static void rb_link_node(struct rb_node *n,struct rb_node *p,struct rb_node **at){(void)p;*at=n;}
static void rb_insert_color(struct rb_node *n,struct rb_root *r){(void)n;(void)r;}
static void nf_reset_ct(struct sk_buff *s){(void)s;}
'''
code += function(a.kernel.joinpath('net/core/gro.c').read_text(), 'gro_list_prepare')
code += function(a.kernel.joinpath('net/ipv4/inet_fragment.c').read_text(), 'inet_frag_queue_insert')
code += r'''
int main(void){
 struct net_device lan={},p6={.rtl9617c_fttr_egress=1,.rtl9617c_fttr_omci=0x8888},mc={.rtl9617c_fttr_egress=2};
 struct net_device gem={.vlan=true,.parent=&p6},customer={.vlan=true,.parent=&gem};
 struct sk_buff management={.protocol=htons(0x8888)},normal={.protocol=htons(0x800)},old={.protocol=htons(0x800)};
 assert(!rtl9617c_fttr_stale(&normal));
 assert(rtl9617c_fttr_enqueue(&management,&p6)&&!management.rtl9617c_fttr_generation);
 rtnl=1;assert(!rtl9617c_fttr_generation_set(true));rtnl=0;
 assert(rtl9617c_fttr_enqueue(&old,&customer)&&old.rtl9617c_fttr_generation==2);
 struct ethhdr wire={.h_proto=htons(0x8100)};struct sk_buff raw={.data=(void *)&wire,.mac_set=true,.len=14,.protocol=htons(3)};
 assert(rtl9617c_fttr_enqueue(&raw,&p6)&&raw.rtl9617c_fttr_generation==2);
 raw.rtl9617c_fttr_generation=0;wire.h_proto=htons(0x8888);
 assert(rtl9617c_fttr_enqueue(&raw,&p6)&&!raw.rtl9617c_fttr_generation);
 raw.len=13;assert(!rtl9617c_fttr_enqueue(&raw,&p6));
 old.rtl9617c_fttr_ingress=true;
 struct sk_buff v4={},v6={};ip_copy_metadata(&v4,&old);ip6_copy_metadata(&v6,&old);
 assert(v4.rtl9617c_fttr_generation==2&&v6.rtl9617c_fttr_generation==2&&v4.rtl9617c_fttr_ingress&&v6.rtl9617c_fttr_ingress);
 /* Actual Linux hard-start returns a BUSY skb to qdisc. */
 struct netdev_queue q={};int rc;old.busy=true;
 assert(dev_hard_start_xmit(&old,&p6,&q,&rc)==&old&&rc==NETDEV_TX_BUSY&&!old.consumed);
 struct nf_hook_state state={.out=&gem,.okfn=okfn};struct sk_buff held={.protocol=htons(0x800)};
 assert(!nf_queue(&held,&state,0,NF_QUEUE)&&queued==1&&held.rtl9617c_fttr_generation==2);
 rtnl=1;assert(!rtl9617c_fttr_generation_set(false));u64 closed=rtl9617c_fttr_generation_read();
 assert(!rtl9617c_fttr_generation_set(false)&&rtl9617c_fttr_generation_read()==closed);
 assert(!rtl9617c_fttr_generation_set(true));rtnl=0;
 assert(rtl9617c_fttr_stale(&old)&&rtl9617c_fttr_stale(&v4)&&rtl9617c_fttr_stale(&v6));
 /* Re-enqueue cannot relabel an old packet into the new generation. */
 assert(!rtl9617c_fttr_enqueue(&held,&mc)&&held.rtl9617c_fttr_generation==2);
 struct sk_buff current={.protocol=htons(0x8100)};
 assert(rtl9617c_fttr_enqueue(&current,&p6));assert(!rtl9617c_fttr_differs(&v4,&v6)&&rtl9617c_fttr_differs(&v4,&current));
 /* Actual GRO discriminator and shared IPv4/IPv6 fragment insertion. */
 struct list_head list;struct sk_buff aggregate=v4,incoming=v6;
 aggregate.list.next=&list;list.next=&aggregate.list;
 aggregate.dev=incoming.dev=&lan;gro_list_prepare(&list,&incoming);assert(aggregate.gro.same_flow);
 incoming.rtl9617c_fttr_generation=current.rtl9617c_fttr_generation;
 gro_list_prepare(&list,&incoming);assert(!aggregate.gro.same_flow);
 incoming.rtl9617c_fttr_generation=0;gro_list_prepare(&list,&incoming);assert(!aggregate.gro.same_flow);
 struct inet_frag_queue fragments={};struct sk_buff first=current,second=current;
 first.len=8;second.len=8;
 assert(inet_frag_queue_insert(&fragments,&first,0,8)==IPFRAG_OK);
 assert(inet_frag_queue_insert(&fragments,&second,8,16)==IPFRAG_OK);
 rtnl=1;assert(!rtl9617c_fttr_generation_set(false));assert(!rtl9617c_fttr_generation_set(true));rtnl=0;
 struct sk_buff newer={.len=8};assert(rtl9617c_fttr_stamp(&newer));
 assert(inet_frag_queue_insert(&fragments,&newer,16,24)==IPFRAG_OVERLAP);
 fragments=(struct inet_frag_queue){};assert(inet_frag_queue_insert(&fragments,&second,0,8)==IPFRAG_OVERLAP);
 /* Retain one valid item after testing a second revocation. */
 current.rtl9617c_fttr_generation=0;assert(rtl9617c_fttr_stamp(&current));
 old.next=&normal;normal.next=&current;
 assert(!dev_hard_start_xmit(&old,&lan,&q,&rc)&&rc==0&&old.consumed&&sent==2&&lan.dropped==1);
 struct nf_queue_entry entry={.skb=&held,.state=state};nf_reinject(&entry,NF_REPEAT);
 assert(entry.released&&held.consumed&&!delivered&&!repeated);
 struct sk_buff good={};entry=(struct nf_queue_entry){.skb=&good,.state=state};nf_reinject(&entry,NF_ACCEPT);
 assert(delivered==1&&good.consumed);
 struct ieee80211_ops ops={.tx=radio_tx};struct ieee80211_local radio={.ops=&ops};
 drv_tx(&radio,NULL,&v4);assert(v4.consumed&&sent==2);
 struct sk_buff fresh={};assert(rtl9617c_fttr_stamp(&fresh));drv_tx(&radio,NULL,&fresh);assert(sent==3);
 rtnl=1;assert(!rtl9617c_fttr_generation_set(false));rtnl=0;
 struct sk_buff rejected={};assert(!nf_queue(&rejected,&state,0,NF_QUEUE|NF_VERDICT_FLAG_QUEUE_BYPASS));assert(rejected.consumed&&queued==1);
 struct sk_buff unrelated={};assert(rtl9617c_fttr_enqueue(&unrelated,&lan)&&!unrelated.rtl9617c_fttr_generation);
 assert(rtl9617c_fttr_enqueue(&management,&p6));
 rtnl=1;rtl_fttr_generation=UINT64_MAX;assert(rtl9617c_fttr_generation_set(true)==-EOVERFLOW);
 assert(!rtl9617c_fttr_generation_set(false)&&rtl_fttr_generation==UINT64_MAX);
 puts("PASS: native generation, actual BUSY/qdisc retry, NFQUEUE enqueue/reinject, IPv4/IPv6 metadata and mac80211 final TX; old identities never reopen");
}
'''
# Verify the hooks exercised above reside at the actual queue boundaries, and
# that copy/aggregation code cannot strip or mix those identities. Full kernel
# compilation covers the surrounding kernel types and configuration.
skb = a.kernel.joinpath('include/linux/skbuff.h').read_text()
assert skb.index('struct_group(headers,') < skb.index('u64\t\t\trtl9617c_fttr_generation;') < skb.index('); /* end headers group */')
copy = function(a.kernel.joinpath('net/core/skbuff.c').read_text(), '__copy_skb_header')
assert 'memcpy(&new->headers, &old->headers, sizeof(new->headers))' in copy and 'CHECK_SKB_FIELD(rtl9617c_fttr_generation)' in copy
rx = function(dev, '__netif_receive_skb_core')
assert rx.index('another_round:') < rx.index('rtl9617c_fttr_stale(skb)') < rx.index('skb->skb_iif =')
for path,name,call in (
 ('net/core/gro.c','gro_list_prepare','rtl9617c_fttr_differs(p, skb)'),
 ('net/core/skbuff.c','skb_try_coalesce','rtl9617c_fttr_differs(to, from)'),
 ('net/ipv4/inet_fragment.c','inet_frag_queue_insert','rtl9617c_fttr_differs(last, skb)'),
 ('net/mac80211/tx.c','ieee80211_amsdu_aggregate','rtl9617c_fttr_differs(head, skb)'),
 ('net/mac80211/tx.c','ieee80211_fragment','rtl9617c_fttr_copy(tmp, skb)'),
 ('net/ipv4/ip_output.c','ip_finish_output2','rtl9617c_fttr_enqueue(skb, dev)'),
 ('net/ipv6/ip6_output.c','ip6_finish_output2','rtl9617c_fttr_enqueue(skb, dev)')):
    assert call in function(a.kernel.joinpath(path).read_text(),name),(path,name)
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);tmp.joinpath('test.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unused-function','-Wno-sign-compare',
        '-fsanitize=address,undefined',str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
