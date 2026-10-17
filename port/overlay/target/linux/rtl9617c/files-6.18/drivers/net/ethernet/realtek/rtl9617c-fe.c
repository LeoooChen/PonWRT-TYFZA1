// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Linux flowtable backend for the Taurus main Hash/packet editor. */
#include <linux/bitmap.h>
#include <linux/delay.h>
#include <linux/debugfs.h>
#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ip.h>
#include <linux/if_vlan.h>
#include <linux/if_bridge.h>
#include <linux/ipv6.h>
#include <linux/ppp_defs.h>
#include <linux/list.h>
#include <linux/mutex.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
#include "rtl9617c-fe.h"
#include "rtl9617c-fe-pipeline.h"
#include "rtl9617c-wifi-format.h"
#include "rtl9617c-pon-path.h"
#include <linux/rtl9617c-bridge.h>

struct rtl_fe_bridge;
static struct rtl_fe_bridge *fe_bridge_start(struct rtl_fe *fe);
static void fe_bridge_stop(struct rtl_fe_bridge *bridge);

#define FE_INTERFACES 64
#define FE_COUNTERS 32
#define FE_SOURCE_MACS 15
#define FE_LAN_PORTS 5
#define FE_LAN_RX_CTRL(port) (0xa5f8+0x90*(port))

