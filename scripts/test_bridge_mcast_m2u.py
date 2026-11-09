#!/usr/bin/env python3
"""Execute Linux MC-to-UC selection through native NI copies and direct TX."""
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
setup=(root/'scripts/test_bridge_mcast_flood.py').read_text().split('# Reuse the original known-good',1)[0]
scope={'__file__':str(root/'scripts/test_bridge_mcast_flood.py')}
exec(compile(setup,'flood-selection-fixture','exec'),scope)
code=scope['code'];function=scope['function'];source=scope['source'];learning=scope['learning']
code+=r'''
#define h_source source
#define ETH_ALEN 6
#define BR_MULTICAST_TO_UNICAST 128
#define MCAST_INCLUDE 1
#define MDB_PG_FLAGS_BLOCKED 1
struct hlist_node {struct hlist_node *next;struct net_bridge_port *port;};
struct net_bridge_port_group {struct {struct net_bridge_port *port;} key;struct net_bridge_port_group *next;unsigned filter_mode,flags;u8 eth_addr[6];};
struct br_ip {unsigned proto;bool star;};
struct net_bridge_mdb_entry {struct net_bridge_port_group *ports;struct br_ip addr;};
struct net_bridge_mcast {struct hlist_node *routers;bool handle_mode;};
static struct hlist_node *br_multicast_get_first_rport_node(struct net_bridge_mcast *b,struct sk_buff *s){(void)s;return b->routers;}
static struct net_bridge_port *br_multicast_rport_from_node_skb(struct hlist_node *n,struct sk_buff *s){(void)s;return n?n->port:NULL;}
static struct hlist_node *hlist_next_rcu(struct hlist_node *n){return n->next;}
static bool br_multicast_should_handle_mode(struct net_bridge_mcast *b,unsigned p){(void)p;return b->handle_mode;}
static bool br_multicast_is_star_g(struct br_ip *ip){return ip->star;}
static unsigned copies,copy_failure;
static struct sk_buff *pskb_copy(struct sk_buff *s,int f){(void)f;if(++copies==copy_failure)return NULL;struct sk_buff *n=malloc(sizeof(*n));assert(n);*n=*s;n->cloned=true;return n;}
'''
for name in ('maybe_deliver_addr','br_multicast_flood'):code+=function(source,name)
initial=learning.split("code+=r'''\nint main(void){",1)[1].split(' struct nf_hook_entries ours=',1)[0]
code+='int main(void){'+initial+r'''
 owner=&bridge;fdb_source=&lan;
 struct wireless_dev ap={.iftype=NL80211_IFTYPE_AP};
 struct net_device wifi={.ifindex=12,.mtu=1500,.running=true,.carrier=true,.master=&br,.net=&net,.ieee80211_ptr=&ap};
 struct rtl_fe_port wp={.fe=&fe,.netdev=&wifi,.source=18,.destination=19,.wifi_id=2,.llid=2};devices[12]=&wifi;fe.ports[3]=&wp;
 struct net_bridge linux_bridge={.dev=&br};struct net_bridge_port ports[2];
 for(unsigned i=0;i<2;i++)ports[i]=(struct net_bridge_port){.br=&linux_bridge,.dev=i?&wifi:&out,.state=BR_STATE_FORWARDING,.flags=BR_MULTICAST_TO_UNICAST,.vlan={true}};
 struct net_bridge_port_group members[3]={
  {.key={&ports[1]},.eth_addr={2,1,2,3,4,5}},
  {.key={&ports[1]},.eth_addr={2,1,2,3,4,6}},
  {.key={&ports[0]},.eth_addr={2,6,5,4,3,2}}};
 members[0].next=&members[1];members[1].next=&members[2];
 struct net_bridge_mdb_entry mdb={.ports=members,.addr={.proto=0x800}};struct net_bridge_mcast mcast={};
 struct rtl9617c_bridge_frame frame=skb.saved;
 for(unsigned scenario=0;scenario<10;scenario++) {
  fe.event_count=0;copies=copy_failure=clone_calls=failed_clone=drops=0;memset(delivered,0,sizeof(delivered));
  members[0].eth_addr[0]=members[1].eth_addr[0]=2;members[1].eth_addr[5]=6;ports[1].flags=BR_MULTICAST_TO_UNICAST;
  members[2].key.port=&ports[0];ports[0].dev=&out;
  struct net_device tagged=wifi;tagged.ifindex=14;tagged.lower=&wifi;tagged.vid=222;tagged.vlan.flags=VLAN_FLAG_REORDER_HDR;devices[14]=&tagged;
  ports[1].dev=scenario==9?&tagged:&wifi;
  if(scenario==2)copy_failure=2;
  if(scenario==3)members[1].eth_addr[5]=5; /* Duplicate complete recipient. */
  if(scenario==4)memset(members[0].eth_addr,255,6); /* Original MC recipient plus UC on same VIF. */
  if(scenario==5){ports[1].flags|=BR_HAIRPIN_MODE;memcpy(members[0].eth_addr,frame.source,6);}
  bool hairpin=scenario==5,local=scenario!=1;
  struct net_device *origin=hairpin?&wifi:&lan;fdb_source=origin;
  skb=(struct sk_buff){.dev=origin,.saved=frame,.frame=frame};skb.saved.port=skb.saved.ingress=origin->ifindex;
  skb.saved.wifi_id=hairpin?2:0;skb.saved.multicast_epoch=fe.mc_gate.epoch;
  br_multicast_flood(&mdb,&skb,&mcast,local,false);fe_mcast_process(&bridge);
  if(scenario==2||scenario==3)assert(!fe.indexed&&!fe.mc_gate.active);
  else {
   unsigned expected=3+local-hairpin;assert(fe.indexed&&fe.indexed->replica_count==expected&&fe.mc_gate.active);
   unsigned cookie[3]={},seen=0;
   for(unsigned c=1;c<256;c++)if(fe.mc_directs[c]&&fe.mc_directs[c]->flow==fe.indexed){
    const struct rtl_fe_mc_direct *d=fe.mc_directs[c];unsigned which=d->wifi_id?(d->frame.destination[5]==6?1:0):2;
    cookie[which]=c;seen++;assert(c!=fe.indexed->mc_group);
    /* NI leaves DA multicast, preserving each final VLAN and original IP.
     * The immutable owner restores only the selected recipient's DA. */
    struct sk_buff copy={.frame=d->frame,.priority=5};ether_addr_copy(copy.frame.destination,frame.destination);
    if(scenario==6){cow_failure=-ENOMEM;assert(fe_mcast_host_receive(&bridge,&copy,c,hairpin?18:0)==-ENOMEM);cow_failure=0;}
    assert(fe_mcast_host_receive(&bridge,&copy,c,hairpin?18:0)==(d->wifi_id?1:2));
    assert(copy.dev==(d->wifi_id?&wifi:&out)&&ether_addr_equal(copy.frame.destination,d->frame.destination));
    assert(copy.priority==(hairpin?5:0)&&fe_mcast_host_local(&bridge,&copy,&br,0)==-ESTALE);
   }
   assert(seen==3-hairpin&&cookie[1]&&cookie[2]&&(hairpin||cookie[0]));
   if(scenario==9){
    const struct rtl_fe_mc_direct *d=fe.mc_directs[cookie[1]];
    assert(d->frame.tags==1&&d->frame.tag[0].tci==222&&d->output==14&&d->physical==12);
   }
   /* Same generation/identical full decision must reuse all recipients. */
   unsigned installs=fe.installs;
   skb=(struct sk_buff){.dev=origin,.saved=frame,.frame=frame};skb.saved.port=skb.saved.ingress=origin->ifindex;
   skb.saved.wifi_id=hairpin?2:0;skb.saved.multicast_epoch=fe.mc_gate.epoch;
   br_multicast_flood(&mdb,&skb,&mcast,local,false);fe_mcast_process(&bridge);assert(fe.installs==installs&&fe.mc_gate.active);
   if(local){struct sk_buff copy={.frame=frame};assert(!fe_mcast_host_receive(&bridge,&copy,fe.indexed->mc_group,hairpin?18:0));}
   if(scenario==7){
    struct sk_buff copy={.frame=frame};ext_change=&fe;
    assert(fe_mcast_host_receive(&bridge,&copy,cookie[1],0)==-ESTALE&&!ext_change);
   }
   if(scenario==8){fe.age_fail=1;assert(rtl_fe_flush_port(&fe,&wifi)==-ETIMEDOUT&&!fe.mc_gate.active);}
   assert(!rtl_fe_flush_port(&fe,&wifi));
  wp.wifi_closing=false; /* Model the next successful NI attach. */
   struct sk_buff stale={.frame=frame};assert(fe_mcast_host_receive(&bridge,&stale,cookie[1],hairpin?18:0)==-ESTALE);
  }
  memcpy(members[0].eth_addr,(u8[]){2,1,2,3,4,5},6);assert(!net.refs);
 }
 /* EPON and per-GEM P6 destinations use the same owned CPU DA edit, then
  * their normal ndo_start_xmit performs current queue/transport decisions. */
 struct net_device ep=out,onu=out;ep.ifindex=9;onu.ifindex=8;onu.lower=&p6;onu.vid=100;onu.vlan.flags=VLAN_FLAG_REORDER_HDR;
 devices[9]=&ep;devices[8]=&onu;
 struct rtl_fe_port epp={.fe=&fe,.netdev=&ep,.source=7,.destination=32};fe.ports[4]=&epp;fe.pon_requested=true;
 struct rtl_epon_paths paths={.generation=1,.count=1,.queues=8,.active=1,.queue_mask={255}};
 assert(!rtl_fe_set_epon_paths(&fe,&paths));
 mdb.ports=&members[2];ports[0].dev=&ep;
 for(unsigned transport=0;transport<2;transport++) {
  if(transport)ports[0].dev=&onu;
  skb=(struct sk_buff){.dev=&lan,.saved=frame,.frame=frame};skb.saved.multicast_epoch=fe.mc_gate.epoch;fdb_source=&lan;fe.event_count=0;
  br_multicast_flood(&mdb,&skb,&mcast,false,false);fe_mcast_process(&bridge);
  assert(fe.indexed&&fe.indexed->replica_count==1&&fe.mc_gate.active);unsigned cookie=0;
  for(unsigned c=1;c<256;c++)if(fe.mc_directs[c]&&fe.mc_directs[c]->flow==fe.indexed)cookie=c;
  assert(cookie);struct sk_buff copy={.frame=fe.mc_directs[cookie]->frame};ether_addr_copy(copy.frame.destination,frame.destination);
  assert(fe_mcast_host_receive(&bridge,&copy,cookie,0)==2&&copy.dev==(transport?&p6:&ep));
  assert(!rtl_fe_flush_port(&fe,transport?&p6:&ep));
 }
 assert(!rtl_fe_set_epon_paths(&fe,NULL));
 /* Zero selected recipients without local RX must still remain software. */
 mdb.ports=NULL;skb=(struct sk_buff){.dev=&lan,.saved=frame,.frame=frame};fdb_source=&lan;
 skb.saved.multicast_epoch=fe.mc_gate.epoch;br_multicast_flood(&mdb,&skb,&mcast,false,false);fe_mcast_process(&bridge);
 assert(!fe.indexed&&!net.refs);fe_mcast_purge(&bridge);for(unsigned c=1;c<256;c++)free(fe.mc_directs[c]);free(fe.memory);
 puts("MC-to-UC: actual Linux per-recipient selection, same-VIF copies, Ethernet output, host/no-host, broadcast placeholder, hairpin DAD, missing/duplicate/OOM and stale-generation rejection passed");
}
'''
with tempfile.TemporaryDirectory(prefix='bridge-mcast-m2u-') as name:
    tmp=Path(name);(tmp/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-sign-compare','-g','-fsanitize=address,undefined',
                    '-I'+str(scope['scope']['DRIVERS']),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),
                    str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
