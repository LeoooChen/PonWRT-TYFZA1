// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Linux flowtable backend for the Taurus main Hash/packet editor. */
#include <linux/bitmap.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ip.h>
#include <linux/if_vlan.h>
#include <linux/ipv6.h>
#include <linux/list.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/unaligned.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
#include "rtl9617c-fe.h"
#include "rtl9617c-fe-pipeline.h"

#define FE_COUNTERS 64
#define FE_SOURCE_MACS 15

struct rtl_fe_port {
 struct list_head list;
 struct rtl_fe *fe;
 struct net_device *netdev;
 unsigned int source, destination, llid;
 bool retired;
};
struct rtl_fe_mac {
 unsigned int users, saved[5];
 u8 address[ETH_ALEN];
};
struct rtl_fe_interface {
 unsigned int users, saved, source_mac;
 bool counted;
};
struct rtl_fe_flow {
 struct list_head list;
 unsigned long cookie, lastused;
 struct rtl_fe_port *input, *output;
 unsigned int hash_index, counter, source_mac, destination_index;
 unsigned int destination_saved[4];
 u16 crc16;
 unsigned char ethernet_bytes;
 bool destination_created;
 bool published, source_owned, destination_owned, interface_owned, counted, installed;
 u8 destination[ETH_ALEN];
 u64 bytes;
 u32 packets;
};
static const unsigned int fe_registers[] = {
 0x3834,0x3838,0x383c,0x3840,0x3844,0x3848,0x384c,0x3850,
 0x3854,0x3858,0x385c,0x38a0,0x38b8,0x38bc,0x38c4,0x3924,0x3724,
 0x3400,0x3404,0x3414,0x3418,0x341c,0x3420,0x3424,0x3428,
 0x3700,0x3704,0x3860,0x394c,0x3950,0x3954,0x3958,0x395c,
 0x2300,0x23a0,0x23a4,0xa5f8,
 0x3278,0x327c,0x3280,
};
struct rtl_fe {
 struct device *device;
 void __iomem *registers;
 struct mutex lock;
 struct list_head ports, flows, blocks;
 void *memory;
 dma_addr_t dma;
 bool enabled, prepared;
 int error;
 u64 installs, deletes, misses;
 unsigned int saved[ARRAY_SIZE(fe_registers)], mask_saved[8];
 unsigned int classifier_saved[2][7], map_saved[2][4];
 struct rtl_fe_mac source_macs[FE_SOURCE_MACS];
 struct rtl_fe_interface interfaces[FE_COUNTERS];
 DECLARE_BITMAP(hashes,RTL_FE_HASH_ENTRIES);
 DECLARE_BITMAP(counters,FE_COUNTERS);
};
static unsigned int fe_read(void *context,unsigned int address)
{
 struct rtl_fe *fe=context;
 return readl(fe->registers+address);
}
static void fe_write(void *context,unsigned int address,unsigned int value)
{
 struct rtl_fe *fe=context;
 writel(value,fe->registers+address);
}
static int fe_access(void *context,unsigned int address,unsigned int value)
{
 struct rtl_fe *fe=context;
 u32 status, busy=(address==0x38b0 || address==0x393c)?1:BIT(31);
 writel(value,fe->registers+address);
 return readl_poll_timeout(fe->registers+address,status,!(status&busy),1,100000);
}
static struct rtl_dq_io fe_io(struct rtl_fe *fe)
{
 return (struct rtl_dq_io){fe,fe_read,fe_write,fe_access};
}
static struct rtl_fe_port *fe_port(struct rtl_fe *fe,struct net_device *netdev)
{
 struct rtl_fe_port *port;
 list_for_each_entry(port,&fe->ports,list)
  if(!port->retired && port->netdev==netdev)return port;
 return NULL;
}
static struct rtl_fe_flow *fe_flow(struct rtl_fe *fe,unsigned long cookie)
{
 struct rtl_fe_flow *flow;
 list_for_each_entry(flow,&fe->flows,list)
  if(flow->cookie==cookie)return flow;
 return NULL;
}
static void fe_collect_ports(struct rtl_fe *fe)
{
 struct rtl_fe_port *port,*next;
 struct rtl_fe_flow *flow;

 list_for_each_entry_safe(port,next,&fe->ports,list) {
  bool owned=false;
  if(!port->retired)continue;
  list_for_each_entry(flow,&fe->flows,list)
   if(flow->input==port || flow->output==port){owned=true;break;}
  if(!owned){list_del(&port->list);kfree(port);}
 }
}

/* PON normal business entry changes are made by the PON control transaction,
 * outside this backend. No flow callback acquires the PON state/RTNL locks. */
