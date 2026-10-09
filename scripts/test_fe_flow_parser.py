#!/usr/bin/env python3
"""Check the native parser against Linux6.18's actual NAT action builders."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
native = (drivers / 'rtl9617c-fe.c').read_text()
kernel = Path(subprocess.check_output(['python3', str(root / 'port/tools/project_paths.py'),
    '--kernel-directory', str(root / 'sources/openwrt')], text=True).strip())
upstream_path = kernel / 'net/netfilter/nf_flow_table_offload.c'
upstream = upstream_path.read_text()

def function(source, name):
    start = re.search(r'^.*\b' + re.escape(name) + r'\([^;]*?\)\s*\{', source, re.M).start()
    if source[start:].startswith(name):
        start = source.rfind('\n', 0, start - 1) + 1
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stddef.h>
#include <arpa/inet.h>
#include <linux/ip.h>
#include "rtl9617c-fe-format.h"
typedef uint8_t u8;typedef uint16_t u16,__be16;typedef uint32_t u32;
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define htons(v) __builtin_bswap16((uint16_t)(v))
#endif
#define ETH_ALEN 6
#define ETH_HLEN 14
#define ETH_P_IP 0x800
#define ETH_P_IPV6 0x86dd
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define ETH_P_PPP_SES 0x8864
#define VLAN_VID_MASK 4095
#define VLAN_HLEN 4
#define BIT(n) (1U<<(n))
#define NF_FLOW_RULE_ACTION_MAX 16
#define unlikely(x) (x)
enum {FLOW_DISSECTOR_KEY_META,FLOW_DISSECTOR_KEY_BASIC,FLOW_DISSECTOR_KEY_IPV4_ADDRS,
 FLOW_DISSECTOR_KEY_PORTS,FLOW_DISSECTOR_KEY_VLAN,FLOW_DISSECTOR_KEY_CVLAN,FLOW_DISSECTOR_KEY_ENC_CONTROL,FLOW_DISSECTOR_KEY_IPV6_ADDRS};
enum {FLOW_ACTION_REDIRECT,FLOW_ACTION_CSUM,FLOW_ACTION_MANGLE,FLOW_ACTION_VLAN_POP,FLOW_ACTION_VLAN_PUSH,FLOW_ACTION_PPPOE_PUSH};
enum flow_action_mangle_base {FLOW_ACT_MANGLE_HDR_TYPE_ETH,FLOW_ACT_MANGLE_HDR_TYPE_IP4,
 FLOW_ACT_MANGLE_HDR_TYPE_TCP,FLOW_ACT_MANGLE_HDR_TYPE_UDP};
struct net_device {int ifindex;};
struct flow_dissector_key_meta {int ingress_ifindex;};
struct flow_dissector_key_basic {__be16 n_proto;u8 ip_proto;};
struct flow_dissector_key_ipv4_addrs {__be32 src,dst;};
struct flow_dissector_key_ipv6_addrs {struct in6_addr src,dst;};
struct flow_dissector_key_ports {__be16 src,dst;};
struct flow_dissector_key_vlan {unsigned int vlan_id,vlan_priority,vlan_dei;__be16 vlan_tpid;};
struct flow_action_entry {int id;union {struct net_device *dev;
 struct {enum flow_action_mangle_base htype;u32 offset,mask,val;} mangle;
 struct {unsigned short vid;__be16 proto;u8 prio;} vlan;struct {unsigned short sid;} pppoe;};};
struct flow_action {unsigned int num_entries;struct flow_action_entry entries[NF_FLOW_RULE_ACTION_MAX];};
struct flow_rule {
 unsigned int used;
 struct flow_dissector_key_meta meta,meta_mask;
 struct flow_dissector_key_basic basic,basic_mask;
 struct flow_dissector_key_ipv4_addrs ipv4,ipv4_mask;
 struct flow_dissector_key_ipv6_addrs ipv6,ipv6_mask;
 struct flow_dissector_key_ports ports,ports_mask;
 struct flow_dissector_key_vlan vlan,vlan_mask,cvlan,cvlan_mask;
 struct flow_action action;
};
struct flow_cls_offload {struct flow_rule *rule;};
#define FLOW_MATCH(name,type) \
 struct flow_match_##name {struct flow_dissector_key_##type *key,*mask;};
FLOW_MATCH(meta,meta)
FLOW_MATCH(basic,basic)
FLOW_MATCH(ipv4_addrs,ipv4_addrs)
FLOW_MATCH(ipv6_addrs,ipv6_addrs)
FLOW_MATCH(ports,ports)
FLOW_MATCH(vlan,vlan)
static void flow_rule_match_meta(struct flow_rule *r,struct flow_match_meta *m){m->key=&r->meta;m->mask=&r->meta_mask;}
static void flow_rule_match_basic(struct flow_rule *r,struct flow_match_basic *m){m->key=&r->basic;m->mask=&r->basic_mask;}
static void flow_rule_match_ipv4_addrs(struct flow_rule *r,struct flow_match_ipv4_addrs *m){m->key=&r->ipv4;m->mask=&r->ipv4_mask;}
static void flow_rule_match_ipv6_addrs(struct flow_rule *r,struct flow_match_ipv6_addrs *m){m->key=&r->ipv6;m->mask=&r->ipv6_mask;}
static void flow_rule_match_ports(struct flow_rule *r,struct flow_match_ports *m){m->key=&r->ports;m->mask=&r->ports_mask;}
static void flow_rule_match_vlan(struct flow_rule *r,struct flow_match_vlan *m){m->key=&r->vlan;m->mask=&r->vlan_mask;}
static void flow_rule_match_cvlan(struct flow_rule *r,struct flow_match_vlan *m){m->key=&r->cvlan;m->mask=&r->cvlan_mask;}
static bool flow_rule_match_key(struct flow_rule *r,unsigned int key){return r->used&(1U<<key);}
static struct flow_rule *flow_cls_offload_flow_rule(struct flow_cls_offload *f){return f->rule;}
#define flow_action_for_each(i,act,a) for(i=0;i<(int)(a)->num_entries && ((act)=&(a)->entries[i]);i++)
static void ether_addr_copy(u8 *d,const u8 *s){memcpy(d,s,6);}
static bool is_valid_ether_addr(const u8 *m){return !(m[0]&1) && memcmp(m,"\0\0\0\0\0\0",6);}
static u32 get_unaligned(const u32 *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned(u32 v,u32 *p){memcpy(p,&v,4);}
struct rtl_fe;
struct rtl_fe_port {struct rtl_fe *fe;struct net_device *netdev;unsigned int source,destination,llid;};
struct rtl_fe {struct rtl_fe_port *ports[2];};
static struct rtl_fe_port *fe_port(struct rtl_fe *fe,struct net_device *dev){for(unsigned int i=0;i<2;i++)if(fe->ports[i]->netdev==dev)return fe->ports[i];return NULL;}
struct net {};
enum flow_offload_tuple_dir {FLOW_OFFLOAD_DIR_ORIGINAL,FLOW_OFFLOAD_DIR_REPLY};
struct flow_offload_tuple {u8 l4proto;struct in_addr src_v4,dst_v4;__be16 src_port,dst_port;
 unsigned int encap_num,in_vlan_ingress;struct {unsigned short id;__be16 proto;} encap[2];};
struct flow_offload {struct {struct flow_offload_tuple tuple;} tuplehash[2];};
struct nf_flow_rule {struct flow_rule *rule;};
'''

test = r'''
/* Literal network addresses/ports from the verified ASIC DNS transaction.
 * MAC mangle offsets/masks are Linux's Ethernet constructor format. */
static void ethernet_actions(struct nf_flow_rule *nf,const u8 *source,const u8 *destination,struct net_device *output)
{
 u32 value,mask;uint16_t short_value;
 struct flow_action_entry *entry;
 entry=flow_action_entry_next(nf);memcpy(&short_value,source,2);value=(u32)short_value<<16;mask=0xffff;
 flow_offload_mangle(entry,FLOW_ACT_MANGLE_HDR_TYPE_ETH,4,&value,&mask);
 entry=flow_action_entry_next(nf);memcpy(&value,source+2,4);mask=0;
 flow_offload_mangle(entry,FLOW_ACT_MANGLE_HDR_TYPE_ETH,8,&value,&mask);
 entry=flow_action_entry_next(nf);memcpy(&value,destination,4);mask=0;
 flow_offload_mangle(entry,FLOW_ACT_MANGLE_HDR_TYPE_ETH,0,&value,&mask);
 entry=flow_action_entry_next(nf);memcpy(&short_value,destination+4,2);value=short_value;mask=0xffff0000;
 flow_offload_mangle(entry,FLOW_ACT_MANGLE_HDR_TYPE_ETH,4,&value,&mask);
 entry=flow_action_entry_next(nf);entry->id=FLOW_ACTION_REDIRECT;entry->dev=output;
}
int main(void)
{
 struct net_device lan={.ifindex=2},pon={.ifindex=4};
 struct rtl_fe_port lp={.netdev=&lan,.source=0,.destination=0},pp={.netdev=&pon,.source=7,.destination=32};
 struct rtl_fe fe={.ports={&lp,&pp}};lp.fe=pp.fe=&fe;
 const u8 source[6]={2,0x66,0x55,0x44,0x33,0x22},destination[6]={2,0x77,0x66,0x55,0x44,0x33};
 unsigned int cases=0;
 for(unsigned int protocol=6;protocol<=17;protocol+=11)
  for(unsigned int vector=0;vector<256;vector++)
   for(unsigned int dir=0;dir<2;dir++) {
    unsigned int port=1+vector*65534/255;
    struct flow_offload flow={};
    flow.tuplehash[0].tuple=(struct flow_offload_tuple){.l4proto=protocol,.src_v4={.s_addr=htonl(0x0a172a01)},
     .dst_v4={.s_addr=htonl(0x74e46f76)},.src_port=htons(port),.dst_port=htons(53)};
    flow.tuplehash[1].tuple=(struct flow_offload_tuple){.l4proto=protocol,.src_v4={.s_addr=htonl(0x74e46f76)},
     .dst_v4={.s_addr=htonl(0xc0a8471c)},.src_port=htons(53),.dst_port=htons(65536-port)};
    struct flow_offload_tuple *t=&flow.tuplehash[dir].tuple;
    struct flow_rule rule={.used=15,.meta={.ingress_ifindex=dir?4:2},
     .basic={.n_proto=htons(ETH_P_IP),.ip_proto=protocol},.basic_mask={.n_proto=htons(65535),.ip_proto=255},
     .ipv4={t->src_v4.s_addr,t->dst_v4.s_addr},.ipv4_mask={~0U,~0U},.ports={t->src_port,t->dst_port},.ports_mask={65535,65535}};
    struct nf_flow_rule nf={.rule=&rule};struct flow_cls_offload cls={.rule=&rule};
    struct rtl_fe_rule parsed={};
    assert(!flow_offload_ipv4_snat(NULL,&flow,dir,&nf));
    assert(!flow_offload_port_snat(NULL,&flow,dir,&nf));
    ethernet_actions(&nf,source,destination,dir?&lan:&pon);
    assert(!fe_parse(dir?&pp:&lp,&cls,&parsed));
    assert(parsed.key.source==ntohl(t->src_v4.s_addr) && parsed.key.destination==ntohl(t->dst_v4.s_addr));
    assert(parsed.key.source_port==ntohs(t->src_port) && parsed.key.destination_port==ntohs(t->dst_port));
    assert(parsed.action.translate && parsed.action.translate_destination==dir);
    assert(parsed.action.address==(dir?0x0a172a01:0xc0a8471c));
    assert(parsed.action.port==(dir?port:65536-port));
    assert(parsed.action.destination==(dir?0:32) && parsed.action.deep_queue==!dir);
    assert(parsed.action.check_mask==63);
    assert(!memcmp(parsed.source,source,6) && !memcmp(parsed.destination,destination,6));
    rule.used|=1U<<FLOW_DISSECTOR_KEY_VLAN;
    assert(fe_parse(dir?&pp:&lp,&cls,&parsed)==-EOPNOTSUPP);
    rule.used&=~(1U<<FLOW_DISSECTOR_KEY_VLAN);
    rule.ports_mask.src=htons(0xff00);
    assert(fe_parse(dir?&pp:&lp,&cls,&parsed)==-EOPNOTSUPP);
    cases++;
   }
 for(unsigned int direction=0;direction<2;direction++)
  for(unsigned int protocol=6;protocol<=17;protocol+=11) {
   struct flow_rule rule={.used=(1U<<FLOW_DISSECTOR_KEY_META)|(1U<<FLOW_DISSECTOR_KEY_BASIC)|
    (1U<<FLOW_DISSECTOR_KEY_PORTS)|(1U<<FLOW_DISSECTOR_KEY_IPV6_ADDRS),
    .meta={.ingress_ifindex=direction?4:2},.basic={.n_proto=htons(ETH_P_IPV6),.ip_proto=protocol},
    .basic_mask={.n_proto=65535,.ip_proto=255},.ports={htons(53123),htons(443)},.ports_mask={65535,65535}};
   struct nf_flow_rule nf={.rule=&rule};struct flow_cls_offload cls={.rule=&rule};
   struct rtl_fe_rule parsed={};
   for(unsigned int word=0;word<4;word++) {
    rule.ipv6.src.s6_addr32[word]=htonl(0x20010db8+word*37);
    rule.ipv6.dst.s6_addr32[word]=htonl(0x20010db8+word*103+1);
    rule.ipv6_mask.src.s6_addr32[word]=~0U;rule.ipv6_mask.dst.s6_addr32[word]=~0U;
   }
   ethernet_actions(&nf,source,destination,direction?&lan:&pon);
   assert(!fe_parse(direction?&pp:&lp,&cls,&parsed) && parsed.ipv6 && !parsed.action.translate);
   for(unsigned int word=0;word<4;word++) {
    assert(parsed.key6.source[word]==0x20010db8+word*37);
    assert(parsed.key6.destination[word]==0x20010db8+word*103+1);
   }
   assert(parsed.action.destination==(direction?0:32));
   assert(parsed.action.check_mask==63);
   assert(parsed.key6.source_port==53123 && parsed.key6.destination_port==443);
   cases++;
  }
 unsigned int vlan_cases=0;
 for(unsigned int input=0;input<=2;input++)
  for(unsigned int output=0;output<=2;output++)
   for(unsigned int vector=0;vector<16;vector++)
    for(unsigned int protocol=6;protocol<=17;protocol+=11) {
     struct flow_rule rule={.used=15,.meta={.ingress_ifindex=2},
      .basic={.n_proto=htons(ETH_P_IP),.ip_proto=protocol},.basic_mask={.n_proto=htons(65535),.ip_proto=255},
      .ipv4={htonl(0x0a172a01),htonl(0x74e46f76)},.ipv4_mask={~0U,~0U},
      .ports={htons(53000),htons(53)},.ports_mask={65535,65535}};
     struct nf_flow_rule nf={.rule=&rule};struct flow_cls_offload cls={.rule=&rule};
     struct flow_offload flow={};struct rtl_fe_rule parsed={};
     unsigned int id=vector*4095/15;
     flow.tuplehash[0].tuple.encap_num=input;flow.tuplehash[1].tuple.encap_num=output;
     for(unsigned int tag=0;tag<input;tag++) {
      flow.tuplehash[0].tuple.encap[tag].id=(id+tag)&4095;
      flow.tuplehash[0].tuple.encap[tag].proto=htons(ETH_P_8021Q);
      rule.used|=1U<<(tag?FLOW_DISSECTOR_KEY_CVLAN:FLOW_DISSECTOR_KEY_VLAN);
      nf_flow_rule_vlan_match(tag?&rule.cvlan:&rule.vlan,tag?&rule.cvlan_mask:&rule.vlan_mask,
                             (id+tag)&4095,htons(ETH_P_8021Q));
     }
     for(unsigned int tag=0;tag<output;tag++) {
      flow.tuplehash[1].tuple.encap[tag].id=(4095-id+tag)&4095;
      flow.tuplehash[1].tuple.encap[tag].proto=htons(ETH_P_8021Q);
     }
     assert(!linux_vlan_actions(&flow,0,&nf));
     ethernet_actions(&nf,source,destination,&pon);
     assert(!fe_parse(&lp,&cls,&parsed));
     assert(parsed.vlan_count==input && parsed.action.vlan_count==output);
     assert(parsed.action.vlan_set==(input!=0||output!=0));
     assert(parsed.ethernet_bytes==14+4*output);
     for(unsigned int tag=0;tag<input;tag++)
      assert(parsed.vlan[tag].vid==((id+tag)&4095) && parsed.vlan[tag].tpid==1);
     /* Linux pushes the encap array in order; last push is outermost. */
     for(unsigned int tag=0;tag<output;tag++)
      assert(parsed.action.vlan[tag].vid==((4095-id+output-1-tag)&4095) && parsed.action.vlan[tag].tpid==1);
     unsigned int packed[8],key[23];rtl_fe_ipv4_action(packed,&parsed.action);
     rtl_fe_ipv4_key(key,&parsed.key);rtl_fe_vlan_key(key,parsed.vlan,parsed.vlan_count);
     assert((packed[5]&12)==(output<<2)); /* Literal group20 final VLAN count at162. */
     assert(((key[14]>>16)&3)==input);   /* Parser VLAN count at464. */
     if(input) {
      rule.vlan.vlan_tpid=htons(ETH_P_8021AD);
      assert(!fe_parse(&lp,&cls,&parsed) && parsed.vlan[0].tpid==2);
      rule.vlan_mask.vlan_id=255;
      assert(fe_parse(&lp,&cls,&parsed)==-EOPNOTSUPP);
     }
     vlan_cases++;
    }
 printf("Native flow parser: %u NAT/IPv6 and %u Linux VLAN/QinQ pop/push vectors passed\n",cases,vlan_cases);
}
'''

parts = [common, re.search(r'struct rtl_fe_rule \{.*?\n\};', native, re.S)[0]]
parts += [function(native,n) for n in ('fe_vlan_tpid','fe_vlan_match','fe_parse')]
for name in ('flow_action_entry_next','flow_offload_mangle','flow_offload_ipv4_snat',
             'flow_offload_l4proto','flow_offload_port_snat'):
    parts.append(function(upstream, name))
parts.append(function(upstream,'nf_flow_rule_vlan_match'))
loop = upstream.index('\tfor (i = 0; i < tuple->encap_num; i++)')
start = upstream.rfind('\ttuple = &flow->tuplehash[dir].tuple;',0,loop)
end = upstream.index('\n\treturn 0;\n}',loop)+len('\n\treturn 0;\n}')
parts.append('static int linux_vlan_actions(const struct flow_offload *flow,enum flow_offload_tuple_dir dir,struct nf_flow_rule *flow_rule) {\n'
             'const struct flow_offload_tuple *tuple,*other_tuple;int i;\n'+upstream[start:end])
parts.append(test)
with tempfile.TemporaryDirectory(dir=root / 'artifacts',prefix='fe-parser-') as work:
    source = Path(work) / 'parser.c'
    binary = Path(work) / 'parser'
    source.write_text('\n'.join(parts))
    subprocess.run(['cc','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',
        '-I'+str(drivers),str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
(root / 'logs/fe-flow-parser.json').write_text(json.dumps({
    'production_source_sha256':hashlib.sha256(native.encode()).hexdigest(),
    'Linux_NAT_builder_sha256':hashlib.sha256(upstream.encode()).hexdigest(),
    'Linux_version':'6.18.55','TCP_UDP_both_directions':True,
    'vectors':1028,'IPv6_128bit_route_vectors':4,'Linux_VLAN_QinQ_vectors':288,'sanitizers':['ASan','UBSan'],
    'hardware_flowtable_acceptance':False,
},indent=2)+'\n')
