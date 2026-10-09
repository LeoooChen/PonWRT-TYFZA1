// SPDX-License-Identifier: GPL-2.0-or-later
/* Prepare native DMA Hash/action/miss tables while data ingress bypasses FE. */
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/etherdevice.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/udp.h>
#include <linux/delay.h>
#include <linux/workqueue.h>
#include <net/ip.h>
#include <net/checksum.h>
#include <net/ip6_checksum.h>
#include <linux/unaligned.h>
#include "rtl9617c-ni.h"
#include "rtl9617c-pon.h"
#include "rtl9617c-fe-pipeline.h"
#include "rtl9617c-fe-table.h"

static const unsigned int regs[] = {
 0x3834,0x3838,0x383c,0x3840,0x3844,0x3848,0x384c,0x3850,
 0x3854,0x3858,0x385c,0x38a0,0x38b8,0x38bc,0x38c4,0x3924,0x3724,
 0x3400,0x3404,0x3414,0x3418,0x341c,0x3420,0x3424,0x3428,0x3700,0x3704,0x3860,
 0x394c,0x3950,0x3954,0x3958,0x395c,
 0x2300,0x23a0,0x23a4,0x30b8,0x30c0,0x30c4,0xa5f8,0x1058,0x105c,
};
static struct device *device;
static void __iomem *base;
static void *memory;
static dma_addr_t dma_address;
static unsigned int saved[ARRAY_SIZE(regs)];
static bool configured;
static unsigned int egress=16;
module_param(egress,uint,0600);
static unsigned int lan_redirect=25;
module_param(lan_redirect,uint,0600);
static bool wired_ingress;
module_param(wired_ingress,bool,0600);
static unsigned int control_bypass;
module_param(control_bypass,uint,0600);
static unsigned int lan_frontend=3;
module_param(lan_frontend,uint,0600);
static unsigned int test_age=7;
module_param(test_age,uint,0600);
static unsigned int test_cache_control=1;
module_param(test_cache_control,uint,0600);
static unsigned int test_cache_misc;
module_param(test_cache_misc,uint,0600);
static unsigned int lan_fib_saved[3],lan_icls_saved[5];
static bool lan_fib_configured;
static bool packet_test;
static bool ipv6_test;
module_param(ipv6_test,bool,0600);
static bool mac_test;
module_param(mac_test,bool,0600);
static bool destination_test=true;
module_param(destination_test,bool,0600);
struct test_mac_state {
 bool configured,created;
 unsigned int cam_id,interface_id,cam_saved[5],interface_saved;
 unsigned int counters_before[7];
 u8 destination[6];
};
static struct test_mac_state mac_states[2];
static u8 source_mac[6],destination_mac[6];
static char *source_mac_text="02:66:55:44:33:22";
static char *destination_mac_text="02:77:66:55:44:33";
module_param(source_mac_text,charp,0600);
module_param(destination_mac_text,charp,0600);
static unsigned int test_destination=0xc0a80101,nat_source=0xc6336401;
static unsigned int test_port=18082;
module_param(test_destination,uint,0600);
module_param(nat_source,uint,0600);
module_param(test_port,uint,0600);
static bool reverse_test;
module_param(reverse_test,bool,0600);
static char *reverse_mac_text="00:e0:4c:16:74:b0";
module_param(reverse_mac_text,charp,0600);
static struct rtl9617c_pon *pon;
static struct device *pon_device;
static unsigned int pdc_saved[2][2],wan_map_saved[4];
static bool pdc_configured,wan_map_configured;
static void trace_work_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(trace_work,trace_work_fn);
static unsigned int trace_attempts;
static unsigned int flow_indices[2],flow_crcs16[2];
static bool flow_installed[2];
module_param(packet_test,bool,0600);
static bool pipeline_configured;
static unsigned int mask_saved[8],classifier_saved[2][7],map_saved[4];
static unsigned int reg_read(void *context,unsigned int reg) {return readl(base+reg);}
static void reg_write(void *context,unsigned int reg,unsigned int value) {writel(value,base+reg);}
static int reg_access(void *context,unsigned int reg,unsigned int value)
{
 u32 status;
 unsigned int busy=reg==0x38b0 || reg==0x393c ? 1 : BIT(31);
 writel(value,base+reg);
 return readl_poll_timeout(base+reg,status,!(status&busy),1,100000);
}
static int cache_dma_check(const struct rtl_dq_io *io)
{
 struct rtl_fe_ipv4_action action={.address=0xc6336401,.port=62000,.destination=32,
  .mac_da_index=2,.ingress_interface=0,.egress_interface=1,.cos=0,.translate=1,.deep_queue=1};
 unsigned int expected[8],actual[7],metadata[7],slot,index;
 unsigned int crc32=0x12345678;
 unsigned short crc16=0x0123;
 unsigned int *hash=memory,*actions=memory+RTL_FE_ACTION_OFFSET;
 int ret;

 index=rtl_fe_hash_bucket(crc16);
 rtl_fe_ipv4_action(expected,&action);
 hash[index]=cpu_to_le32(crc32);
 memcpy((u8 *)actions+index*RTL_FE_ACTION_BYTES,expected,sizeof(expected));
 dma_wmb();
 ret=rtl_fe_main_age(io,index,7);
 if(ret)return ret;
 ret=rtl_fe_cache_command(io,index,crc16,2);
 if(ret)return ret;
 io->write(io->context,0x38ac,(index&31)|((unsigned int)crc16<<5)|(3U<<26)|(1U<<24));
 ret=io->indirect(io->context,0x38b0,1);
 if(ret)return ret;
 pr_info("TYFZA1 FE cache allocation status=%08x\n",readl(base+0x38b4));
 if(readl(base+0x38b4)&6)return -EIO;
 for(slot=0;slot<RTL_FE_CACHE_ENTRIES;slot++) {
  ret=rtl_fe_cache_read(io,slot,0,metadata);
  if(ret)return ret;
  if(metadata[0]!=crc32 || (metadata[1]&0x1fff)!=crc16)continue;
  ret=rtl_fe_cache_read(io,slot,1,actual);
  if(ret)return ret;
  if(memcmp(actual,expected,28)) {
   unsigned int i;
   for(i=0;i<7;i++)pr_info("TYFZA1 FE cache action slot%u word%u=%08x expected=%08x\n",slot,i,actual[i],expected[i]);
   pr_info("TYFZA1 FE cache key=%08x/%08x proves main Hash DMA read\n",metadata[0],metadata[1]);
   return -EIO;
  }
  pr_info("TYFZA1 native FE DMA fetch verified:CRC=%08x cache_slot=%u NAT_action224 matches CPU-written table\n",crc32,slot);
  ret=rtl_fe_cache_command(io,index,crc16,1);
  if(ret)return ret;
  ret=rtl_fe_main_age(io,index,0);
  if(ret)return ret;
  hash[index]=0;
  dma_wmb();
  return 0;
 }
 return -ENOENT;
}
static int configure_lan_ingress(const struct rtl_dq_io *io)
{
 unsigned int i,words[5];
 int ret=io->indirect(io->context,0x1b44,0x80000000U);
 if(ret)return ret;
 for(i=0;i<5;i++)lan_icls_saved[i]=io->read(io->context,0x1b58-i*4);
 ret=io->indirect(io->context,0x1e14,0x80000000U);
 if(ret)return ret;
 for(i=0;i<3;i++)lan_fib_saved[i]=io->read(io->context,0x1e20-i*4);
 lan_fib_configured=true;
 memcpy(words,lan_icls_saved,sizeof(lan_icls_saved));
 words[3]=(words[3]&~0x7fcU)|(lan_redirect<<2)|BIT(1);
 words[0]=(words[0]&~((511U<<20)|15U))|(lan_redirect<<20)|BIT(19)|15U;
 if(lan_frontend&1) {
  for(i=0;i<5;i++)io->write(io->context,0x1b58-i*4,words[i]);
  ret=io->indirect(io->context,0x1b44,0xc0000000U);
  if(ret)return ret;
 }
 memcpy(words,lan_fib_saved,sizeof(lan_fib_saved));
 words[1]=(words[1]&~(255U<<8))|(lan_redirect<<8);
 if(lan_frontend&2) {
  for(i=0;i<3;i++)io->write(io->context,0x1e20-i*4,words[i]);
  ret=io->indirect(io->context,0x1e14,0xc0000000U);
  if(ret)return ret;
 }
 lan_fib_configured=true;
 if(control_bypass==1)
  writel(readl(base+0xa5f8)&~BIT(28),base+0xa5f8);
 else if(control_bypass==2)
  writel((readl(base+0xa5f8)&~(63U<<16))|(lan_redirect<<16),base+0xa5f8);
 pr_info("TYFZA1 native FE wired ingress:LAN0 PONCLE destination=%u RXctrl=%08x\n",lan_redirect,readl(base+0xa5f8));
 return 0;
}