struct rtl_fe_port {
 struct list_head list;
 struct rtl_fe *fe;
 struct net_device *netdev;
 unsigned int source, destination, llid, wifi_id, bindings;
 bool retired;
};
struct rtl_fe_wifi_peer {
 struct list_head list;
 struct rtl_fe_port *port;
 u8 address[ETH_ALEN];
 unsigned int destination;
};
struct rtl_fe_mac {
 unsigned int users, saved[5];
 u8 address[ETH_ALEN];
};
struct rtl_fe_interface {
 unsigned int users, saved, source_mac, pppoe;
 bool counted;
};
struct rtl_fe_mtu {
 unsigned int users, size, saved;
};
struct rtl_fe_flow {
 struct list_head list;
 unsigned long cookie, lastused;
 struct rtl_fe_port *input, *output;
 unsigned int hash_index, counter, source_mac, destination_index;
 unsigned int pppoe, mtu_index;
 unsigned int ingress_session;
 unsigned int ingress_pol;
 u8 ip_source[16],ip_destination[16],protocol;
 u8 ethernet_source[ETH_ALEN];
 u16 ethernet_protocol;
 u16 source_port,destination_port;
 bool ipv6, bridge;
 bool learned;
 unsigned int bridge_index;
 unsigned int bridge_ingress, bridge_egress;
 u8 bridge_tags;
 struct {u16 tci,protocol;} bridge_tag[2];
 u16 bridge_vid;
 u16 bridge_ppp_protocol;
 unsigned int destination_saved[4];
 u16 crc16;
 unsigned char ethernet_bytes;
 bool destination_created;
 bool mtu_owned;
 bool published, source_owned, destination_owned, interface_owned, counted, installed, deleting;
 u8 destination[ETH_ALEN];
 u64 bytes;
 u32 packets;
};
static const unsigned int fe_registers[] = {
 0x3834,0x3838,0x383c,0x3840,0x3844,0x3848,0x384c,0x3850,
 0x3854,0x3858,0x385c,0x38a0,0x38b8,0x38bc,0x38c4,0x3924,0x3724,
 0x3400,0x3404,0x3414,0x3418,0x341c,0x3420,0x3424,0x3428,
 0x3700,0x3704,0x3708,0x370c,0x3710,0x3714,0x3718,0x371c,0x3720,0x3860,0x394c,0x3950,0x3954,0x3958,0x395c,
 0x2300,0x23a0,0x23a4,0xa1e0,
 0x3278,0x327c,0x3280,
 0x3500,0x3504,0x351c,
 0x3940,0x3944,0x3948,0x3c80,
 0x3508,0x30cc,
};
struct rtl_fe {
 struct device *device;
 struct dentry *debug;
 void __iomem *registers;
 struct mutex lock;
 struct delayed_work cleanup_work;
 struct list_head ports, flows, blocks, wifi_peers;
 void *memory;
 dma_addr_t dma;
 bool enabled, prepared;
 int error;
 u64 installs, deletes, misses;
 unsigned int saved[ARRAY_SIZE(fe_registers)], mask_saved[8];
 unsigned int wifi_mask_saved[8];
 unsigned int bridge_mask_saved[6][8];
 struct rtl_fe_cam_protocol protocol_cam[3];
 unsigned char ppp_protocol_ids[2];
 unsigned int lan_saved[FE_LAN_PORTS];
 unsigned int wifi_ippb_saved;
 unsigned int classifier_saved[2][7], map_saved[2][4];
 struct rtl_fe_mac source_macs[FE_SOURCE_MACS];
 struct rtl_fe_interface interfaces[FE_INTERFACES];
 struct rtl_fe_mtu mtus[16];
 struct rtl_itu_paths *itu_paths;
 struct net_device *itu_netdev;
 struct rtl_fe_bridge *bridge;
 DECLARE_BITMAP(hashes,RTL_FE_HASH_ENTRIES);
 DECLARE_BITMAP(counters,FE_INTERFACES);
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
static struct rtl_fe_wifi_peer *fe_wifi_peer(struct rtl_fe *fe,struct rtl_fe_port *port,const u8 *address)
{
 struct rtl_fe_wifi_peer *peer;
 list_for_each_entry(peer,&fe->wifi_peers,list)
  if(peer->port==port && ether_addr_equal(peer->address,address))return peer;
 return NULL;
}
static void fe_wifi_forget_port(struct rtl_fe *fe,struct rtl_fe_port *port)
{
 struct rtl_fe_wifi_peer *peer,*next;
 list_for_each_entry_safe(peer,next,&fe->wifi_peers,list)
  if(peer->port==port){list_del(&peer->list);kfree(peer);}
}
static void fe_collect_ports(struct rtl_fe *fe)
{
 struct rtl_fe_port *port,*next;
 struct rtl_fe_flow *flow;

 list_for_each_entry_safe(port,next,&fe->ports,list) {
  bool owned=false;
  if(!port->retired || port->bindings)continue;
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
 for(i=0;i<FE_LAN_PORTS;i++)fe->lan_saved[i]=fe_read(fe,FE_LAN_RX_CTRL(i));
 ret=fe_access(fe,0x11dc,BIT(31)|RTL_WIFI_SOURCE);
 if(ret)return ret;
 fe->wifi_ippb_saved=fe_read(fe,0x11e0);
 ret=rtl_fe_mask_read(&io,63,fe->mask_saved);
 if(ret)return ret;
 ret=rtl_fe_mask_read(&io,62,fe->wifi_mask_saved);
 if(ret)return ret;
 for(i=0;i<6;i++) {
  ret=rtl_fe_mask_read(&io,61-i,fe->bridge_mask_saved[i]);
  if(ret)return ret;
 }
 /* Default type0 FIB index is 1024 | (profile << 2), not a packed row. */
 for(i=0;i<2;i++) {
  ret=rtl_fe_classifier_read(&io,1024+4*i,fe->classifier_saved[i]);
  if(ret)return ret;
  for(side=0;side<4;side++) {
   ret=fe_access(fe,0x1654,BIT(31)|(24+i)|(side<<6));
   if(ret)return ret;
   fe->map_saved[i][side]=fe_read(fe,0x1658);
  }
 }
 fe->prepared=true;
 /* The inherited IPPB maps source18 to port4. Preserve the Wi-Fi descriptor's
  * logical source before STG0; per-interface identity remains in metadata. */
 fe_write(fe,0x11e0,(fe->wifi_ippb_saved&~63U)|RTL_WIFI_SOURCE);
 ret=fe_access(fe,0x11dc,BIT(31)|BIT(30)|RTL_WIFI_SOURCE);
 if(ret)return ret;
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
 mask[2]&=~(BIT(25)|BIT(27)); /* PPPoE session/type bits89/91 */
 ret=rtl_fe_mask_write(&io,63,mask);
 if(ret)return ret;
 rtl_fe_wifi_mask(mask);
 ret=rtl_fe_mask_write(&io,62,mask);
 if(ret)return ret;
 rtl_fe_bridge_mask(mask);
 ret=rtl_fe_mask_write(&io,61,mask);
 if(ret)return ret;
 rtl_fe_wifi_mask(mask);
 ret=rtl_fe_mask_write(&io,60,mask);
 if(ret)return ret;
 rtl_fe_bridge_mask(mask);
 rtl_fe_bits_set(mask,92,2,0); /* Exact learned inner DEI/PCP. */
 rtl_fe_bits_set(mask,96,2,0); /* Exact learned outer DEI/PCP. */
 ret=rtl_fe_mask_write(&io,59,mask);
 if(ret)return ret;
 rtl_fe_wifi_mask(mask);
 ret=rtl_fe_mask_write(&io,58,mask);
 if(ret)return ret;
 rtl_fe_bits_set(mask,135,8,255); /* Wired PPPoE data retains no VIF selector. */
 rtl_fe_bits_set(mask,88,1,0); /* Match the CAM protocol encoding; LCP misses. */
 ret=rtl_fe_mask_write(&io,57,mask);
 if(ret)return ret;
 rtl_fe_wifi_mask(mask);
 ret=rtl_fe_mask_write(&io,56,mask);
 if(ret)return ret;
 fe_write(fe,0x3500,0x11); /* Session version/type1, code0. */
 fe_write(fe,0x3504,PPP_IP|PPP_IPV6<<16);
 fe_write(fe,0x351c,(fe_read(fe,0x351c)&~(0x3fU<<16|7U<<24))|BIT(31)|BIT(12)|16U<<16);
 /* TX MIB differences use unsigned wrap, including the32-bit packet count. */
 fe_write(fe,0x3508,fe_read(fe,0x3508)|0x1f00);
 /* Linux must generate TTL/Hop-Limit errors from the original ingress
  * packet; override an accelerated WAN action's deep-Q flag as well. */
 fe_write(fe,0x30cc,(fe_read(fe,0x30cc)&~0xfffU)|0xf21);
 for(i=0;i<2;i++) {
  rtl_fe_classify_hash(cls,0,i?7:0);
  /* LAN0..4 share a connector, but CPU misses and flow keys must retain
   * the real ingress port. PON keeps its logical source7. */
  if(!i)rtl_fe_bits_set(cls,208,1,0);
  ret=rtl_fe_classifier_write(&io,1024+4*i,cls);
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
 fe_write(fe,0x3400,(fe_read(fe,0x3400)&~(BIT(0)|BIT(10)))|BIT(3)|BIT(2)|BIT(19));
 fe_write(fe,0x3404,7|(25<<8)|(1<<14)|(24<<16)|(2<<22)|(3<<24));
 fe_write(fe,0x2300,fe_read(fe,0x2300)|BIT(12)|BIT(13));
 fe_write(fe,0x2304+12*12,0xff|(6<<16));
 fe_write(fe,0x2304+13*12,0xff|(6<<16));
 for(i=0;i<5;i++)fe_write(fe,0x394c+i*4,0);
 fe_write(fe,0x3724,RTL_FE_ACTION_GROUPS|(3U<<25));
 fe_write(fe,0x3704,63); /* Stock FC five-tuple priority0. */
 fe_write(fe,0x3708,62|BIT(8)); /* Wi-Fi identity tuple, priority1. */
 fe_write(fe,0x370c,61|(2U<<8)); /* Transparent Ethernet, priority2. */
 fe_write(fe,0x3710,60|(3U<<8)); /* Per-VIF transparent Ethernet, priority3. */
 fe_write(fe,0x3714,59|(4U<<8));
 fe_write(fe,0x3718,58|(5U<<8));
 fe_write(fe,0x371c,57|(6U<<8));
 fe_write(fe,0x3720,56|(7U<<8));
 fe_write(fe,0x3700,8);
 fe_write(fe,0x3860,0x020d0000);
 fe_write(fe,0x3278,0x88a88100);
 fe_write(fe,0x327c,0x92009100);
 fe_write(fe,0x3280,0x77); /* Original parser TPID defaults. */
 for(i=0;i<3;i++) {
  ret=rtl_fe_cam_protocol_get(&io,&fe->protocol_cam[i],i?8:2,
   i==0?ETH_P_PPP_SES:i==1?PPP_IP:PPP_IPV6);
  if(ret<0)return ret;
  if(i)fe->ppp_protocol_ids[i-1]=ret;
 }
 /* The Linux rule already selected the physical LAN output. A second
  * L2 lookup would override it with the indexed DA entry's forwarding port.
  * NI demux3 enters L2TM directly; only LAN0..4's selectors change. */
 fe_write(fe,0xa1e0,fe_read(fe,0xa1e0)|0x3ff);
 rtl_fe_cache_start(&io);
 return 0;
}
static int fe_restore(struct rtl_fe *fe)
{
 struct rtl_dq_io io=fe_io(fe);
 unsigned int i,side;
 int ret;

 if(!fe->prepared)return 0;
 fe_write(fe,0x11e0,fe->wifi_ippb_saved);
 ret=fe_access(fe,0x11dc,BIT(31)|BIT(30)|RTL_WIFI_SOURCE);
 if(ret)return ret;
 fe_write(fe,0x38c4,0);
 ret=fe_access(fe,0x393c,1);
 if(ret)return ret;
 for(i=0;i<3;i++) {
  ret=rtl_fe_cam_protocol_put(&io,&fe->protocol_cam[i]);
  if(ret)return ret;
 }
 for(i=0;i<2;i++) {
  for(side=0;side<4;side++) {
   fe_write(fe,0x1658,fe->map_saved[i][side]);
   ret=fe_access(fe,0x1654,BIT(31)|BIT(30)|(24+i)|(side<<6));
   if(ret)return ret;
  }
  ret=rtl_fe_classifier_write(&io,1024+4*i,fe->classifier_saved[i]);
  if(ret)return ret;
 }
 ret=rtl_fe_mask_write(&io,63,fe->mask_saved);
 if(ret)return ret;
 ret=rtl_fe_mask_write(&io,62,fe->wifi_mask_saved);
 if(ret)return ret;
 for(i=0;i<6;i++) {
  ret=rtl_fe_mask_write(&io,61-i,fe->bridge_mask_saved[i]);
  if(ret)return ret;
 }
 for(i=0;i<ARRAY_SIZE(fe_registers);i++)fe_write(fe,fe_registers[i],fe->saved[i]);
 for(i=0;i<FE_LAN_PORTS;i++)fe_write(fe,FE_LAN_RX_CTRL(i),fe->lan_saved[i]);
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
 * TYFZA1 has independent MIBs at0..31;32..63 read the same aggregate.
 * Counted flows own one interface MIB. Further flows share its MAC selection
 * with their action counter bit clear, so they cannot pollute that flow MIB. */
static int fe_interface_get(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_fe_interface *interface;
 unsigned int index=find_first_zero_bit(fe->counters,FE_INTERFACES),i;
 int ret;

 if(index==FE_INTERFACES) {
  for(i=0;i<FE_INTERFACES;i++)
   if(fe->interfaces[i].source_mac==flow->source_mac &&
      fe->interfaces[i].pppoe==flow->pppoe &&
      (!fe->interfaces[i].counted || index==FE_INTERFACES)) {
    index=i;
    if(!fe->interfaces[i].counted)break;
   }
  if(index==FE_INTERFACES)return -ENOSPC;
 }
 interface=&fe->interfaces[index];
 if(!interface->users) {
  ret=fe_access(fe,0x3000,BIT(31)|index);
  if(ret)return ret;
  interface->saved=fe_read(fe,0x3004);
  interface->source_mac=flow->source_mac;
  interface->pppoe=flow->pppoe;
  __set_bit(index,fe->counters);
 }
 flow->counter=index;
 flow->counted=index<FE_COUNTERS && !interface->counted;
 interface->counted|=flow->counted;
 interface->users++;
 flow->interface_owned=true;
 if(interface->users==1) {
  /* Exclude Ethernet padding from the PPPoE payload length (SDK EIF bit23). */
  fe_write(fe,0x3004,BIT(23)|BIT(18)|((flow->source_mac+1)<<19)|flow->pppoe);
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
static int fe_mtu_get(struct rtl_fe *fe,struct rtl_fe_flow *flow,unsigned int size)
{
 unsigned int i,index=16,address,shift,value;
 struct rtl_fe_mtu *mtu;

 for(i=0;i<16;i++) {
  if(fe->mtus[i].users && fe->mtus[i].size==size){index=i;break;}
  if(!fe->mtus[i].users && index==16)index=i;
 }
 if(index==16)return -ENOSPC;
 mtu=&fe->mtus[index];
 if(!mtu->users) {
  address=0x3520+(index/2)*4;shift=(index%2)*16;
  value=fe_read(fe,address);
  mtu->saved=(value>>shift)&0x3fff;
  mtu->size=size;
  fe_write(fe,address,(value&~(0x3fffU<<shift))|size<<shift);
 }
 mtu->users++;
 flow->mtu_index=index;flow->mtu_owned=true;
 return 0;
}
static void fe_mtu_put(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 unsigned int index=flow->mtu_index,address=0x3520+(index/2)*4,shift=(index%2)*16;
 struct rtl_fe_mtu *mtu=&fe->mtus[index];

 if(!--mtu->users)
  fe_write(fe,address,(fe_read(fe,address)&~(0x3fffU<<shift))|mtu->saved<<shift);
 flow->mtu_owned=false;
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
 bool ipv6, bridge;
 bool vlan_exact;
 u16 ingress_tci[2];
 u8 ppp_protocol_slot;
 unsigned short ethernet_protocol;
 unsigned int pppoe_session, egress_pppoe, mtu;
 bool pppoe_input;
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
#include "rtl9617c-fe-vlan.h"

static int fe_parse_bridge(struct rtl_fe_port *port,struct flow_rule *rule,struct rtl_fe_rule *data)
{
 struct flow_match_eth_addrs ethernet;
 struct flow_match_basic basic;
 struct flow_match_vlan vlan;
 struct flow_action_entry *act;
 struct net_device *output=NULL;
 bool known[2]={false,false},changed=false;
 unsigned int count,key;
 int i,ret;

 for(key=0;key<FLOW_DISSECTOR_KEY_MAX;key++) {
  if(!flow_rule_match_key(rule,key))continue;
  switch(key) {
  case FLOW_DISSECTOR_KEY_BASIC:case FLOW_DISSECTOR_KEY_ETH_ADDRS:
  case FLOW_DISSECTOR_KEY_META:case FLOW_DISSECTOR_KEY_CONTROL:
  case FLOW_DISSECTOR_KEY_VLAN:case FLOW_DISSECTOR_KEY_CVLAN:
  case FLOW_DISSECTOR_KEY_PPPOE:break;
  default:return -EOPNOTSUPP;
  }
 }
 if(!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_BASIC))return -EOPNOTSUPP;
 flow_rule_match_basic(rule,&basic);
 if(basic.mask->n_proto!=htons(0xffff) || basic.mask->ip_proto)return -EOPNOTSUPP;
 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_CONTROL)) {
  struct flow_match_control control;
  flow_rule_match_control(rule,&control);
  if(control.mask->thoff || control.mask->addr_type || control.mask->flags)return -EOPNOTSUPP;
 }
 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_META)) {
  struct flow_match_meta meta;
  flow_rule_match_meta(rule,&meta);
  if(meta.mask->ingress_iftype || meta.mask->l2_miss || (meta.mask->ingress_ifindex &&
     (meta.mask->ingress_ifindex!=-1 || meta.key->ingress_ifindex!=port->netdev->ifindex)))
   return -EOPNOTSUPP;
 }
 flow_rule_match_eth_addrs(rule,&ethernet);
 for(i=0;i<ETH_ALEN;i++)
  if(ethernet.mask->src[i]!=255 || ethernet.mask->dst[i]!=255)return -EOPNOTSUPP;
 if(!is_valid_ether_addr(ethernet.key->src) || !is_valid_ether_addr(ethernet.key->dst))return -EINVAL;
 data->bridge=true;data->input=port;
 data->ethernet_protocol=ntohs(basic.key->n_proto);
 data->key=(struct rtl_fe_ipv4_key){.ingress=port->source,.llid=port->llid,
  .mask=port->wifi_id?60:61};
 ether_addr_copy(data->source,ethernet.key->src);
 ether_addr_copy(data->destination,ethernet.key->dst);
 if(data->ethernet_protocol==ETH_P_PPP_SES) {
  struct flow_match_pppoe pppoe;
  if(!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_PPPOE))return -EOPNOTSUPP;
  flow_rule_match_pppoe(rule,&pppoe);
  if(pppoe.mask->session_id!=htons(0xffff) || pppoe.mask->ppp_proto ||
     (pppoe.mask->type && (pppoe.mask->type!=htons(0xffff) ||
     pppoe.key->type!=htons(ETH_P_PPP_SES))))return -EOPNOTSUPP;
  data->pppoe_input=true;data->pppoe_session=ntohs(pppoe.key->session_id);
 } else if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_PPPOE))return -EOPNOTSUPP;
 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_VLAN)) {
  flow_rule_match_vlan(rule,&vlan);
  ret=fe_vlan_match(&vlan,&data->vlan[data->vlan_count++]);
  if(ret)return ret;
 }
 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_CVLAN)) {
  if(!data->vlan_count)return -EOPNOTSUPP;
  flow_rule_match_cvlan(rule,&vlan);
  ret=fe_vlan_match(&vlan,&data->vlan[data->vlan_count++]);
  if(ret)return ret;
 }
 count=data->vlan_count;
 memcpy(data->action.vlan,data->vlan,sizeof(data->vlan));
 flow_action_for_each(i,act,&rule->action) {
  switch(act->id) {
  case FLOW_ACTION_REDIRECT:output=act->dev;break;
  case FLOW_ACTION_VLAN_POP:
   if(!count)return -EOPNOTSUPP;
   data->action.vlan[0]=data->action.vlan[1];known[0]=known[1];
   count--;changed=true;break;
  case FLOW_ACTION_VLAN_PUSH:
   if(count==2 || act->vlan.vid>VLAN_VID_MASK || act->vlan.prio>7)return -EOPNOTSUPP;
   ret=fe_vlan_tpid(act->vlan.proto);
   if(ret<0)return ret;
   data->action.vlan[1]=data->action.vlan[0];known[1]=known[0];
   data->action.vlan[0]=(struct rtl_fe_vlan){.vid=act->vlan.vid,.tpid=ret,.priority=act->vlan.prio};
   known[0]=true;count++;changed=true;break;
  default:return -EOPNOTSUPP;
  }
 }
 if(changed)for(i=0;i<count;i++)if(!known[i])return -EOPNOTSUPP;
 data->output=fe_port(port->fe,output);
 if(!data->output || data->input==data->output)return -EOPNOTSUPP;
 data->action.bridge=1;
 data->action.destination=data->output->destination;
 data->action.deep_queue=data->output->destination>=32;
 data->action.check_mask=data->key.mask;
 data->action.vlan_count=count;data->action.vlan_set=changed;
 data->ethernet_bytes=ETH_HLEN+VLAN_HLEN*count+(data->pppoe_input?8:0);
 return 0;
}

