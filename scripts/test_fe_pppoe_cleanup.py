#!/usr/bin/env python3
"""Reproduce DIRECT union misuse and exercise PPP/physical flow cleanup."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
kernel = Path(subprocess.check_output([
    'python3', str(root / 'port/tools/project_paths.py'),
    '--kernel-directory', str(root / 'sources/openwrt')], text=True).strip())
source = (kernel / 'net/netfilter/nf_flow_table_core.c').read_text()
start = source.index('static bool nf_flow_tuple_uses_dev(')
end = source.index('\nvoid nf_flow_table_gc_cleanup(', start)
current = source[start:end]
notifier_start = source.index('static int nf_flow_table_netdev_event(')
notifier_end = source.index('\nstatic struct notifier_block flow_offload_netdev_notifier', notifier_start)
notifier = source[notifier_start:notifier_end]
old = current[current.index('static void nf_flow_table_do_cleanup('):]
for direction in (0, 1):
    old = old.replace(f'nf_flow_tuple_uses_dev(&flow->tuplehash[{direction}].tuple, dev)',
                      f'flow->tuplehash[{direction}].tuple.dst_cache->dev == dev')
assert old != current

common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
struct net_device {int ifindex,net,carrier;};
struct dst_entry {struct net_device *dev;};
struct nf_flowtable {};
enum { FLOW_OFFLOAD_XMIT_UNSPEC, FLOW_OFFLOAD_XMIT_NEIGH,
 FLOW_OFFLOAD_XMIT_XFRM, FLOW_OFFLOAD_XMIT_DIRECT };
struct flow_offload_tuple {
 int iifidx,xmit_type;
 union {struct dst_entry *dst_cache;struct {int ifidx,hw_ifidx;} out;};
};
struct flow_offload {
 struct {struct flow_offload_tuple tuple;} tuplehash[2];
 int type,ct;bool torn_down;
};
#define NF_FLOW_OFFLOAD_ROUTE 1
static int nf_ct_net(int ct){return ct;}
static int dev_net(struct net_device *dev){return dev->net;}
static bool net_eq(int a,int b){return a==b;}
static void flow_offload_teardown(struct flow_offload *flow){flow->torn_down=true;}
struct notifier_block {};
enum {NETDEV_DOWN,NETDEV_CHANGEMTU,NETDEV_CHANGE,NETDEV_UP,NETDEV_CHANGEADDR};
#define NOTIFY_DONE 0
static unsigned int cleanups;
static struct net_device *netdev_notifier_info_to_dev(void *ptr){return ptr;}
static bool netif_carrier_ok(struct net_device *dev){return dev->carrier;}
static void nf_flow_table_cleanup(struct net_device *dev){(void)dev;cleanups++;}
'''
test = r'''
int main(void) {
 struct net_device pon={4,1,1},lan={2,1,1},ppp={6,1,1},other_ppp={7,1,1};
 struct dst_entry ppp_dst={&ppp},lan_dst={&lan};
 struct flow_offload flow={.type=NF_FLOW_OFFLOAD_ROUTE,.ct=1,
  .tuplehash={{{.iifidx=2,.xmit_type=FLOW_OFFLOAD_XMIT_DIRECT,.out={7,7}}},
              {{.iifidx=4,.xmit_type=FLOW_OFFLOAD_XMIT_NEIGH,.dst_cache=&lan_dst}}}};
 nf_flow_table_do_cleanup(NULL,&flow,&ppp);
 assert(!flow.torn_down);
 nf_flow_table_do_cleanup(NULL,&flow,&other_ppp);
 assert(flow.torn_down);
 flow.torn_down=false;
 flow.tuplehash[0].tuple.xmit_type=FLOW_OFFLOAD_XMIT_NEIGH;
 flow.tuplehash[0].tuple.dst_cache=&ppp_dst;
 nf_flow_table_do_cleanup(NULL,&flow,&other_ppp);
 assert(!flow.torn_down);
 nf_flow_table_do_cleanup(NULL,&flow,&ppp);
 assert(flow.torn_down);
 flow.torn_down=false;
 nf_flow_table_do_cleanup(NULL,&flow,&pon);
 assert(flow.torn_down);
 flow.torn_down=false;flow.ct=2;
 nf_flow_table_do_cleanup(NULL,&flow,&ppp);
 assert(!flow.torn_down);
 flow.type=2;flow.ct=1;flow.tuplehash[0].tuple.dst_cache=NULL;
 flow.tuplehash[1].tuple.dst_cache=NULL;
 nf_flow_table_do_cleanup(NULL,&flow,&ppp);
 assert(!flow.torn_down);
 for(unsigned int event=0;event<=NETDEV_CHANGEADDR;event++) {
  unsigned int before=cleanups;
  nf_flow_table_netdev_event(NULL,event,&pon);
  assert(cleanups-before==(event==NETDEV_DOWN || event==NETDEV_CHANGEMTU));
 }
 pon.carrier=0;
 nf_flow_table_netdev_event(NULL,NETDEV_CHANGE,&pon);
 assert(cleanups==3);
 pon.carrier=1;
 nf_flow_table_netdev_event(NULL,NETDEV_CHANGE,&pon);
 assert(cleanups==3);
 puts("DIRECT/PPP flow cleanup and PON carrier-loss teardown passed");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='pppoe-cleanup-') as work:
    for fixed, code in ((0, old), (1, current)):
        path = Path(work) / 'cleanup.c'
        binary = Path(work) / 'cleanup'
        path.write_text(common + code + notifier + test)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-fsanitize=address,undefined', str(path), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if fixed:
            assert result.returncode == 0, result.stderr
            print(result.stdout, end='')
        else:
            assert result.returncode != 0
            print('Original union-pointer crash reproduced')