static int configure_mac(const struct rtl_dq_io *io,struct test_mac_state *state,
 unsigned int cam_id,unsigned int interface_id,const u8 *source,const u8 *destination,
 unsigned int *destination_index)
{
 unsigned int cam[5]={},fdb[4],result,i;
 int ret;
 state->cam_id=cam_id;state->interface_id=interface_id;
 ether_addr_copy(state->destination,destination);
 ret=rtl_fe_tx_counter_read(io,interface_id,state->counters_before);
 if(ret)return ret;
 ret=rtl_fe_source_mac_read(io,cam_id,state->cam_saved);
 if(ret)return ret;
 ret=io->indirect(io->context,0x3000,0x80000000U|interface_id);
 if(ret)return ret;
 state->interface_saved=io->read(io->context,0x3004);
 state->configured=true;
 for(i=0;i<6;i++)rtl_fe_bits_set(cam,(5-i)*8,8,source[i]);
 cam[1]|=1U<<16;
 ret=rtl_fe_source_mac_write(io,cam_id,cam);
 if(ret)return ret;
 io->write(io->context,0x3004,BIT(18)|((cam_id+1)<<19));
 ret=io->indirect(io->context,0x3000,0xc0000000U|interface_id);
 if(ret)return ret;
 rtl_fe_fdb_key(fdb,destination);
 ret=rtl_fe_fdb_command(io,4,fdb,&result);
 if(ret)return ret;
 pr_info("TYFZA1 FE destination FDB read result=%08x control=%08x\n",result,readl(base+0x1c00));
 if((result&15)!=5) {
  rtl_fe_fdb_key(fdb,destination);
  ret=rtl_fe_fdb_command(io,0x45,fdb,&result);
  if(ret)return ret;
  pr_info("TYFZA1 FE destination FDB append result=%08x\n",result);
  if((result&15)!=5)return -EIO;
  state->created=true;
  rtl_fe_fdb_key(fdb,destination);
  ret=rtl_fe_fdb_command(io,4,fdb,&result);
  if(ret)return ret;
  pr_info("TYFZA1 FE destination FDB lookup after append=%08x data=%08x/%08x/%08x/%08x\n",result,fdb[0],fdb[1],fdb[2],fdb[3]);
  if((result&15)!=5)return -EIO;
 }
 *destination_index=(result>>4)&0x1fff;
 pr_info("TYFZA1 FE MAC setup:source_CAM%u=%pM EIF%u destination_FDB%u=%pM\n",
  cam_id,source,interface_id,*destination_index,destination);
 return 0;
}

