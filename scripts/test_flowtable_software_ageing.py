#!/usr/bin/env python3
"""Run real IPv4/IPv6 software forwarding against bridge ageing and moves.

The existing FDB model supplies devices and time. Packet admission, NAT and
MMIO are modeled; this is not a live network or throughput test.
"""
import argparse
import ast
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--kernel", type=Path, required=True)
parser.add_argument("--before", action="store_true", help="expect the original ageing failure")
args = parser.parse_args()
existing = ast.parse((root / "scripts/test_flowtable_bridge_ageing.py").read_text())
common = next(ast.literal_eval(n.value) for n in existing.body
              if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "common" for t in n.targets))
common = common.replace("unsigned xmit_type;", "unsigned xmit_type,dir,mtu;")
common = common.replace("struct flow_offload {", "struct flow_offload_tuple_rhash {struct flow_offload_tuple tuple;};\nstruct flow_offload {")
common = common.replace("struct {struct flow_offload_tuple tuple;}tuplehash[2]", "struct flow_offload_tuple_rhash tuplehash[2]")


def function(text, name):
    start = re.search(r"^.*\b" + name + r"\([^;]*?\)\s*\{", text, re.M).start()
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + "\n"


core = (args.kernel / "net/netfilter/nf_flow_table_core.c").read_text()
fdb = (args.kernel / "net/bridge/br_fdb.c").read_text()
offload = (args.kernel / "net/netfilter/nf_flow_table_offload.c").read_text()
ip = (args.kernel / "net/netfilter/nf_flow_table_ip.c").read_text()
model = r'''
#define HZ 1
#define likely(x) (x)
#define unlikely(x) (x)
#define nf_flowtable_time_stamp ((u32)jiffies)
#define NF_FLOW_CLOSING 5
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
static bool nf_flowtable_hw_offload(struct nf_flowtable *t){(void)t;return false;}
static void nf_flow_offload_add(struct nf_flowtable *t,struct flow_offload *f){(void)t;(void)f;assert(false);}
struct nf_flowtable_ctx {unsigned offset,hdrsize;};
struct iphdr {u8 ihl,protocol,ttl;};
struct ipv6hdr {u8 nexthdr,hop_limit;};
struct sk_buff {unsigned len,family;struct iphdr ip;struct ipv6hdr ip6;};
static unsigned reject;
static bool nf_flow_exceeds_mtu(struct sk_buff *s,unsigned mtu){return s->len>mtu;}
static void *skb_network_header(struct sk_buff *s){return s->family==4?(void *)&s->ip:(void *)&s->ip6;}
static int nf_flow_state_check(struct flow_offload *f,u8 p,struct sk_buff *s,unsigned off){(void)f;(void)p;(void)s;(void)off;return reject==2;}
static bool nf_flow_dst_check(struct flow_offload_tuple *t){(void)t;return reject!=3;}
static void flow_offload_teardown(struct flow_offload *f){(void)f;}
static int skb_try_make_writable(struct sk_buff *s,unsigned n){(void)s;(void)n;return reject==4;}
static void nf_flow_encap_pop(struct sk_buff *s,struct flow_offload_tuple_rhash *t){(void)s;(void)t;}
static struct iphdr *ip_hdr(struct sk_buff *s){return &s->ip;}
static struct ipv6hdr *ipv6_hdr(struct sk_buff *s){return &s->ip6;}
static void nf_flow_nat_ip(struct flow_offload *f,struct sk_buff *s,unsigned off,unsigned d,struct iphdr *h){(void)f;(void)s;(void)off;(void)d;(void)h;}
static void nf_flow_nat_ipv6(struct flow_offload *f,struct sk_buff *s,unsigned d,struct ipv6hdr *h){(void)f;(void)s;(void)d;(void)h;}
static void ip_decrease_ttl(struct iphdr *h){h->ttl--;}
static void skb_clear_tstamp(struct sk_buff *s){(void)s;}
static void nf_ct_acct_update(struct nf_conn *c,unsigned d,unsigned n){(void)c;(void)d;(void)n;assert(false);}
'''
cases = r'''
int main(void){
 for(unsigned family=4;family<=6;family+=2)for(unsigned dir=0;dir<2;dir++){
  struct nf_conn ct={&net};struct nf_flowtable table={};struct nf_flowtable_ctx ctx={};
  struct flow_offload route={.type=NF_FLOW_OFFLOAD_ROUTE,.ct=&ct,.timeout=31};
  for(unsigned i=0;i<2;i++)route.tuplehash[i].tuple=(struct flow_offload_tuple){.dir=i,.mtu=1500};
  route.tuplehash[!dir].tuple.xmit_type=FLOW_OFFLOAD_XMIT_DIRECT;
  route.tuplehash[!dir].tuple.out.bridge_ifidx=10;
  route.tuplehash[!dir].tuple.out.bridge_port=2;
  route.tuplehash[!dir].tuple.out.bridge_vid=7;
  route.tuplehash[!dir].tuple.out.h_dest[5]=1;
  int (*forward)(struct nf_flowtable_ctx *,struct nf_flowtable *,struct flow_offload_tuple_rhash *,struct sk_buff *)=
   family==4?nf_flow_offload_forward:nf_flow_offload_ipv6_forward;
  struct sk_buff skb={.len=100,.family=family,.ip={.ihl=5,.protocol=6,.ttl=64},.ip6={.nexthdr=6,.hop_limit=64}};
  present=true;entry=(struct net_bridge_fdb_entry){.updated=1,.used=1,.dst=&bridge_port};reject=0;
  unsigned teardown=0;
  for(jiffies=2;jiffies<=360;jiffies++){
   skb.ip.ttl=skb.ip6.hop_limit=64;
   assert(forward(&ctx,&table,&route.tuplehash[dir],&skb)==1);
   assert(route.timeout>jiffies);
   if(has_expired(&bridge,&entry))present=false;
   rcu_read_lock();bool changed=nf_flow_bridge_changed(&route);rcu_read_unlock();
   if(changed){teardown=jiffies;break;}
  }
  printf("IPv%u dir=%u teardown=%u FDB_updated=%lu timeout=%llu\n",family,dir,teardown,entry.updated,(unsigned long long)route.timeout);
#ifdef EXPECT_BEFORE
  assert(teardown==301 && entry.updated==1 && route.timeout>jiffies);
#else
  assert(!teardown && entry.updated==360);
  /* Download alone does not prove activity of this source. Idle still ages. */
  jiffies=500;assert(forward(&ctx,&table,&route.tuplehash[!dir],&skb)==1);
  assert(entry.updated==360);jiffies=660;assert(has_expired(&bridge,&entry));
  /* Do not refresh on MTU, state, route or writable-buffer rejection. */
  for(reject=1;reject<=4;reject++){
   skb.len=reject==1?1501:100;
   assert(forward(&ctx,&table,&route.tuplehash[dir],&skb)==(reject==4?-1:0));
   assert(entry.updated==360);
  }
  reject=0;skb.len=100;jiffies=700;
  entry.dst=&new_port;assert(forward(&ctx,&table,&route.tuplehash[dir],&skb)==1);assert(entry.updated==360);
  entry.dst=&bridge_port;present=false;assert(forward(&ctx,&table,&route.tuplehash[dir],&skb)==1);assert(entry.updated==360);
  present=true;route.tuplehash[!dir].tuple.out.bridge_vid=8;
  assert(forward(&ctx,&table,&route.tuplehash[dir],&skb)==1);assert(entry.updated==360);
  route.tuplehash[!dir].tuple.out.bridge_vid=7;
  struct net other={1};ct.net=&other;
  assert(forward(&ctx,&table,&route.tuplehash[dir],&skb)==1);assert(entry.updated==360);
#endif
 }
}
'''
source = common + model + function(fdb, "hold_time") + function(fdb, "has_expired")
source += function(fdb, "br_fdb_refresh_offload_rcu") + function(offload, "flow_offload_refresh_bridge")
source += function(core, "flow_offload_refresh") + function(core, "nf_flow_bridge_changed")
source += function(ip, "nf_flow_offload_forward") + function(ip, "nf_flow_offload_ipv6_forward") + cases
with tempfile.TemporaryDirectory(prefix="software-fdb-", dir=root / "artifacts") as directory:
    work = Path(directory)
    (work / "test.c").write_text(source)
    command = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
               "-Wno-unused-variable", "-fsanitize=address,undefined", "-g"]
    if args.before:
        command.append("-DEXPECT_BEFORE")
    subprocess.run(command + [str(work / "test.c"), "-o", str(work / "test")], check=True)
    subprocess.run([str(work / "test")], check=True)
