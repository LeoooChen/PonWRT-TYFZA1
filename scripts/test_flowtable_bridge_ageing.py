#!/usr/bin/env python3
"""Exercise routed hardware activity against real bridge FDB ageing and GC."""
from pathlib import Path
import argparse
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--kernel", type=Path, required=True)
parser.add_argument("--before", action="store_true")
args = parser.parse_args()
kernel = args.kernel


def function(source, name):
    start = re.search(r"^.*\b" + re.escape(name) + r"\([^;]*?\)\s*\{", source, re.M).start()
    if source[start:].startswith(name + "("):
        start = source.rfind("\n", 0, start - 1) + 1
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


fdb = (kernel / "net/bridge/br_fdb.c").read_text()
core = (kernel / "net/netfilter/nf_flow_table_core.c").read_text()
offload = (kernel / "net/netfilter/nf_flow_table_offload.c").read_text()
if args.before and "flow_offload_refresh_bridge" in offload:
    patch = root / "port/overlay/target/linux/rtl9617c/patches-6.18/217-flowtable-bridge-ageing.patch"
    with tempfile.TemporaryDirectory(prefix="flowtable-ageing-before-") as directory:
        before = Path(directory)
        for name in re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.M):
            target = before / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(kernel / name, target)
        subprocess.run(["patch", "-R", "-p1", "--fuzz=0", "-i", str(patch)],
                       cwd=before, check=True, stdout=subprocess.DEVNULL)
        fdb = (before / "net/bridge/br_fdb.c").read_text()
        offload = (before / "net/netfilter/nf_flow_table_offload.c").read_text()