static int fe_prepare(struct rtl_fe *fe)
{
 struct rtl_dq_io io=fe_io(fe);
 unsigned int mask[8],cls[7],lpb[3],i,side;
 int ret;

 for(i=0;i<ARRAY_SIZE(fe_registers);i++)fe->saved[i]=fe_read(fe,fe_registers[i]);
 ret=rtl_fe_mask_read(&io,63,fe->mask_saved);
 if(ret)return ret;
 for(i=0;i<2;i++) {
  ret=rtl_fe_classifier_read(&io,1024+i,fe->classifier_saved[i]);
  if(ret)return ret;
  for(side=0;side<4;side++) {
   ret=fe_access(fe,0x1654,BIT(31)|(24+i)|(side<<6));
   if(ret)return ret;
   fe->map_saved[i][side]=fe_read(fe,0x1658);
  }
 }
 fe->prepared=true;
 rtl_fe_dma_tables(&io,lower_32_bits(fe->dma),(upper_32_bits(fe->dma)&255)|2);
 ret=fe_access(fe,0x393c,1);
 if(ret)return ret;
 rtl_fe_ipv6_mask(mask);
 /* Keep LLID/POL_ID and LSPID in the key; orig_lspid/profile are ignored.
  * Full128 address prefixes also cover IPv4, which the parser clamps to32.
  * TCP FIN/SYN/RST continue on the kernel control path. */
 mask[6]|=BIT(23)|BIT(26);
 mask[0]&=~(BIT(1)|BIT(2)|BIT(3));
 mask[3]&=~BIT(4); /* mask.vlan_cnt bit100, key.vlan_cnt bits464..465 */
 /* Match VID/TPID on both tags, independently of PCP/DEI. */
 mask[2]&=~(BIT(30)|BIT(31)); /* inner VID/TPID bits94..95 */
 mask[3]&=~(BIT(2)|BIT(3));  /* top VID/TPID bits98..99 */
 ret=rtl_fe_mask_write(&io,63,mask);
 if(ret)return ret;
 for(i=0;i<2;i++) {
  rtl_fe_classify_hash(cls,0,i?7:0);
  ret=rtl_fe_classifier_write(&io,1024+i,cls);
  if(ret)return ret;
  rtl_fe_ingress_lpb(lpb,i?7:0,16);
  rtl_fe_bits_set(lpb,42,4,i);
  for(side=0;side<3;side++)fe_write(fe,0x3414+i*12+side*4,lpb[side]);
  for(side=0;side<4;side++) {
   fe_write(fe,0x1658,i?12:13);
   ret=fe_access(fe,0x1654,BIT(31)|BIT(30)|(i?24:25)|(side<<6));
   if(ret)return ret;
  }
 }
 fe_write(fe,0x3400,(fe_read(fe,0x3400)&~(BIT(0)|BIT(10)|BIT(19)))|BIT(3)|BIT(2));
 fe_write(fe,0x3404,7|(25<<8)|(1<<14)|(24<<16)|(2<<22)|(3<<24));
 fe_write(fe,0x2300,fe_read(fe,0x2300)|BIT(12)|BIT(13));
 fe_write(fe,0x2304+12*12,0xff|(6<<16));
 fe_write(fe,0x2304+13*12,0xff|(6<<16));
 for(i=0;i<5;i++)fe_write(fe,0x394c+i*4,0);
 fe_write(fe,0x3724,RTL_FE_ACTION_GROUPS|(3U<<25));
 fe_write(fe,0x3704,63); /* Stock FC five-tuple priority0. */
 fe_write(fe,0x3700,1);
 fe_write(fe,0x3860,0x020d0000);
 fe_write(fe,0x3278,0x88a88100);
 fe_write(fe,0x327c,0x92009100);
 fe_write(fe,0x3280,0x77); /* Original parser TPID defaults. */
 rtl_fe_cache_start(&io);
 return 0;
}
static int fe_restore(struct rtl_fe *fe)
{
 struct rtl_dq_io io=fe_io(fe);
 unsigned int i,side;
 int ret;

 if(!fe->prepared)return 0;
 fe_write(fe,0x38c4,0);
 ret=fe_access(fe,0x393c,1);
 if(ret)return ret;
 for(i=0;i<2;i++) {
  for(side=0;side<4;side++) {
   fe_write(fe,0x1658,fe->map_saved[i][side]);
   ret=fe_access(fe,0x1654,BIT(31)|BIT(30)|(24+i)|(side<<6));
   if(ret)return ret;
  }
  ret=rtl_fe_classifier_write(&io,1024+i,fe->classifier_saved[i]);
  if(ret)return ret;
 }
 ret=rtl_fe_mask_write(&io,63,fe->mask_saved);
 if(ret)return ret;
 for(i=0;i<ARRAY_SIZE(fe_registers);i++)fe_write(fe,fe_registers[i],fe->saved[i]);
 fe_read(fe,0x383c);
 fe->prepared=false;
 return 0;
}
static int fe_source_get(struct rtl_fe *fe,const u8 *address,unsigned int *index)
{
 struct rtl_dq_io io=fe_io(fe);
 unsigned int i,words[5]={};
 int ret;

 for(i=0;i<FE_SOURCE_MACS;i++)
  if(fe->source_macs[i].users && ether_addr_equal(address,fe->source_macs[i].address)) {
   fe->source_macs[i].users++;
   *index=i;
   return 0;
  }
 for(i=0;i<FE_SOURCE_MACS;i++)if(!fe->source_macs[i].users)break;
 if(i==FE_SOURCE_MACS)return -ENOSPC;
 ret=rtl_fe_source_mac_read(&io,i,fe->source_macs[i].saved);
 if(ret)return ret;
 for(unsigned int byte=0;byte<ETH_ALEN;byte++)rtl_fe_bits_set(words,(5-byte)*8,8,address[byte]);
 words[1]|=BIT(16);
 ret=rtl_fe_source_mac_write(&io,i,words);
 if(ret)return ret;
 ether_addr_copy(fe->source_macs[i].address,address);
 fe->source_macs[i].users=1;
 *index=i;
 return 0;
}
static int fe_source_put(struct rtl_fe *fe,unsigned int index)
{
 struct rtl_dq_io io=fe_io(fe);
 int ret;

 if(fe->source_macs[index].users>1){fe->source_macs[index].users--;return 0;}
 ret=rtl_fe_source_mac_write(&io,index,fe->source_macs[index].saved);
 if(!ret)fe->source_macs[index].users=0;
 return ret;
}
/* L3 output interfaces select a source MAC; they are not flow-table slots.
 * Counted flows own one interface MIB. Further flows share its MAC selection
 * with their action counter bit clear, so they cannot pollute that flow MIB. */