static int pdc_access(unsigned int index,bool write)
{
 u32 value;

 writel(BIT(31)|(write?BIT(30):0)|index,pon->mac+0x9020);
 return readl_poll_timeout(pon->mac+0x9020,value,!(value&BIT(31)),1,100000);
}

static int configure_reverse(const struct rtl_dq_io *io)
{
 struct rtl_fe_ipv4_key key={.source=test_destination,.destination=nat_source,
  .source_port=test_port,.destination_port=62000,.protocol=17,.ingress=7,.mask=63};
 struct rtl_fe_ipv4_action action={.address=0x0a172a01,.port=53000,.destination=0,.check_mask=63,
  .translate=1,.translate_destination=1,.egress_interface=30};
 struct net_device *owner=dev_get_drvdata(device);
 unsigned int key_words[RTL_FE_KEY_WORDS],words[8],cls[7],lpb[3],crc32,index,i,da_index;
 unsigned short crc16;
 u8 da[6];
 int ret;

 if(!mac_pton(reverse_mac_text,da))return -EINVAL;
 ret=configure_mac(io,&mac_states[1],13,30,owner->dev_addr,da,&da_index);
 if(ret)return ret;
 action.mac_da_index=da_index;
 rtl_fe_ipv4_action(words,&action);
 rtl_fe_action_set(words,RTL_FE_CACHE_CTRL1,test_cache_control);
 rtl_fe_action_set(words,RTL_FE_L3_IF_COUNTER_EN,1);
 rtl_fe_ipv4_key(key_words,&key);
 rtl_fe_key_set(key_words,RTL_FE_KEY_HKEY_ID,0);
 rtl_fe_key_set(key_words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_key_set(key_words,RTL_FE_KEY_ORIG_LSPID,0);
 rtl_fe_key_crc(key_words,&crc32,&crc16);
 index=rtl_fe_hash_bucket(crc16);
 ((u32 *)memory)[index]=cpu_to_le32(crc32);
 memcpy(memory+RTL_FE_ACTION_OFFSET+index*RTL_FE_ACTION_BYTES,words,sizeof(words));
 dma_wmb();
 ret=rtl_fe_main_age(io,index,test_age);
 if(ret)return ret;
 flow_indices[1]=index;flow_crcs16[1]=crc16;flow_installed[1]=true;
 rtl_fe_classify_hash(cls,0,7);
 ret=rtl_fe_classifier_write(io,1025,cls);
 if(ret)return ret;
 rtl_fe_ingress_lpb(lpb,7,16);
 rtl_fe_bits_set(lpb,42,4,1);
 for(i=0;i<3;i++)writel(lpb[i],base+0x3420+i*4);
 for(i=0;i<4;i++) {
  ret=io->indirect(io->context,0x1654,0x80000000U|24|(i<<6));
  if(ret)return ret;
  wan_map_saved[i]=io->read(io->context,0x1658);
 }
 wan_map_configured=true;
 for(i=0;i<4;i++) {
  io->write(io->context,0x1658,12);
  ret=io->indirect(io->context,0x1654,0xc0000000U|24|(i<<6));
  if(ret)return ret;
 }
 pon_device=bus_find_device_by_name(&platform_bus_type,NULL,"4f5500000.pon");
 if(!pon_device)return -ENODEV;
 pon=dev_get_drvdata(pon_device);
 mutex_lock(&pon->control_lock);
 mutex_lock(&pon->lock);
 if(!rtl_pon_mode_is_epon(pon->mode)||!pon->pdc_configured) {
  ret=-EAGAIN;
  goto unlock;
 }
 for(i=0;i<2;i++) {
  ret=pdc_access(i*128,false);
  if(ret)goto unlock;
  pdc_saved[i][0]=readl(pon->mac+0x9028);
  pdc_saved[i][1]=readl(pon->mac+0x9024);
 }
 pdc_configured=true;
 for(i=0;i<2;i++) {
  writel((pdc_saved[i][0]&~((63U<<3)|BIT(15)))|(24U<<3),pon->mac+0x9028);
  writel(pdc_saved[i][1],pon->mac+0x9024);
  ret=pdc_access(i*128,true);
  if(ret)goto unlock;
 }
 pr_info("TYFZA1 FE reverse static flow:index=%u CRC=%08x/%04x PON LLID0->L3WAN24->LAN0 DNAT=10.23.42.1:53000\n",index,crc32,crc16);
unlock:
 mutex_unlock(&pon->lock);
 mutex_unlock(&pon->control_lock);
 return ret;
}

static int packet_pipeline(const struct rtl_dq_io *io)
{
 struct rtl_fe_ipv4_key key={.source=0x0a172a01,.destination=0xc0a80101,
  .source_port=53000,.destination_port=18082,.protocol=17,.ingress=0,.mask=63};
 struct rtl_fe_ipv4_action action={.address=0xc6336401,.port=62000,.destination=16,.check_mask=63,
  .translate=1,.cos=0};
 struct rtl_fe_ipv6_key key6={.source={0x20010db8,1,0,2},.destination={0x20010db8,2,0,2},
  .source_port=53000,.destination_port=18082,.protocol=17,.mask=63};
 unsigned int mask[8],key_words[RTL_FE_KEY_WORDS],action_words[8],cls[7],lpb[3],crc32,index,i,trace[64];
 unsigned short crc16;
 unsigned int destination_index;
 struct net_device *owner=dev_get_drvdata(device);
 struct sk_buff *skb;
 struct ethhdr *eth;
 struct iphdr *ip;
 struct ipv6hdr *ip6=NULL;
 struct udphdr *udp;
 const struct rtl9617c_ni_frame frame={.header_low=(25<<3)|BIT(29)};
 int ret;
 static const char payload[]="TYFZA1-FE-NAT-TEST";

 key.destination=test_destination;
 key.destination_port=test_port;
 action.address=nat_source;
 action.deep_queue=egress>=32;
 if(ipv6_test)action.translate=0;
 ret=rtl_fe_mask_read(io,63,mask_saved);
 if(ret)return ret;
 for(i=0;i<2;i++) {
  ret=rtl_fe_classifier_read(io,1024+i,classifier_saved[i]);
  if(ret)return ret;
 }
 for(i=0;i<4;i++) {
  ret=io->indirect(io->context,0x1654,0x80000000U|25|(i<<6));
  if(ret)return ret;
  map_saved[i]=io->read(io->context,0x1658);
 }
 pipeline_configured=true;
 if(ipv6_test)rtl_fe_ipv6_mask(mask);
 else rtl_fe_ipv4_mask(mask);
 mask[6]|=BIT(201-192)|BIT(215-192)|BIT(218-192);
 ret=rtl_fe_mask_write(io,63,mask);
 if(ret)return ret;
 rtl_fe_classify_hash(cls,0,0);
 for(i=0;i<2;i++) {
  ret=rtl_fe_classifier_write(io,1024+i,cls);
  if(ret)return ret;
 }
 rtl_fe_ingress_lpb(lpb,0,16);
 for(i=0;i<3;i++)writel(lpb[i],base+0x3414+i*4);
 writel((readl(base+0x3400)&~(BIT(0)|BIT(10)|BIT(19)))|BIT(3)|BIT(2),base+0x3400);
 writel(7|(25<<8)|(1<<14)|(24<<16)|(2<<22)|(3<<24),base+0x3404);
 for(i=0;i<4;i++) {
  io->write(io->context,0x1658,13);
  ret=io->indirect(io->context,0x1654,0xc0000000U|25|(i<<6));
  if(ret)return ret;
 }
 writel(readl(base+0x2300)|BIT(13),base+0x2300);
 writel(0xff|(6<<16),base+0x2304+13*12);
 for(i=0;i<5;i++)writel(0,base+0x394c+i*4);
 writel(63,base+0x3704);
 writel(1,base+0x3700);
 writel(0x020d0000,base+0x3860); /* group16/18/19,128-bit CPU miss */
 if(ipv6_test)rtl_fe_ipv6_key(key_words,&key6);
 else rtl_fe_ipv4_key(key_words,&key);
 rtl_fe_key_set(key_words,RTL_FE_KEY_HKEY_ID,0);
 rtl_fe_key_set(key_words,RTL_FE_KEY_TABLE_ID,0);
 rtl_fe_key_crc(key_words,&crc32,&crc16);
 index=rtl_fe_hash_bucket(crc16);
 rtl_fe_ipv4_action(action_words,&action);
 rtl_fe_action_set(action_words,RTL_FE_CACHE_CTRL1,test_cache_control);
 rtl_fe_action_set(action_words,RTL_FE_MCGID,egress);
 rtl_fe_action_set(action_words,RTL_FE_L3_IF_VLD1,0);
 rtl_fe_action_set(action_words,RTL_FE_SMAC_TRANS,0);
 rtl_fe_action_set(action_words,RTL_FE_MAC_DA_IDX_VLD,0);
 if(mac_test) {
  ret=configure_mac(io,&mac_states[0],14,31,source_mac,destination_mac,&destination_index);
  if(ret)return ret;
  rtl_fe_action_set(action_words,RTL_FE_MAC_DA_IDX_VLD,destination_test);
  rtl_fe_action_set(action_words,RTL_FE_MAC_DA_IDX,destination_index);
  rtl_fe_action_set(action_words,RTL_FE_L3_IF_VLD1,1);
  rtl_fe_action_set(action_words,RTL_FE_EGR_L3_IF_IDX1,31);
  rtl_fe_action_set(action_words,RTL_FE_L3_IF_COUNTER_EN,1);
 }
 ((u32 *)memory)[index]=cpu_to_le32(crc32);
 memcpy(memory+RTL_FE_ACTION_OFFSET+index*RTL_FE_ACTION_BYTES,action_words,sizeof(action_words));
 dma_wmb();
 ret=rtl_fe_main_age(io,index,test_age);
 if(ret)return ret;
 pr_info("TYFZA1 FE packet static flow:index=%u CRC=%08x/%04x destination=%08x output_source=%08x:62000 egress=%u deepq=%u\n",index,crc32,crc16,key.destination,action.address,egress,action.deep_queue);
 flow_indices[0]=index;flow_crcs16[0]=crc16;flow_installed[0]=true;
 if(reverse_test) {
  ret=configure_reverse(io);
  if(ret)return ret;
 }
 writel(test_cache_misc,base+0x38c4);
 if(test_cache_misc) {
  rtl_fe_cache_start(io);
  writel(test_cache_misc,base+0x38c4);
 }
 if(wired_ingress)return configure_lan_ingress(io);
 skb=alloc_skb(128,GFP_KERNEL);
 if(!skb)return -ENOMEM;
 eth=skb_put(skb,sizeof(*eth));
 ether_addr_copy(eth->h_dest,owner->dev_addr);
 ether_addr_copy(eth->h_source,(u8[]){2,0x11,0x22,0x33,0x44,0x55});
 eth->h_proto=htons(ipv6_test?ETH_P_IPV6:ETH_P_IP);
 if(ipv6_test) {
  ip6=skb_put_zero(skb,sizeof(*ip6));
  ip6->version=6;ip6->hop_limit=64;ip6->nexthdr=17;
  ip6->payload_len=htons(sizeof(*udp)+sizeof(payload));
  for(i=0;i<4;i++) {
   ip6->saddr.s6_addr32[i]=htonl(key6.source[i]);
   ip6->daddr.s6_addr32[i]=htonl(key6.destination[i]);
  }
 } else {
  ip=skb_put_zero(skb,sizeof(*ip));
  ip->version=4;ip->ihl=5;ip->ttl=64;ip->protocol=17;
  ip->saddr=htonl(key.source);ip->daddr=htonl(key.destination);
  ip->tot_len=htons(sizeof(*ip)+sizeof(*udp)+sizeof(payload));
  ip_send_check(ip);
 }
 udp=skb_put_zero(skb,sizeof(*udp));
 udp->source=htons(key.source_port);udp->dest=htons(key.destination_port);
 udp->len=htons(sizeof(*udp)+sizeof(payload));
 memcpy(skb_put(skb,sizeof(payload)),payload,sizeof(payload));
 if(ipv6_test)
  udp->check=csum_ipv6_magic(&ip6->saddr,&ip6->daddr,sizeof(*udp)+sizeof(payload),17,
   csum_partial(udp,sizeof(*udp)+sizeof(payload),0));
 else udp->check=csum_tcpudp_magic(ip->saddr,ip->daddr,sizeof(*udp)+sizeof(payload),17,
   csum_partial(udp,sizeof(*udp)+sizeof(payload),0));
 if(!udp->check)udp->check=CSUM_MANGLED_0;
 skb->dev=owner;skb->protocol=htons(ipv6_test?ETH_P_IPV6:ETH_P_IP);
 pr_info("TYFZA1 FE packet egress=%u CPU_RX_before=%lu\n",egress,owner->stats.rx_packets);
 ret=rtl9617c_ni_transmit(device,owner,skb,&frame);
 if(ret==NETDEV_TX_BUSY){kfree_skb(skb);return -EBUSY;}
 usleep_range(500,1000);
 for(i=0;i<64;i++) {
  writel(i,base+0x30b8);
  trace[i]=readl(base+0x30bc);
 }
 {
  unsigned int actual32;
  unsigned short actual16;
  ret=rtl_fe_crc_hardware(io,trace,63,&actual32,&actual16);
  if(ret)return ret;
  pr_info("TYFZA1 FE actual packet CRC=%08x/%04x expected=%08x/%04x\n",actual32,actual16,crc32,crc16);
  for(i=0;i<RTL_FE_KEY_WORDS;i++) {
   unsigned int word=io->read(io->context,0x38dc);
   if(word!=key_words[i])pr_info("TYFZA1 FE actual tuple word%u=%08x expected=%08x\n",i,word,key_words[i]);
  }
 }
 for(i=0;i<64;i++)
  pr_info("TYFZA1 FE packet stage%u word%u=%08x\n",i>>5,i&31,trace[i]);
 for(i=0;i<4;i++) {
  writel((15<<5)|i,base+0x30b8);
  pr_info("TYFZA1 FE packet counters%u=%08x\n",i,readl(base+0x30bc));
 }
 pr_info("TYFZA1 FE packet CPU_RX_after=%lu\n",owner->stats.rx_packets);
 for(i=0;i<2;i++) {
  writel((14<<5)|i,base+0x30b8);
  pr_info("TYFZA1 FE packet output_HeaderA%u=%08x\n",i,readl(base+0x30bc));
 }
 for(i=0;i<32;i++) {
  writel((8<<5)|i,base+0x30b8);
  trace[i]=readl(base+0x30bc);
 }
 for(i=0;i<32;i+=4)
  pr_info("TYFZA1 FE before_PE words%u=%08x/%08x/%08x/%08x\n",i,trace[i],trace[i+1],trace[i+2],trace[i+3]);
 writel(3<<5,base+0x30c4);
 pr_info("TYFZA1 FE PE drop_latch=%08x\n",readl(base+0x30c8));
 return 0;
}

static unsigned int header_field(const unsigned int *words,unsigned int bit,unsigned int width)
{
 u64 value=words[bit/32];
 if(bit%32+width>32)value|=(u64)words[bit/32+1]<<32;
 return (value>>(bit%32))&(width==32?~0U:((1U<<width)-1));
}
static void snapshot_pipeline(const unsigned int *matched_header)
{
 unsigned int words[7][32],l3[64],latch[64],group,i;
 const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
 unsigned int crc32;
 unsigned short crc16;

 for(group=0;group<7;group++)
  for(i=0;i<32;i++) {
   writel((group<<5)|i,base+0x105c);
   words[group][i]=readl(base+0x1060);
  }
 for(i=0;i<64;i++) {
  writel(i,base+0x30b8);
  l3[i]=readl(base+0x30bc);
  writel(i,base+0x30c4);
  latch[i]=readl(base+0x30c8);
 }
 if(matched_header)memcpy(l3,matched_header,32*sizeof(*l3));
 for(group=0;group<7;group++)
  for(i=0;i<(group==0?32:8);i+=4)
   pr_info("TYFZA1 FE L2 group%u words%u=%08x/%08x/%08x/%08x\n",
    group,i,words[group][i],words[group][i+1],words[group][i+2],words[group][i+3]);
 for(i=0;i<64;i+=4)
  pr_info("TYFZA1 FE L3 words%u=%08x/%08x/%08x/%08x\n",
   i,l3[i],l3[i+1],l3[i+2],l3[i+3]);
 for(i=0;i<64;i+=4)
  pr_info("TYFZA1 FE latch words%u=%08x/%08x/%08x/%08x\n",
   i,latch[i],latch[i+1],latch[i+2],latch[i+3]);
 if(!rtl_fe_crc_hardware(&io,l3,63,&crc32,&crc16)) {
  struct rtl_fe_ipv4_key key={.source=test_destination,.destination=nat_source,
   .source_port=test_port,.destination_port=62000,.protocol=17,.ingress=7,.mask=63};
  unsigned int expected[RTL_FE_KEY_WORDS];

  pr_info("TYFZA1 FE physical L3 CRC=%08x/%04x\n",crc32,crc16);
  rtl_fe_ipv4_key(expected,&key);
  rtl_fe_key_set(expected,RTL_FE_KEY_HKEY_ID,0);
  rtl_fe_key_set(expected,RTL_FE_KEY_TABLE_ID,0);
  rtl_fe_key_set(expected,RTL_FE_KEY_ORIG_LSPID,0);
  for(i=0;i<RTL_FE_KEY_WORDS;i++) {
   unsigned int actual=readl(base+0x38dc);
   if(actual!=expected[i])pr_info("TYFZA1 FE physical tuple%u actual=%08x expected=%08x\n",i,actual,expected[i]);
  }
 }
}
static void trace_work_fn(struct work_struct *work)
{
 unsigned int header[32],i;

 for(i=0;i<32;i++) {
  writel(i,base+0x30b8);
  header[i]=readl(base+0x30bc);
 }
 if(header_field(header,361,32)==test_destination &&
    header_field(header,233,32)==nat_source &&
    header_field(header,74,16)==62000 && header_field(header,90,16)==test_port) {
  snapshot_pipeline(header);
  pr_info("TYFZA1 FE targeted reverse header matched after %u samples\n",trace_attempts);
 } else if(++trace_attempts<2000)
  schedule_delayed_work(&trace_work,msecs_to_jiffies(1));
 else pr_info("TYFZA1 FE targeted reverse header timed out after 2000 samples\n");
}
static unsigned int monitor;
static int remove_test_flows(void)
{
 const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
 unsigned int i;
 int ret;

 for(i=0;i<2;i++)if(flow_installed[i]) {
  ret=rtl_fe_main_age(&io,flow_indices[i],0);
  if(ret)return ret;
  ret=rtl_fe_cache_command(&io,flow_indices[i],flow_crcs16[i],1);
  if(ret)return ret;
  ((u32 *)memory)[flow_indices[i]]=0;
  dma_wmb();
  flow_installed[i]=false;
  pr_info("TYFZA1 FE static direction%u removed:index=%u cache invalidated age=0\n",i,flow_indices[i]);
 }
 return 0;
}
static int monitor_set(const char *value,const struct kernel_param *parameter)
{
 int ret=param_set_uint(value,parameter);

 if(ret)return ret;
 if(!configured)return -ENODEV;
 if(monitor==1) {
  writel((readl(base+0x1058)|BIT(31))^BIT(0),base+0x1058);
  writel((readl(base+0x30c0)|BIT(1))^BIT(0),base+0x30c0);
  pr_info("TYFZA1 FE next physical packet monitor armed\n");
 } else if(monitor==2)snapshot_pipeline(NULL);
 else if(monitor==3){trace_attempts=0;schedule_delayed_work(&trace_work,0);}
 else if(monitor==4)return remove_test_flows();
 else if(monitor==5) {
  const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
  unsigned int i,age;

  for(i=0;i<2;i++)if(flow_installed[i]) {
   ret=rtl_fe_main_age_read(&io,flow_indices[i],&age);
   if(ret)return ret;
   pr_info("TYFZA1 FE direction%u index%u current_age=%u\n",i,flow_indices[i],age);
  }
 }
 else if(monitor==6) {
  const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
  unsigned int i,words[7];

  for(i=0;i<2;i++)if(mac_states[i].configured) {
   struct test_mac_state *state=&mac_states[i];
   u64 before=(u64)state->counters_before[1]<<32|state->counters_before[0],bytes;

   ret=rtl_fe_tx_counter_read(&io,state->interface_id,words);
   if(ret)return ret;
   bytes=(u64)words[1]<<32|words[0];
   pr_info("TYFZA1 FE direction%u TX IFID%u packet_delta=%u bytes_delta=%llu dropped_delta=%u\n",
    i,state->interface_id,words[2]-state->counters_before[2],bytes-before,words[6]-state->counters_before[6]);
  }
 }
 else if(monitor==7) {
  const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
  unsigned int slot,words[7],count=0;

  for(slot=0;slot<RTL_FE_CACHE_ENTRIES;slot++) {
   ret=rtl_fe_cache_read(&io,slot,0,words);
   if(ret)return ret;
   if(!words[0])continue;
   count++;
   pr_info("TYFZA1 FE cache%u key=%08x metadata=%08x/%08x\n",slot,words[0],words[1],words[2]);
  }
  pr_info("TYFZA1 FE populated cache entries=%u CACHE_MISC=%08x\n",count,readl(base+0x38c4));
 }
 else return -EINVAL;
 return 0;
}
static const struct kernel_param_ops monitor_ops={.set=monitor_set,.get=param_get_uint};
module_param_cb(monitor,&monitor_ops,&monitor,0600);
static void restore(void)
{
 unsigned int i;
 const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
 /* Remove physical ingress before detaching the test's DMA tables. */
 for(i=0;i<ARRAY_SIZE(regs);i++)
  if(regs[i]==0xa5f8)writel(saved[i],base+regs[i]);
 if(pdc_configured) {
  mutex_lock(&pon->control_lock);
  mutex_lock(&pon->lock);
  for(i=0;i<2;i++) {
   writel(pdc_saved[i][0],pon->mac+0x9028);
   writel(pdc_saved[i][1],pon->mac+0x9024);
   pdc_access(i*128,true);
  }
  mutex_unlock(&pon->lock);
  mutex_unlock(&pon->control_lock);
  pdc_configured=false;
 }
 if(pon_device){put_device(pon_device);pon_device=NULL;pon=NULL;}
 usleep_range(1000,2000);
 if(lan_fib_configured) {
  for(i=0;i<5;i++)writel(lan_icls_saved[i],base+0x1b58-i*4);
  reg_access(NULL,0x1b44,0xc0000000U);
  for(i=0;i<3;i++)writel(lan_fib_saved[i],base+0x1e20-i*4);
  reg_access(NULL,0x1e14,0xc0000000U);
  lan_fib_configured=false;
 }
 for(i=0;i<2;i++)if(mac_states[i].configured) {
  struct test_mac_state *state=&mac_states[i];
  unsigned int fdb[4],result;
  if(state->created) {
   rtl_fe_fdb_key(fdb,state->destination);
   rtl_fe_fdb_command(&io,0x85,fdb,&result);
   state->created=false;
  }
  rtl_fe_source_mac_write(&io,state->cam_id,state->cam_saved);
  writel(state->interface_saved,base+0x3004);
  reg_access(NULL,0x3000,0xc0000000U|state->interface_id);
  state->configured=false;
 }
 if(wan_map_configured) {
  for(i=0;i<4;i++) {
   writel(wan_map_saved[i],base+0x1658);
   reg_access(NULL,0x1654,0xc0000000U|24|(i<<6));
  }
  wan_map_configured=false;
 }
 if(pipeline_configured) {
  for(i=0;i<4;i++) {
   writel(map_saved[i],base+0x1658);
   reg_access(NULL,0x1654,0xc0000000U|25|(i<<6));
  }
  rtl_fe_mask_write(&io,63,mask_saved);
  for(i=0;i<2;i++)rtl_fe_classifier_write(&io,1024+i,classifier_saved[i]);
  pipeline_configured=false;
 }
 /* Stop cache allocations before detaching every DMA base from this memory. */
 writel(0,base+0x38c4);
 writel(1,base+0x393c);
 {u32 status;readl_poll_timeout(base+0x393c,status,!(status&1),1,100000);}
 for(i=0;i<ARRAY_SIZE(regs);i++)
  writel(saved[i],base+regs[i]);
 readl(base+0x383c);
 configured=false;
}
static int __init memory_init(void)
{
 const struct rtl_dq_io io={NULL,reg_read,reg_write,reg_access};
 unsigned int i,*defaults;
 u32 status;
 int ret;

 if(!mac_pton(source_mac_text,source_mac)||!mac_pton(destination_mac_text,destination_mac))
  return -EINVAL;
 device=bus_find_device_by_name(&platform_bus_type,NULL,"4f4300000.ethernet");
 if(!device)return -ENODEV;
 base=ioremap(0x4f4300000ULL,0x10000);
 if(!base){ret=-ENOMEM;goto put_device;}
 memory=dma_alloc_coherent(device,RTL_FE_MEMORY_BYTES,&dma_address,GFP_KERNEL);
 if(!memory){ret=-ENOMEM;goto unmap;}
 defaults=memory+RTL_FE_DEFAULT_OFFSET;
 for(i=0;i<32;i++)rtl_fe_cpu_miss(defaults+i*4);
 dma_wmb();
 for(i=0;i<ARRAY_SIZE(regs);i++)saved[i]=readl(base+regs[i]);
 rtl_fe_dma_tables(&io,lower_32_bits(dma_address),(upper_32_bits(dma_address)&255)|2);
 configured=true;
 writel(1,base+0x393c);
 ret=readl_poll_timeout(base+0x393c,status,!(status&1),1,100000);
 if(ret)goto restore_tables;
 pr_info("TYFZA1 native FE DMA:address=%pad bytes=%u main=%u flow%uB defaults16B cache%u-internal; memory initialization complete\n",
  &dma_address,RTL_FE_MEMORY_BYTES,RTL_FE_HASH_ENTRIES,RTL_FE_ACTION_BYTES,RTL_FE_CACHE_ENTRIES);
 pr_info("TYFZA1 FE DMA readback:HASH=%08x/%08x ACTION=%08x/%08x DEFAULT=%08x/%08x config=%08x cache=%08x\n",
  readl(base+0x3838),readl(base+0x383c),readl(base+0x3840),readl(base+0x3844),
  readl(base+0x3858),readl(base+0x385c),readl(base+0x3834),readl(base+0x38a0));
 writel(RTL_FE_ACTION_GROUPS|(3U<<25),base+0x3724);
 ret=cache_dma_check(&io);
 if(ret)goto restore_tables;
 if(packet_test) {ret=packet_pipeline(&io);if(ret)goto restore_tables;}
 return 0;
restore_tables:
 restore();
 dma_free_coherent(device,RTL_FE_MEMORY_BYTES,memory,dma_address);
unmap:
 iounmap(base);
put_device:
 put_device(device);
 return ret;
}
static void __exit memory_exit(void)
{
 cancel_delayed_work_sync(&trace_work);
 if(configured)restore();
 dma_free_coherent(device,RTL_FE_MEMORY_BYTES,memory,dma_address);
 iounmap(base);
 put_device(device);
 pr_info("TYFZA1 native FE DMA tables detached and original configuration restored\n");
}
module_init(memory_init);
module_exit(memory_exit);
MODULE_DESCRIPTION("Native RTL9617C FE DMA table initialization");
MODULE_LICENSE("GPL");