fe = (root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe.c").read_text()

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define BIT(n) (1UL<<(n))
#define test_bit(n,p) (!!(*(p)&BIT(n)))
#define time_before_eq(a,b) ((long)((a)-(b))<=0)
#define time_after(a,b) ((long)((b)-(a))<0)
#define FLOW_ACTION_HW_STATS_DELAYED 1
#define EXPORT_SYMBOL_GPL(n)
#define RCU_LOCKDEP_WARN(v,s) assert(!(v))
#define NF_FLOW_OFFLOAD_ROUTE 1
#define NF_FLOW_HW_BIDIRECTIONAL 4
#define NF_FLOWTABLE_COUNTER 1
#define max_t(t,a,b) ((t)(a)>(t)(b)?(t)(a):(t)(b))
#define FLOW_OFFLOAD_DIR_MAX 2
enum flow_offload_tuple_dir {FLOW_OFFLOAD_DIR_ORIGINAL,FLOW_OFFLOAD_DIR_REPLY};
enum {FLOW_OFFLOAD_XMIT_UNSPEC,FLOW_OFFLOAD_XMIT_DIRECT};
enum {BR_FDB_STATIC,BR_FDB_ADDED_BY_EXT_LEARN};
static unsigned long jiffies;
static unsigned rcu_depth;
static void rcu_read_lock(void){rcu_depth++;}
static void rcu_read_unlock(void){assert(rcu_depth);rcu_depth--;}
static bool rcu_read_lock_held(void){return rcu_depth!=0;}
#define ASSERT_RTNL() assert(!"hardware stats must not acquire RTNL")
struct net {unsigned id;};
struct net_device {unsigned ifindex;struct net *net;};
struct net_bridge {bool topology_change;unsigned long forward_delay,ageing_time;};
struct net_bridge_port {struct net_bridge *br;struct net_device *dev;};
struct net_bridge_fdb_entry {unsigned long flags,used,updated;struct net_bridge_port *dst;};
static struct net net;
static struct net_bridge bridge={.ageing_time=300,.forward_delay=15};
static struct net_device brdev={10,&net},lan={2,&net},moved={3,&net};
static struct net_bridge_port bridge_port={&bridge,&lan},new_port={&bridge,&moved};
static struct net_bridge_fdb_entry entry;
static bool present;
static struct net_device *dev_get_by_index_rcu(struct net *n,unsigned index){
 assert(rcu_depth);if(n!=&net)return NULL;
 return index==10?&brdev:index==2?&lan:index==3?&moved:NULL;
}
static struct net_bridge_port *br_port_get_rcu(const struct net_device *dev){
 assert(rcu_depth);return dev==&lan?&bridge_port:dev==&moved?&new_port:NULL;
}
static struct net_bridge_fdb_entry *br_fdb_find_rcu(struct net_bridge *br,const u8 *addr,u16 vid){
 assert(rcu_depth);return br==&bridge&&present&&addr[5]==1&&vid==7?&entry:NULL;
}
static struct net_device *br_fdb_find_port_rcu(const struct net_device *dev,const u8 *addr,u16 vid){
 struct net_bridge_fdb_entry *e=br_fdb_find_rcu(&bridge,addr,vid);
 return dev==&brdev&&e?e->dst->dev:NULL;
}
struct nf_conn {struct net *net;};
struct flow_offload_tuple {unsigned xmit_type;struct {
 unsigned bridge_ifidx,bridge_port;u16 bridge_vid;u8 h_dest[6];
} out;};
struct flow_offload {unsigned type;unsigned long flags;u64 timeout;struct nf_conn *ct;struct {struct flow_offload_tuple tuple;}tuplehash[2];};
static struct net *nf_ct_net(struct nf_conn *ct){return ct->net;}
struct nf_flowtable {unsigned flags;};
struct flow_offload_work {struct flow_offload *flow;struct nf_flowtable *flowtable;};
struct flow_stats {u64 lastused,bytes,pkts;};
static u64 samples[2];
static void flow_offload_tuple_stats(struct flow_offload_work *work,unsigned dir,struct flow_stats *stats){
 (void)work;stats->lastused=samples[dir];
}
static unsigned flow_offload_get_timeout(struct flow_offload *flow){(void)flow;return 30;}
static void nf_ct_acct_add(struct nf_conn *ct,unsigned dir,u64 packets,u64 bytes){
 (void)ct;(void)dir;(void)packets;(void)bytes;assert(false);
}
struct rtl_fe {unsigned age;};
struct rtl_fe_flow {bool published,counted;unsigned counter,hash_index,ethernet_bytes;u64 bytes;u32 packets;unsigned long lastused;};
struct flow_cls_offload {struct {u64 bytes,packets;unsigned long lastused;}stats;};
struct rtl_dq_io {struct rtl_fe *fe;};
static struct rtl_dq_io fe_io(struct rtl_fe *fe){return (struct rtl_dq_io){fe};}
static int rtl_fe_main_age_read(struct rtl_dq_io *io,unsigned index,unsigned *age){(void)index;*age=io->fe->age;return 0;}
static int rtl_fe_main_age(struct rtl_dq_io *io,unsigned index,unsigned age){(void)index;io->fe->age=age;return 0;}
static int rtl_fe_tx_counter_read(struct rtl_dq_io *io,unsigned counter,unsigned *words){(void)io;(void)counter;(void)words;assert(false);return 0;}
#define flow_stats_update(s,b,p,d,u,h) do{(s)->bytes+=(b);(s)->packets+=(p);(s)->lastused=(u);}while(0)
'''
parts = [common, function(fdb, "hold_time"), function(fdb, "has_expired")]
if not args.before:
    parts += [function(fdb, "br_fdb_refresh_offload_rcu"),
              function(offload, "flow_offload_refresh_bridge")]
parts += [function(offload, "flow_offload_work_stats"),
          function(core, "nf_flow_bridge_changed"), function(fe, "fe_stats")]
parts.append(r'''
static void activity(struct flow_offload *flow,unsigned dir,unsigned long when){
 struct nf_flowtable table={};
 struct flow_offload_work work={flow,&table};
 samples[dir]=when;samples[!dir]=0;
 flow->flags|=BIT(NF_FLOW_HW_BIDIRECTIONAL);
 flow_offload_work_stats(&work);
}
int main(void){
 struct nf_conn ct={&net};
 struct flow_offload route={.type=NF_FLOW_OFFLOAD_ROUTE,.ct=&ct};
 route.tuplehash[1].tuple=(struct flow_offload_tuple){.xmit_type=FLOW_OFFLOAD_XMIT_DIRECT,
  .out={.bridge_ifidx=10,.bridge_port=2,.bridge_vid=7,.h_dest={0,0,0,0,0,1}}};
 struct rtl_fe hardware={.age=1};
 struct rtl_fe_flow flow={.published=true,.counted=false,.lastused=1};
 struct flow_cls_offload cls={};
 present=true;entry=(struct net_bridge_fdb_entry){.updated=1,.used=1,.dst=&bridge_port};
 unsigned teardown_at=0;
 for(jiffies=2;jiffies<=360;jiffies++){
  hardware.age=6;assert(!fe_stats(&hardware,&flow,&cls));
  assert(cls.stats.lastused==jiffies); /* Actual FE reports active hardware traffic. */
  activity(&route,FLOW_OFFLOAD_DIR_ORIGINAL,cls.stats.lastused);
  assert(route.timeout>jiffies); /* Connection is active when the FDB check fails. */
  if(has_expired(&bridge,&entry))present=false; /* Same expiry predicate as bridge GC. */
  rcu_read_lock();bool changed=nf_flow_bridge_changed(&route);rcu_read_unlock();
  if(changed){teardown_at=jiffies;break;}
 }
 printf("active_route_teardown_at=%u FDB_updated=%lu FE_lastused=%lu\n",
  teardown_at,entry.updated,cls.stats.lastused);
#ifdef EXPECT_BEFORE
 assert(teardown_at==301&&cls.stats.lastused==301);
 return 0;
#else
 assert(!teardown_at&&entry.updated==360);
 /* Delayed/idle hardware stats never extend the FDB beyond the actual hit. */
 jiffies=500;activity(&route,FLOW_OFFLOAD_DIR_ORIGINAL,360);
 assert(entry.updated==360);
 jiffies=660;assert(has_expired(&bridge,&entry));
 /* Download traffic is no proof that the LAN source is still sending. */
 jiffies=700;activity(&route,FLOW_OFFLOAD_DIR_REPLY,700);assert(entry.updated==360);
 /* Never revive an entry that has moved to another port or disappeared. */
 present=true;entry.dst=&new_port;activity(&route,0,700);assert(entry.updated==360);
 entry.dst=&bridge_port;present=false;activity(&route,0,700);assert(entry.updated==360);
 present=true;
 /* Respect the captured VLAN, namespace and bridge device. */
 route.tuplehash[1].tuple.out.bridge_vid=8;activity(&route,0,700);assert(entry.updated==360);
 route.tuplehash[1].tuple.out.bridge_vid=7;
 struct net other={1};ct.net=&other;activity(&route,0,700);assert(entry.updated==360);ct.net=&net;
 route.tuplehash[1].tuple.out.bridge_ifidx=99;activity(&route,0,700);assert(entry.updated==360);
 puts("Routed FDB: active retained; idle, delayed stats, one-way download, move, missing entry, VLAN and namespace isolation passed.");
 return 0;
#endif
}
''')
with tempfile.TemporaryDirectory(prefix="flowtable-ageing-", dir=root / "artifacts") as directory:
    work = Path(directory)
    (work / "test.c").write_text("\n".join(parts))
    command = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
               "-Wno-unused-variable", "-fsanitize=address,undefined", "-g"]
    if args.before:
        command.append("-DEXPECT_BEFORE")
    subprocess.run(command + [str(work / "test.c"), "-o", str(work / "test")], check=True)
    subprocess.run([str(work / "test")], check=True)