static int fe_interface_get(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_fe_interface *interface;
 unsigned int index=find_first_zero_bit(fe->counters,FE_COUNTERS),i;
 int ret;

 if(index==FE_COUNTERS) {
  for(i=0;i<FE_COUNTERS;i++)
   if(fe->interfaces[i].source_mac==flow->source_mac &&
      (!fe->interfaces[i].counted || index==FE_COUNTERS)) {
    index=i;
    if(!fe->interfaces[i].counted)break;
   }
  if(index==FE_COUNTERS)return -ENOSPC;
 }
 interface=&fe->interfaces[index];
 if(!interface->users) {
  ret=fe_access(fe,0x3000,BIT(31)|index);
  if(ret)return ret;
  interface->saved=fe_read(fe,0x3004);
  interface->source_mac=flow->source_mac;
  __set_bit(index,fe->counters);
 }
 flow->counter=index;
 flow->counted=!interface->counted;
 interface->counted|=flow->counted;
 interface->users++;
 flow->interface_owned=true;
 if(interface->users==1) {
  fe_write(fe,0x3004,BIT(18)|((flow->source_mac+1)<<19));
  return fe_access(fe,0x3000,BIT(31)|BIT(30)|index);
 }
 return 0;
}
static int fe_interface_put(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_fe_interface *interface=&fe->interfaces[flow->counter];
 int ret;

 if(interface->users==1) {
  fe_write(fe,0x3004,interface->saved);
  ret=fe_access(fe,0x3000,BIT(31)|BIT(30)|flow->counter);
  if(ret)return ret;
  __clear_bit(flow->counter,fe->counters);
 }
 interface->users--;
 if(flow->counted)interface->counted=false;
 return 0;
}
static int fe_destination_get(struct rtl_fe *fe,struct rtl_fe_flow *flow,const u8 *address)
{
 struct rtl_dq_io io=fe_io(fe);
 struct rtl_fe_flow *other;
 unsigned int words[4],result;
 int ret;

 ether_addr_copy(flow->destination,address);
 list_for_each_entry(other,&fe->flows,list)
  if(other!=flow && other->destination_owned && ether_addr_equal(other->destination,address)) {
   flow->destination_index=other->destination_index;
   memcpy(flow->destination_saved,other->destination_saved,sizeof(flow->destination_saved));
   flow->destination_created=other->destination_created;
   flow->destination_owned=true;
   return 0;
  }
 rtl_fe_fdb_key(words,address);
 ret=rtl_fe_fdb_command(&io,4,words,&result);
 if(ret)return ret;
 flow->destination_created=(result&15)!=5;
 if(!flow->destination_created)memcpy(flow->destination_saved,words,sizeof(words));
 /* APPEND updates an existing key in place. Pin its MAC/permissions until
  * the last Linux flow releases it, then restore its original contents. */
 if(flow->destination_created)rtl_fe_fdb_key(words,address);
 else words[0]|=BIT(9)|BIT(19)|BIT(20)|BIT(21);
 ret=rtl_fe_fdb_command(&io,0x45,words,&result);
 if(ret)return ret;
 if((result&15)!=5)return -ENOSPC;
 flow->destination_owned=true;
 rtl_fe_fdb_key(words,address);
 ret=rtl_fe_fdb_command(&io,4,words,&result);
 if(ret)return ret;
 if((result&15)!=5)return -EIO;
 flow->destination_index=(result>>4)&0x1fff;
 return 0;
}
static int fe_destination_put(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_dq_io io=fe_io(fe);
 struct rtl_fe_flow *other;
 unsigned int words[4],result;

 list_for_each_entry(other,&fe->flows,list)
  if(other!=flow && other->destination_owned && ether_addr_equal(other->destination,flow->destination))return 0;
 if(flow->destination_created) {
  rtl_fe_fdb_key(words,flow->destination);
  return rtl_fe_fdb_command(&io,0x85,words,&result);
 }
 memcpy(words,flow->destination_saved,sizeof(words));
 return rtl_fe_fdb_command(&io,0x45,words,&result);
}