static int fe_parse(struct rtl_fe_port *port,struct flow_cls_offload *cls,struct rtl_fe_rule *data)
{
 struct flow_rule *rule=flow_cls_offload_flow_rule(cls);
 struct flow_match_meta meta;
 struct flow_match_basic basic;
 struct flow_match_pppoe pppoe;
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
 __be16 protocol;

 if(flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_ETH_ADDRS)) {
  ret=fe_parse_bridge(port,rule,data);
  if(ret)return ret;
  goto itu_paths;
 }
 if(!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_META) ||
    !flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_BASIC) ||
    (!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_IPV4_ADDRS) &&
     !flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_IPV6_ADDRS)) ||
    !flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_PORTS))return -EOPNOTSUPP;
 flow_rule_match_meta(rule,&meta);
 if(meta.key->ingress_ifindex!=port->netdev->ifindex)return -EOPNOTSUPP;
 flow_rule_match_basic(rule,&basic);
 protocol=basic.key->n_proto;
 if(protocol==htons(ETH_P_PPP_SES)) {
  if(!flow_rule_match_key(rule,FLOW_DISSECTOR_KEY_PPPOE))return -EOPNOTSUPP;
  flow_rule_match_pppoe(rule,&pppoe);
  if(pppoe.mask->session_id!=htons(0xffff) || pppoe.mask->ppp_proto!=htons(0xffff) ||
     pppoe.mask->type!=htons(0xffff) || pppoe.key->type!=htons(ETH_P_PPP_SES))return -EOPNOTSUPP;
  if(pppoe.key->ppp_proto==htons(PPP_IP))protocol=htons(ETH_P_IP);
  else if(pppoe.key->ppp_proto==htons(PPP_IPV6))protocol=htons(ETH_P_IPV6);
  else return -EOPNOTSUPP;
  data->pppoe_input=true;data->pppoe_session=ntohs(pppoe.key->session_id);
 }
 data->ipv6=protocol==htons(ETH_P_IPV6);
 if((protocol!=htons(ETH_P_IP) && !data->ipv6) || basic.mask->n_proto!=htons(0xffff) ||
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
  case FLOW_ACTION_PPPOE_PUSH:
   if(!act->pppoe.sid || !act->pppoe.mtu || act->pppoe.mtu>0x3fff)return -EOPNOTSUPP;
   data->egress_pppoe=3U|((unsigned int)act->pppoe.sid<<2);
   data->mtu=act->pppoe.mtu;
   break;
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
 data->action=(struct rtl_fe_ipv4_action){.destination=data->output->destination,.check_mask=port->wifi_id?62:63,
  .deep_queue=data->output->destination>=32,.translate=source_changed||destination_changed,
  .translate_destination=destination_changed,.address=ntohl(destination_changed?new_destination:new_source),
  .port=ntohs(destination_changed?new_dport:new_sport)};
 data->action.vlan_set=vlan_changed;
 data->action.pppoe_pop=data->pppoe_input && !data->egress_pppoe;
 data->action.vlan_count=output_vlan_count;
 memcpy(data->action.vlan,output_vlan,sizeof(output_vlan));
 data->ethernet_bytes=ETH_HLEN+VLAN_HLEN*output_vlan_count+(data->egress_pppoe?8:0);
itu_paths:
 if(port->fe->itu_paths) {
  bool mapped=port->fe->itu_paths->profile_count!=0;
  for(i=0;!mapped && i<(int)port->fe->itu_paths->count;i++)mapped=port->fe->itu_paths->path[i].unmarked_mode!=0;
  if(mapped)return fe_itu_rule(data);
 }
 if(port->netdev==port->fe->itu_netdev) {
  const struct rtl_itu_path *path=rtl_itu_path_downstream(port->fe->itu_paths,
   data->vlan_count?data->vlan[0].vid:65535,255);
  if(!path)return -EOPNOTSUPP;
  data->key.llid=path->index;data->key6.llid=path->index;
 }
 if(data->output->netdev==port->fe->itu_netdev) {
  const struct rtl_itu_path *path=rtl_itu_path_flow(port->fe->itu_paths,
   data->action.vlan_count?data->action.vlan[0].vid:65535);
  if(!path)return -EOPNOTSUPP;
  data->action.destination=32+path->tcont;
  data->action.deep_queue=0;
  data->action.cos=path->queue;
  data->action.gem_set=1;data->action.gem_index=path->index;
 }
 return 0;
}