struct rtl_fe_rule {
 struct rtl_fe_ipv4_key key;
 struct rtl_fe_ipv6_key key6;
 bool ipv6;
 struct rtl_fe_ipv4_action action;
 struct rtl_fe_port *input,*output;
 struct rtl_fe_vlan vlan[2];
 unsigned char vlan_count,ethernet_bytes;
 u8 source[ETH_ALEN],destination[ETH_ALEN];
};
static int fe_vlan_tpid(__be16 protocol)
{
 switch(ntohs(protocol)) {
 case ETH_P_8021Q:return 1;
 case ETH_P_8021AD:return 2;
 default:return -EOPNOTSUPP;
 }
}
static int fe_vlan_match(const struct flow_match_vlan *match,struct rtl_fe_vlan *vlan)
{
 int tpid;

 if(match->mask->vlan_id!=VLAN_VID_MASK || match->mask->vlan_tpid!=htons(0xffff) ||
    match->mask->vlan_priority || match->mask->vlan_dei)return -EOPNOTSUPP;
 tpid=fe_vlan_tpid(match->key->vlan_tpid);
 if(tpid<0)return tpid;
 *vlan=(struct rtl_fe_vlan){.vid=match->key->vlan_id,.tpid=tpid};
 return 0;
}
static int fe_parse(struct rtl_fe_port *port,struct flow_cls_offload *cls,struct rtl_fe_rule *data)
{
 struct flow_rule *rule=flow_cls_offload_flow_rule(cls);
 struct flow_match_meta meta;
 struct flow_match_basic basic;
 struct flow_match_ipv4_addrs addresses;
 struct flow_match_ipv6_addrs addresses6;
 struct flow_match_ports ports;
 struct flow_match_vlan vlan_match;
 struct flow_action_entry *act;
 struct net_device *output=NULL;
 u8 ethernet[ETH_HLEN]={};
 __be32 new_source,new_destination;
 __be16 new_sport,new_dport;
 bool source_changed,destination_changed;
 struct rtl_fe_vlan output_vlan[2];
 bool vlan_known[2]={false,false},vlan_changed=false;
 unsigned int output_vlan_count;
 int i,ret;

 if(!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_META) ||
    !flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_BASIC) ||
    (!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_IPV4_ADDRS) &&
     !flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_IPV6_ADDRS)) ||
    !flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_PORTS))return -EOPNOTSUPP;
 flow_rule_match_meta(rule,&meta);
 if(meta.key->ingress_ifindex!=port->netdev->ifindex)return -EOPNOTSUPP;
 flow_rule_match_basic(rule,&basic);
 data->ipv6=basic.key->n_proto==htons(ETH_P_IPV6);
 if((basic.key->n_proto!=htons(ETH_P_IP) && !data->ipv6) || basic.mask->n_proto!=htons(0xffff) ||
    basic.mask->ip_proto!=255 || (basic.key->ip_proto!=IPPROTO_TCP && basic.key->ip_proto!=IPPROTO_UDP))
  return -EOPNOTSUPP;
 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_ENC_CONTROL))return -EOPNOTSUPP;
 data->vlan_count=0;
 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_VLAN)) {
  flow_rule_match_vlan(rule,&vlan_match);
  ret=fe_vlan_match(&vlan_match,&data->vlan[data->vlan_count++]);
  if(ret)return ret;
 }
 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_CVLAN)) {
  if(!data->vlan_count)return -EOPNOTSUPP;
  flow_rule_match_cvlan(rule,&vlan_match);
  ret=fe_vlan_match(&vlan_match,&data->vlan[data->vlan_count++]);
  if(ret)return ret;
 }
 output_vlan_count=data->vlan_count;
 memcpy(output_vlan,data->vlan,sizeof(output_vlan));
 flow_rule_match_ports(rule,&ports);
 if(ports.mask->src!=htons(0xffff) || ports.mask->dst!=htons(0xffff))return -EOPNOTSUPP;
 data->input=port;
 data->key=(struct rtl_fe_ipv4_key){
  .source_port=ntohs(ports.key->src),.destination_port=ntohs(ports.key->dst),
  .protocol=basic.key->ip_proto,.ingress=port->source,.llid=port->llid,.mask=63};
 if(data->ipv6) {
  if(!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_IPV6_ADDRS))return -EOPNOTSUPP;
  flow_rule_match_ipv6_addrs(rule,&addresses6);
  data->key6=(struct rtl_fe_ipv6_key){.source_port=data->key.source_port,
   .destination_port=data->key.destination_port,.protocol=data->key.protocol,
   .ingress=data->key.ingress,.llid=data->key.llid,.mask=63};
  for(i=0;i<4;i++) {
   if(addresses6.mask->src.s6_addr32[i]!=htonl(~0U) ||
      addresses6.mask->dst.s6_addr32[i]!=htonl(~0U))return -EOPNOTSUPP;
   data->key6.source[i]=ntohl(addresses6.key->src.s6_addr32[i]);
   data->key6.destination[i]=ntohl(addresses6.key->dst.s6_addr32[i]);
  }
  new_source=new_destination=0;
 } else {
  if(!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_IPV4_ADDRS))return -EOPNOTSUPP;
  flow_rule_match_ipv4_addrs(rule,&addresses);
  if(addresses.mask->src!=htonl(~0U) || addresses.mask->dst!=htonl(~0U))return -EOPNOTSUPP;
  data->key.source=ntohl(addresses.key->src);data->key.destination=ntohl(addresses.key->dst);
  new_source=addresses.key->src;new_destination=addresses.key->dst;
 }
 new_sport=ports.key->src;new_dport=ports.key->dst;
 flow_action_for_each(i,act,&rule->action) {
  unsigned int offset=act->mangle.offset;
  u32 value;

  switch(act->id) {
  case FLOW_ACTION_VLAN_POP:
   if(!output_vlan_count)return -EOPNOTSUPP;
   output_vlan[0]=output_vlan[1];vlan_known[0]=vlan_known[1];
   output_vlan_count--;vlan_changed=true;
   break;
  case FLOW_ACTION_VLAN_PUSH:
   if(output_vlan_count==2 || act->vlan.vid>VLAN_VID_MASK || act->vlan.prio>7)
    return -EOPNOTSUPP;
   ret=fe_vlan_tpid(act->vlan.proto);
   if(ret<0)return ret;
   output_vlan[1]=output_vlan[0];vlan_known[1]=vlan_known[0];
   output_vlan[0]=(struct rtl_fe_vlan){.vid=act->vlan.vid,.tpid=ret,.priority=act->vlan.prio};
   vlan_known[0]=true;output_vlan_count++;vlan_changed=true;
   break;
  case FLOW_ACTION_REDIRECT:output=act->dev;break;
  case FLOW_ACTION_CSUM:break;
  case FLOW_ACTION_MANGLE:
   if(act->mangle.htype==FLOW_ACT_MANGLE_HDR_TYPE_ETH) {
    if(offset>ETH_HLEN-sizeof(value))return -EOPNOTSUPP;
    value=get_unaligned((u32 *)(ethernet+offset));
    put_unaligned((value&act->mangle.mask)|act->mangle.val,(u32 *)(ethernet+offset));
   } else if(act->mangle.htype==FLOW_ACT_MANGLE_HDR_TYPE_IP4) {
    if(data->ipv6)return -EOPNOTSUPP;
    if(act->mangle.mask)return -EOPNOTSUPP;
    if(offset==offsetof(struct iphdr,saddr))memcpy(&new_source,&act->mangle.val,4);
    else if(offset==offsetof(struct iphdr,daddr))memcpy(&new_destination,&act->mangle.val,4);
    else return -EOPNOTSUPP;
   } else if(act->mangle.htype==FLOW_ACT_MANGLE_HDR_TYPE_TCP || act->mangle.htype==FLOW_ACT_MANGLE_HDR_TYPE_UDP) {
    __be16 pair[2]={new_sport,new_dport};
    if(offset)return -EOPNOTSUPP;
    memcpy(&value,pair,4);
    value=(value&act->mangle.mask)|act->mangle.val;
    memcpy(pair,&value,4);
    new_sport=pair[0];new_dport=pair[1];
    if(data->ipv6 && (new_sport!=ports.key->src || new_dport!=ports.key->dst))return -EOPNOTSUPP;
   } else return -EOPNOTSUPP;
   break;
  default:return -EOPNOTSUPP;
  }
 }
 data->output=fe_port(port->fe,output);
 if(!data->output || data->input==data->output)return -EOPNOTSUPP;
 ether_addr_copy(data->destination,ethernet);
 ether_addr_copy(data->source,ethernet+ETH_ALEN);
 if(!is_valid_ether_addr(data->source) || !is_valid_ether_addr(data->destination))return -EINVAL;
 source_changed=!data->ipv6 && (new_source!=addresses.key->src || new_sport!=ports.key->src);
 destination_changed=!data->ipv6 && (new_destination!=addresses.key->dst || new_dport!=ports.key->dst);
 if(source_changed && destination_changed)return -EOPNOTSUPP;
 /* A remaining input tag has unknown PCP/DEI. Leave it untouched, or let
  * Linux perform a partial-stack edit; a new tag is fully specified. */
 if(vlan_changed)
  for(i=0;i<output_vlan_count;i++)if(!vlan_known[i])return -EOPNOTSUPP;
 data->action=(struct rtl_fe_ipv4_action){.destination=data->output->destination,.check_mask=63,
  .deep_queue=data->output->destination>=32,.translate=source_changed||destination_changed,
  .translate_destination=destination_changed,.address=ntohl(destination_changed?new_destination:new_source),
  .port=ntohs(destination_changed?new_dport:new_sport)};
 data->action.vlan_set=vlan_changed;
 data->action.vlan_count=output_vlan_count;
 memcpy(data->action.vlan,output_vlan,sizeof(output_vlan));
 data->ethernet_bytes=ETH_HLEN+VLAN_HLEN*output_vlan_count;
 return 0;
}

static int fe_delete(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_dq_io io=fe_io(fe);
 int ret;

 if(flow->published) {
  ret=rtl_fe_main_age(&io,flow->hash_index,0);
  if(ret)return ret;
  ret=rtl_fe_cache_command(&io,flow->hash_index,flow->crc16,1);
  if(ret)return ret;
  flow->published=false;
 }
 ((__le32 *)fe->memory)[flow->hash_index]=0;
 dma_wmb();
 if(flow->destination_owned) {
  ret=fe_destination_put(fe,flow);
  if(ret)return ret;
  flow->destination_owned=false;
 }
 if(flow->source_owned) {
  ret=fe_source_put(fe,flow->source_mac);
  if(ret)return ret;
  flow->source_owned=false;
 }
 if(flow->interface_owned) {
  ret=fe_interface_put(fe,flow);
  if(ret)return ret;
  flow->interface_owned=false;
 }
 __clear_bit(flow->hash_index,fe->hashes);
 list_del(&flow->list);
 if(flow->installed)fe->deletes++;
 kfree(flow);
 return 0;
}
static int fe_replace(struct rtl_fe_port *port,struct flow_cls_offload *cls)
{
 struct rtl_fe *fe=port->fe;
 struct rtl_dq_io io=fe_io(fe);
 struct rtl_fe_rule data={};
 struct rtl_fe_flow *flow;
 unsigned int words[23],action[8],crc32,index,start,counters[7];
 u16 crc16;
 int ret;

 if(port->retired)return -ENODEV;
 ret=fe_parse(port,cls,&data);
 if(ret)return ret;
 if(!fe->enabled || !netif_running(data.input->netdev) || !netif_running(data.output->netdev) ||
    !netif_carrier_ok(data.input->netdev) || !netif_carrier_ok(data.output->netdev))return -EAGAIN;
 if(fe_flow(fe,cls->cookie))return -EEXIST;
 if(data.ipv6)rtl_fe_ipv6_key(words,&data.key6);
 else rtl_fe_ipv4_key(words,&data.key);
 rtl_fe_vlan_key(words,data.vlan,data.vlan_count);
 rtl_fe_key_set(words,RTL_FE_KEY_ORIG_LSPID,0);
 rtl_fe_key_set(words,RTL_FE_KEY_CTRL_SET_ID,0);
 rtl_fe_key_set(words,RTL_FE_KEY_HKEY_ID,0);
 rtl_fe_key_set(words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_key_crc(words,&crc32,&crc16);
 start=rtl_fe_hash_bucket(crc16);
 for(index=start;index<start+32;index++)if(!test_bit(index,fe->hashes))break;
 if(index==start+32)return -ENOSPC;
 flow=kzalloc(sizeof(*flow),GFP_KERNEL);
 if(!flow)return -ENOMEM;
 flow->cookie=cls->cookie;flow->hash_index=index;flow->crc16=crc16;
 flow->input=data.input;flow->output=data.output;flow->lastused=jiffies;
 flow->ethernet_bytes=data.ethernet_bytes;
 __set_bit(index,fe->hashes);
 list_add_tail(&flow->list,&fe->flows);
 ret=fe_source_get(fe,data.source,&flow->source_mac);
 if(ret)goto rollback;
 flow->source_owned=true;
 ret=fe_destination_get(fe,flow,data.destination);
 if(ret)goto rollback;
 ret=fe_interface_get(fe,flow);
 if(ret)goto rollback;
 if(flow->counted) {
  ret=rtl_fe_tx_counter_read(&io,flow->counter,counters);
  if(ret)goto rollback;
  flow->bytes=(u64)counters[1]<<32|counters[0];flow->packets=counters[2];
 }
 data.action.mac_da_index=flow->destination_index;
 data.action.egress_interface=flow->counter;
 rtl_fe_ipv4_action(action,&data.action);
 rtl_fe_action_set(action,RTL_FE_L3_IF_COUNTER_EN,flow->counted);
 ((__le32 *)fe->memory)[index]=cpu_to_le32(crc32);
 memcpy(fe->memory+RTL_FE_ACTION_OFFSET+index*RTL_FE_ACTION_BYTES,action,sizeof(action));
 dma_wmb();
 flow->published=true;
 ret=rtl_fe_main_age(&io,index,1); /* FC idle/valid; hardware hit raises age. */
 if(ret)goto rollback;
 flow->installed=true;
 fe->installs++;
 return 0;
rollback:
 /* Keep an incomplete release attached to its owners for the next callback. */
 if(fe_delete(fe,flow))fe->error=ret;
 return ret;
}
static int fe_stats(struct rtl_fe *fe,struct rtl_fe_flow *flow,struct flow_cls_offload *cls)
{
 struct rtl_dq_io io=fe_io(fe);
 unsigned int words[7],age;
 u64 bytes;
 u64 byte_delta;
 u32 packets;
 u32 packet_delta;
 int ret;

 if(!flow->published)return -ENOENT;
 ret=rtl_fe_main_age_read(&io,flow->hash_index,&age);
 if(ret)return ret;
 if(age>1) {
  ret=rtl_fe_main_age(&io,flow->hash_index,1);
  if(ret)return ret;
  flow->lastused=jiffies;
 }
 if(!flow->counted) {
  flow_stats_update(&cls->stats,0,0,0,flow->lastused,FLOW_ACTION_HW_STATS_DELAYED);
  return 0;
 }
 ret=rtl_fe_tx_counter_read(&io,flow->counter,words);
 if(ret)return ret;
 bytes=(u64)words[1]<<32|words[0];packets=words[2];
 if(bytes!=flow->bytes || packets!=flow->packets)flow->lastused=jiffies;
 byte_delta=bytes-flow->bytes;packet_delta=packets-flow->packets;
 /* The PE MIB measures the output frame, including its VLAN headers. */
 flow_stats_update(&cls->stats,byte_delta-(u64)packet_delta*flow->ethernet_bytes,packet_delta,0,
                   flow->lastused,FLOW_ACTION_HW_STATS_DELAYED);
 flow->bytes=bytes;flow->packets=packets;
 return 0;
}
static int fe_block_callback(enum tc_setup_type type,void *data,void *private)
{
 struct rtl_fe_port *port=private;
 struct rtl_fe *fe=port->fe;
 struct flow_cls_offload *cls=data;
 struct rtl_fe_flow *flow,*next;
 int ret;

 if(type!=TC_SETUP_CLSFLOWER)return -EOPNOTSUPP;
 mutex_lock(&fe->lock);
 list_for_each_entry_safe(flow,next,&fe->flows,list)
  if(!flow->installed) {
   ret=fe_delete(fe,flow);
   if(ret)goto unlock;
  }
 flow=fe_flow(fe,cls->cookie);
 switch(cls->command) {
 case FLOW_CLS_REPLACE:ret=fe_replace(port,cls);break;
 case FLOW_CLS_DESTROY:
  ret=flow && flow->input==port?fe_delete(fe,flow):-ENOENT;
  break;
 case FLOW_CLS_STATS:
  ret=flow && flow->input==port?fe_stats(fe,flow,cls):-ENOENT;
  break;
 default:ret=-EOPNOTSUPP;
 }
 if(ret && ret!=-EOPNOTSUPP && ret!=-ENOENT && ret!=-EEXIST)fe->misses++;
 fe_collect_ports(fe);
unlock:
 mutex_unlock(&fe->lock);
 return ret;
}
int rtl_fe_setup_tc(struct rtl_fe *fe,struct net_device *netdev,enum tc_setup_type type,void *data)
{
 struct rtl_fe_port *port;
 struct flow_block_offload *block=data;
 int ret;

 if(!fe || type!=TC_SETUP_FT)return -EOPNOTSUPP;
 if(block->binder_type!=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS)return -EOPNOTSUPP;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 ret=port?flow_block_cb_setup_simple(block,&fe->blocks,fe_block_callback,port,port,true):-ENODEV;
 mutex_unlock(&fe->lock);
 return ret;
}

struct rtl_fe *rtl_fe_allocate(struct device *device,void __iomem *registers)
{
 struct rtl_fe *fe=kzalloc(sizeof(*fe),GFP_KERNEL);
 unsigned int *defaults,i;

 if(!fe)return ERR_PTR(-ENOMEM);
 fe->device=device;fe->registers=registers;
 mutex_init(&fe->lock);
 INIT_LIST_HEAD(&fe->ports);INIT_LIST_HEAD(&fe->flows);INIT_LIST_HEAD(&fe->blocks);
 fe->memory=dma_alloc_coherent(device,RTL_FE_MEMORY_BYTES,&fe->dma,GFP_KERNEL);
 if(!fe->memory){kfree(fe);return ERR_PTR(-ENOMEM);}
 defaults=fe->memory+RTL_FE_DEFAULT_OFFSET;
 for(i=0;i<32;i++)rtl_fe_cpu_miss(defaults+i*4);
 dma_wmb();
 return fe;
}
int rtl_fe_add_port(struct rtl_fe *fe,struct net_device *netdev,unsigned int source,
                    unsigned int destination,unsigned int llid)
{
 struct rtl_fe_port *port;

 if(!fe)return 0;
 port=kzalloc(sizeof(*port),GFP_KERNEL);
 if(!port)return -ENOMEM;
 port->fe=fe;port->netdev=netdev;port->source=source;port->destination=destination;port->llid=llid;
 mutex_lock(&fe->lock);
 list_add_tail(&port->list,&fe->ports);
 netdev->hw_features|=NETIF_F_HW_TC;
 netdev->features|=NETIF_F_HW_TC;
 mutex_unlock(&fe->lock);
 return 0;
}
void rtl_fe_remove_port(struct rtl_fe *fe,struct net_device *netdev)
{
 struct rtl_fe_port *port;
 struct rtl_fe_flow *flow,*next;

 if(!fe)return;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 if(port) {
  port->retired=true;
  list_for_each_entry_safe(flow,next,&fe->flows,list)
   if(flow->input==port || flow->output==port) {
    int ret=fe_delete(fe,flow);
    if(ret)fe->error=ret;
   }
  fe_collect_ports(fe);
 }
 mutex_unlock(&fe->lock);
}
void rtl_fe_flush_port(struct rtl_fe *fe,struct net_device *netdev)
{
 struct rtl_fe_port *port;
 struct rtl_fe_flow *flow,*next;

 if(!fe)return;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 list_for_each_entry_safe(flow,next,&fe->flows,list)
  if(flow->input==port || flow->output==port) {
   int ret=fe_delete(fe,flow);
   if(ret)fe->error=ret;
  }
 mutex_unlock(&fe->lock);
}
int rtl_fe_enable(struct rtl_fe *fe)
{
 int ret;

 if(!fe)return -EOPNOTSUPP;
 mutex_lock(&fe->lock);
 if(fe->enabled){ret=0;goto unlock;}
 ret=fe_prepare(fe);
 if(ret){fe_restore(fe);goto unlock;}
 /* The DMA tables, miss actions and port maps exist before admitting LAN. */
 fe_write(fe,0xa5f8,(fe_read(fe,0xa5f8)&~(63U<<16))|(25U<<16));
 fe->enabled=true;
unlock:
 fe->error=ret;
 mutex_unlock(&fe->lock);
 return ret;
}
int rtl_fe_disable(struct rtl_fe *fe)
{
 struct rtl_fe_flow *flow,*next;
 unsigned int i;
 int ret=0;

 if(!fe)return 0;
 mutex_lock(&fe->lock);
 fe->enabled=false;
 if(fe->prepared) {
  for(i=0;i<ARRAY_SIZE(fe_registers);i++)
   if(fe_registers[i]==0xa5f8)fe_write(fe,0xa5f8,fe->saved[i]);
  usleep_range(1000,2000);
  list_for_each_entry_safe(flow,next,&fe->flows,list) {
   ret=fe_delete(fe,flow);
   if(ret)goto unlock;
  }
  ret=fe_restore(fe);
 }
 fe_collect_ports(fe);
unlock:
 fe->error=ret;
 mutex_unlock(&fe->lock);
 return ret;
}
void rtl_fe_free(struct rtl_fe *fe)
{
 struct rtl_fe_port *port,*next;
 struct rtl_fe_flow *flow,*flow_next;

 if(!fe)return;
 rtl_fe_disable(fe);
 list_for_each_entry_safe(flow,flow_next,&fe->flows,list){list_del(&flow->list);kfree(flow);}
 list_for_each_entry_safe(port,next,&fe->ports,list){list_del(&port->list);kfree(port);}
 dma_free_coherent(fe->device,RTL_FE_MEMORY_BYTES,fe->memory,fe->dma);
 kfree(fe);
}
ssize_t rtl_fe_state(struct rtl_fe *fe,char *buffer)
{
 ssize_t length;
 struct rtl_fe_flow *flow;
 unsigned int flows=0,counted=0;

 if(!fe)return sysfs_emit(buffer,"available=0\n");
 mutex_lock(&fe->lock);
 list_for_each_entry(flow,&fe->flows,list)if(flow->published) {
  flows++;
  counted+=flow->counted;
 }
 length=sysfs_emit(buffer,"available=1 enabled=%u prepared=%u error=%d flows=%u hash_entries=%u counter_slots=%u counted_flows=%u activity_flows=%u installs=%llu deletes=%llu rejected=%llu\n",
  fe->enabled,fe->prepared,fe->error,flows,RTL_FE_HASH_ENTRIES,
  FE_COUNTERS,counted,flows-counted,fe->installs,fe->deletes,fe->misses);
 mutex_unlock(&fe->lock);
 return length;
}