static int fe_delete(struct rtl_fe *fe,struct rtl_fe_flow *flow)
{
 struct rtl_dq_io io=fe_io(fe);
 int ret;

 flow->deleting=true;
 if(flow->published) {
  ret=rtl_fe_main_age(&io,flow->hash_index,0);
  if(ret)goto retry;
  ret=rtl_fe_cache_command(&io,flow->hash_index,flow->crc16,1);
  if(ret)goto retry;
  flow->published=false;
 }
 ((__le32 *)fe->memory)[flow->hash_index]=0;
 dma_wmb();
 if(flow->destination_owned) {
  ret=fe_destination_put(fe,flow);
  if(ret)goto retry;
  flow->destination_owned=false;
 }
 if(flow->source_owned) {
  ret=fe_source_put(fe,flow->source_mac);
  if(ret)goto retry;
  flow->source_owned=false;
 }
 if(flow->interface_owned) {
  ret=fe_interface_put(fe,flow);
  if(ret)goto retry;
  flow->interface_owned=false;
 }
 if(flow->mtu_owned)fe_mtu_put(fe,flow);
 __clear_bit(flow->hash_index,fe->hashes);
 list_del(&flow->list);
 if(flow->installed)fe->deletes++;
 kfree(flow);
 return 0;
retry:
 mod_delayed_work(system_wq,&fe->cleanup_work,HZ);
 return ret;
}
static void fe_cleanup_work(struct work_struct *work)
{
 struct rtl_fe *fe=container_of(to_delayed_work(work),struct rtl_fe,cleanup_work);
 struct rtl_fe_flow *flow,*next;

 mutex_lock(&fe->lock);
 list_for_each_entry_safe(flow,next,&fe->flows,list)
  if(flow->deleting)fe_delete(fe,flow);
 fe_collect_ports(fe);
 mutex_unlock(&fe->lock);
}
static int fe_install(struct rtl_fe_port *port,struct rtl_fe_rule data,unsigned long cookie)
{
 struct rtl_fe *fe=port->fe;
 struct rtl_dq_io io=fe_io(fe);
 struct rtl_fe_flow *flow;
 unsigned int words[23],action[8],crc32,index,start,counters[7],i;
 u16 crc16;
 int ret;

 if(port->retired)return -ENODEV;
 if(!fe->enabled || !netif_running(data.input->netdev) || !netif_running(data.output->netdev) ||
    !netif_carrier_ok(data.input->netdev) || !netif_carrier_ok(data.output->netdev))return -EAGAIN;
 if(fe_flow(fe,cookie))return -EEXIST;
 if(data.bridge)rtl_fe_bridge_key(words,data.source,data.destination,data.ethernet_protocol,
  data.key.ingress,data.key.llid,data.key.mask);
 else if(data.ipv6)rtl_fe_ipv6_key(words,&data.key6);
 else rtl_fe_ipv4_key(words,&data.key);
 rtl_fe_vlan_key(words,data.vlan,data.vlan_count);
 if(data.vlan_exact)for(i=0;i<data.vlan_count;i++) {
  rtl_fe_bits_set(words,i?426:445,1,!!(data.ingress_tci[i]&VLAN_CFI_MASK));
  rtl_fe_bits_set(words,i?427:446,3,data.ingress_tci[i]>>VLAN_PRIO_SHIFT);
 }
 rtl_fe_key_set(words,RTL_FE_KEY_PPPOE_SESSION_ID,data.pppoe_session);
 rtl_fe_key_set(words,RTL_FE_KEY_PPPOE_TYPE,data.pppoe_input?2:0);
 if(data.vlan_exact && data.pppoe_input)rtl_fe_bits_set(words,400,4,data.ppp_protocol_slot);
 rtl_fe_key_set(words,RTL_FE_KEY_ORIG_LSPID,0);
 rtl_fe_key_set(words,RTL_FE_KEY_CTRL_SET_ID,0);
 rtl_fe_key_set(words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_key_set(words,RTL_FE_KEY_MDATA_BYTE,port->wifi_id);
 rtl_fe_key_crc(words,&crc32,&crc16);
 start=rtl_fe_hash_bucket(crc16);
 for(index=start;index<start+32;index++)if(!test_bit(index,fe->hashes))break;
 if(index==start+32)return -ENOSPC;
 flow=kzalloc(sizeof(*flow),GFP_KERNEL);
 if(!flow)return -ENOMEM;
 flow->cookie=cookie;flow->hash_index=index;flow->crc16=crc16;
 flow->input=data.input;flow->output=data.output;flow->lastused=jiffies;
 flow->ethernet_bytes=data.ethernet_bytes;
 flow->pppoe=data.egress_pppoe;
 flow->ingress_session=data.pppoe_session;
 flow->ingress_pol=data.key.llid;
 flow->ethernet_protocol=data.ethernet_protocol;
 ether_addr_copy(flow->ethernet_source,data.source);
 flow->protocol=data.key.protocol;flow->ipv6=data.ipv6;
 flow->bridge=data.bridge;
 flow->source_port=data.key.source_port;flow->destination_port=data.key.destination_port;
 for(i=0;i<(data.ipv6?4:1);i++) {
  put_unaligned_be32(data.ipv6?data.key6.source[i]:data.key.source,flow->ip_source+i*4);
  put_unaligned_be32(data.ipv6?data.key6.destination[i]:data.key.destination,flow->ip_destination+i*4);
 }
 __set_bit(index,fe->hashes);
 list_add_tail(&flow->list,&fe->flows);
 if(!data.bridge) {
  ret=fe_source_get(fe,data.source,&flow->source_mac);
  if(ret)goto rollback;
  flow->source_owned=true;
  ret=fe_destination_get(fe,flow,data.destination);
  if(ret)goto rollback;
  ret=fe_interface_get(fe,flow);
  if(ret)goto rollback;
 } else ether_addr_copy(flow->destination,data.destination);
 if(data.mtu) {
  ret=fe_mtu_get(fe,flow,data.mtu);
  if(ret)goto rollback;
  data.action.mtu_set=1;data.action.mtu_index=flow->mtu_index;
 }
 if(flow->counted) {
  ret=rtl_fe_tx_counter_read(&io,flow->counter,counters);
  if(ret)goto rollback;
  flow->bytes=(u64)counters[1]<<32|counters[0];flow->packets=counters[2]+counters[3];
 }
 data.action.mac_da_index=flow->destination_index;
 data.action.egress_interface=flow->counter;
 { struct rtl_fe_wifi_peer *peer=fe_wifi_peer(fe,data.output,data.destination);
   if(peer)data.action.destination=peer->destination; }
 rtl_fe_ipv4_action(action,&data.action);
 if(data.input->wifi_id || data.output->wifi_id) {
  rtl_fe_action_set(action,RTL_FE_MDATA_BYTE_VLD,1);
  rtl_fe_action_set(action,RTL_FE_MDATA_BYTE,data.output->wifi_id);
 }
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
static int fe_replace(struct rtl_fe_port *port,struct flow_cls_offload *cls)
{
 struct rtl_fe_rule data={};
 int ret=fe_parse(port,cls,&data);

 return ret?ret:fe_install(port,data,cls->cookie);
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
 bytes=(u64)words[1]<<32|words[0];packets=words[2]+words[3];
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

 if(type!=TC_SETUP_CLSFLOWER || cls->common.chain_index)return -EOPNOTSUPP;
 mutex_lock(&fe->lock);
 list_for_each_entry_safe(flow,next,&fe->flows,list)
  if(!flow->installed || flow->deleting) {
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
static void fe_block_release(void *private)
{
 struct rtl_fe_port *port=private;
 struct rtl_fe *fe=port->fe;

 mutex_lock(&fe->lock);
 port->bindings--;
 fe_collect_ports(fe);
 mutex_unlock(&fe->lock);
}
int rtl_fe_setup_tc(struct rtl_fe *fe,struct net_device *netdev,enum tc_setup_type type,void *data)
{
 struct rtl_fe_port *port,*candidate;
 struct flow_block_offload *block=data;
 struct flow_block_cb *callback;
 int ret;

 if(!fe || (type!=TC_SETUP_FT && type!=TC_SETUP_BLOCK))return -EOPNOTSUPP;
 if(block->binder_type!=FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS)return -EOPNOTSUPP;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 if(!port && block->command==FLOW_BLOCK_UNBIND)
  list_for_each_entry(candidate,&fe->ports,list)
   if(candidate->retired && candidate->netdev==netdev){port=candidate;break;}
 if(!port){ret=-ENODEV;goto unlock;}
 block->driver_block_list=&fe->blocks;
 callback=flow_block_cb_lookup(block->block,fe_block_callback,port);
 switch(block->command) {
 case FLOW_BLOCK_BIND:
  /* fw4 probes a second flowtable while the active one remains bound. */
  if(callback){flow_block_cb_incref(callback);ret=0;break;}
  callback=flow_block_cb_alloc(fe_block_callback,port,port,fe_block_release);
  if(IS_ERR(callback)){ret=PTR_ERR(callback);break;}
  flow_block_cb_incref(callback);
  flow_block_cb_add(callback,block);
  list_add_tail(&callback->driver_list,&fe->blocks);
  port->bindings++;
  ret=0;
  break;
 case FLOW_BLOCK_UNBIND:
  if(!callback){ret=-ENOENT;break;}
  if(!flow_block_cb_decref(callback)) {
   flow_block_cb_remove(callback,block);
   list_del(&callback->driver_list);
  }
  ret=0;
  break;
 default:ret=-EOPNOTSUPP;break;
 }
unlock:
 mutex_unlock(&fe->lock);
 return ret;
}

static int fe_counters_show(struct seq_file *file,void *unused)
{
 struct rtl_fe *fe=file->private;
 struct rtl_dq_io io=fe_io(fe);
 struct rtl_fe_flow *flow;
 unsigned int words[7],age,interface,destination;
 int ret;

 mutex_lock(&fe->lock);
 seq_puts(file,"hash input output pol_id dpid ingress_sid egress_sid age protocol source destination counter bytes packets drop_bytes drop_packets egress_word\n");
 list_for_each_entry(flow,&fe->flows,list) {
  if(!flow->published)continue;
  ret=rtl_fe_main_age_read(&io,flow->hash_index,&age);
  if(!ret && flow->interface_owned)ret=fe_access(fe,0x3000,BIT(31)|flow->counter);
  if(ret){seq_printf(file,"%u error=%d\n",flow->hash_index,ret);continue;}
  interface=flow->interface_owned?fe_read(fe,0x3004):0;
  destination=le32_to_cpu(((__le32 *)(fe->memory+RTL_FE_ACTION_OFFSET+
                                      flow->hash_index*RTL_FE_ACTION_BYTES))[0])>>8&1023;
  seq_printf(file,"%u %s %s %u %u %u %u %u ",flow->hash_index,
             flow->input->netdev->name,flow->output->netdev->name,
             flow->ingress_pol,destination,flow->ingress_session,flow->pppoe>>2,age);
  if(flow->bridge)
   seq_printf(file,"%04x %pM %pM ",flow->ethernet_protocol,
              flow->ethernet_source,flow->destination);
  else if(flow->ipv6)
   seq_printf(file,"%u [%pI6c]:%u [%pI6c]:%u ",flow->protocol,
              flow->ip_source,flow->source_port,flow->ip_destination,flow->destination_port);
  else
   seq_printf(file,"%u %pI4:%u %pI4:%u ",flow->protocol,
              flow->ip_source,flow->source_port,flow->ip_destination,flow->destination_port);
  if(!flow->interface_owned) {
   seq_puts(file,"- - - - - -\n");
   continue;
  }
  if(!flow->counted) {
   seq_printf(file,"- - - - - %08x\n",interface);
   continue;
  }
  ret=rtl_fe_tx_counter_read(&io,flow->counter,words);
  if(ret){seq_printf(file,"error=%d\n",ret);continue;}
  seq_printf(file,"%u %llu %u %llu %u %08x\n",flow->counter,
             (u64)words[1]<<32|words[0],words[2]+words[3],
             (u64)words[5]<<32|words[4],words[6],interface);
 }
 mutex_unlock(&fe->lock);
 return 0;
}
DEFINE_SHOW_ATTRIBUTE(fe_counters);

struct rtl_fe *rtl_fe_allocate(struct device *device,void __iomem *registers)
{
 void __iomem *reo=devm_platform_ioremap_resource_byname(to_platform_device(device),"l3fe-axi-reorder");
 struct rtl_fe *fe;
 unsigned int *defaults,i;

 if(IS_ERR(reo))return ERR_CAST(reo);
 fe=kzalloc(sizeof(*fe),GFP_KERNEL);
 if(!fe)return ERR_PTR(-ENOMEM);
 fe->device=device;fe->registers=registers;
 mutex_init(&fe->lock);
 INIT_DELAYED_WORK(&fe->cleanup_work,fe_cleanup_work);
 INIT_LIST_HEAD(&fe->ports);INIT_LIST_HEAD(&fe->flows);INIT_LIST_HEAD(&fe->blocks);
 INIT_LIST_HEAD(&fe->wifi_peers);
 fe->memory=dma_alloc_coherent(device,RTL_FE_MEMORY_BYTES,&fe->dma,GFP_KERNEL);
 if(!fe->memory){kfree(fe);return ERR_PTR(-ENOMEM);}
 defaults=fe->memory+RTL_FE_DEFAULT_OFFSET;
 for(i=0;i<32;i++)rtl_fe_cpu_miss(defaults+i*4);
 dma_wmb();
 fe->debug=debugfs_create_dir("rtl9617c-fe",NULL);
 debugfs_create_file("flows",0400,fe->debug,fe,&fe_counters_fops);
 fe->bridge=fe_bridge_start(fe);
 if(IS_ERR(fe->bridge)) {
  int ret=PTR_ERR(fe->bridge);
  debugfs_remove_recursive(fe->debug);
  dma_free_coherent(fe->device,RTL_FE_MEMORY_BYTES,fe->memory,fe->dma);
  kfree(fe);
  return ERR_PTR(ret);
 }
 /* aal_l3fe_axi_reo_init: Hash uses instance1. ID15 avoids the stock
  * main-Hash hang; default actions must remain in DMA memory as well. */
 writel(15,reo+0x80);
 writel(BIT(31)|8,reo+0x84);
 writel(BIT(28),reo+0x88);
 writel(BIT(28),reo+0x8c);
 writel(BIT(31)|9,reo+0x90);
 readl(reo+0x90);
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
int rtl_fe_add_wifi_port(struct rtl_fe *fe,struct net_device *netdev,unsigned int id)
{
 struct rtl_fe_port *port;
 int ret;

 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 if(port)port->wifi_id=id;
 mutex_unlock(&fe->lock);
 if(port)return 0;
 ret=rtl_fe_add_port(fe,netdev,RTL_WIFI_SOURCE,16,0);
 if(ret)return ret;
 mutex_lock(&fe->lock);
 fe_port(fe,netdev)->wifi_id=id;
 mutex_unlock(&fe->lock);
 return 0;
}

bool rtl_fe_enabled(struct rtl_fe *fe)
{
 return fe && READ_ONCE(fe->enabled);
}

int rtl_fe_wifi_peer_route(struct rtl_fe *fe,struct net_device *netdev,const u8 *address,unsigned int destination)
{
 struct rtl_fe_port *port;
 struct rtl_fe_wifi_peer *peer;
 struct rtl_fe_flow *flow;
 struct rtl_dq_io io=fe_io(fe);
 int ret=0;

 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 if(!port || !port->wifi_id){ret=-ENODEV;goto unlock;}
 peer=fe_wifi_peer(fe,port,address);
 if(destination && !peer) {
  peer=kzalloc(sizeof(*peer),GFP_KERNEL);
  if(!peer){ret=-ENOMEM;goto unlock;}
  peer->port=port;ether_addr_copy(peer->address,address);
  list_add_tail(&peer->list,&fe->wifi_peers);
 }
 if(destination)peer->destination=destination;
 list_for_each_entry(flow,&fe->flows,list) {
  u32 *action;
  if(!flow->installed || flow->deleting || flow->output!=port ||
     !ether_addr_equal(flow->destination,address))continue;
  /* Withdraw both lookup locations before changing an in-flight flow's output. */
  ret=rtl_fe_main_age(&io,flow->hash_index,0);
  if(ret)goto unlock;
  ret=rtl_fe_cache_command(&io,flow->hash_index,flow->crc16,1);
  if(ret)goto unlock;
  action=fe->memory+RTL_FE_ACTION_OFFSET+flow->hash_index*RTL_FE_ACTION_BYTES;
  rtl_fe_action_set(action,RTL_FE_MCGID,destination?destination:port->destination);
  dma_wmb();
  ret=rtl_fe_main_age(&io,flow->hash_index,1);
  if(ret)goto unlock;
 }
 if(!destination && peer){list_del(&peer->list);kfree(peer);}
unlock:
 mutex_unlock(&fe->lock);
 return ret;
}

void rtl_fe_remove_port(struct rtl_fe *fe,struct net_device *netdev)
{
 struct rtl_fe_port *port;
 struct rtl_fe_flow *flow,*next;

 if(!fe)return;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 if(port) {
  fe_wifi_forget_port(fe,port);
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
int rtl_fe_flush_port(struct rtl_fe *fe,struct net_device *netdev)
{
 struct rtl_fe_port *port;
 struct rtl_fe_flow *flow,*next;
 int ret=0;

 if(!fe)return 0;
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 fe_wifi_forget_port(fe,port);
 list_for_each_entry_safe(flow,next,&fe->flows,list)
  if(flow->input==port || flow->output==port) {
   ret=fe_delete(fe,flow);
   if(ret){fe->error=ret;break;}
  }
 mutex_unlock(&fe->lock);
 return ret;
}
int rtl_fe_set_itu_paths(struct rtl_fe *fe,struct net_device *netdev,const struct rtl_itu_paths *paths)
{
 struct rtl_itu_paths *copy=paths?kvmalloc(rtl_itu_paths_bytes(paths),GFP_KERNEL):NULL;
 struct rtl_fe_flow *flow,*next;
 struct rtl_fe_port *port;
 int ret=0;

 if(paths && !copy)return -ENOMEM;
 if(copy)memcpy(copy,paths,rtl_itu_paths_bytes(paths));
 mutex_lock(&fe->lock);
 port=fe_port(fe,netdev);
 list_for_each_entry_safe(flow,next,&fe->flows,list)
  if(flow->input==port || flow->output==port) {
   ret=fe_delete(fe,flow);
   if(ret)goto unlock;
  }
 kvfree(fe->itu_paths);
 fe->itu_paths=copy;copy=NULL;
 fe->itu_netdev=paths?netdev:NULL;
unlock:
 mutex_unlock(&fe->lock);
 kvfree(copy);
 return ret;
}
static int fe_disable_locked(struct rtl_fe *fe)
{
 struct rtl_fe_flow *flow,*next;
 unsigned int i;
 int ret=0;

 fe->enabled=false;
 if(fe->prepared) {
  for(i=0;i<FE_LAN_PORTS;i++)fe_write(fe,FE_LAN_RX_CTRL(i),fe->lan_saved[i]);
  usleep_range(1000,2000);
  list_for_each_entry_safe(flow,next,&fe->flows,list) {
   ret=fe_delete(fe,flow);
   if(ret)return ret;
  }
  ret=fe_restore(fe);
 }
 fe_collect_ports(fe);
 return ret;
}
int rtl_fe_enable(struct rtl_fe *fe)
{
 unsigned int i;
 int ret;

 if(!fe)return -EOPNOTSUPP;
 mutex_lock(&fe->lock);
 if(fe->enabled){ret=0;goto unlock;}
 /* An unfinished rollback still owns the original register snapshot. */
 if(fe->prepared) {
  ret=fe_disable_locked(fe);
  if(ret)goto unlock;
 }
 ret=fe_prepare(fe);
 if(ret){fe_restore(fe);goto unlock;}
 /* The DMA tables, miss actions and port maps exist before admitting LAN. */
 for(i=0;i<FE_LAN_PORTS;i++)
  fe_write(fe,FE_LAN_RX_CTRL(i),(fe_read(fe,FE_LAN_RX_CTRL(i))&~(63U<<16))|(25U<<16));
 fe->enabled=true;
unlock:
 fe->error=ret;
 mutex_unlock(&fe->lock);
 return ret;
}
int rtl_fe_disable(struct rtl_fe *fe)
{
 int ret;

 if(!fe)return 0;
 mutex_lock(&fe->lock);
 ret=fe_disable_locked(fe);
 fe->error=ret;
 mutex_unlock(&fe->lock);
 return ret;
}
void rtl_fe_free(struct rtl_fe *fe)
{
 struct rtl_fe_port *port,*next;
 struct rtl_fe_flow *flow,*flow_next;

 if(!fe)return;
 fe_bridge_stop(fe->bridge);
 debugfs_remove_recursive(fe->debug);
 rtl_fe_disable(fe);
 cancel_delayed_work_sync(&fe->cleanup_work);
 list_for_each_entry_safe(flow,flow_next,&fe->flows,list){list_del(&flow->list);kfree(flow);}
 list_for_each_entry(port,&fe->ports,list)fe_wifi_forget_port(fe,port);
 list_for_each_entry_safe(port,next,&fe->ports,list){list_del(&port->list);kfree(port);}
 dma_free_coherent(fe->device,RTL_FE_MEMORY_BYTES,fe->memory,fe->dma);
 kvfree(fe->itu_paths);
 kfree(fe);
}
ssize_t rtl_fe_state(struct rtl_fe *fe,char *buffer)
{
 ssize_t length;
 struct rtl_fe_flow *flow;
 unsigned int flows=0,counted=0,bridged=0,learned=0;

 if(!fe)return sysfs_emit(buffer,"available=0\n");
 mutex_lock(&fe->lock);
 list_for_each_entry(flow,&fe->flows,list)if(flow->published) {
  flows++;
  counted+=flow->counted;
  bridged+=flow->bridge;learned+=flow->learned;
 }
 length=sysfs_emit(buffer,"available=1 enabled=%u prepared=%u error=%d flows=%u hash_entries=%u counter_slots=%u counted_flows=%u activity_flows=%u installs=%llu deletes=%llu rejected=%llu bridge_rules=%u learned_bridge_rules=%u\n",
  fe->enabled,fe->prepared,fe->error,flows,RTL_FE_HASH_ENTRIES,
  FE_COUNTERS,counted,flows-counted,fe->installs,fe->deletes,fe->misses,bridged,learned);
 mutex_unlock(&fe->lock);
 return length;
}

#include "rtl9617c-fe-bridge.h"
