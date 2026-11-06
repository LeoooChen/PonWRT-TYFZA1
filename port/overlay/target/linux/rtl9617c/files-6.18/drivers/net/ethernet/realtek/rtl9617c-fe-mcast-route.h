/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_MCAST_ROUTE_H
#define RTL9617C_FE_MCAST_ROUTE_H
#include "rtl9617c-fe-mcast.h"

/* The table owner does not own port/queue configuration. Save and restore
 * just the multicast connector and per-destination L2 editor/deep-buffer
 * selections, using the same FE mutex as Hash publication. */
struct rtl_fe_mc_route {
 unsigned int registers[7],map[2],elp[17][2];
 unsigned int connector_elp[2];
 unsigned int dbuf_index,dbuf_saved,ports;
 unsigned char owned,dbuf_owned,ready;
};
static const unsigned int rtl_fe_mc_route_regs[]={0x1600,0x1700,0x30a4,0xa1dc,0x2388,0x2300,0xaa60};
static const unsigned int rtl_fe_mc_route_masks[]={0x80000100U,0x104,0x3f000,3U<<22,0x00ff00ff,1U<<11,1023};

static inline int rtl_fe_mc_route_read(const struct rtl_dq_io *io,unsigned int access,
 unsigned int data,unsigned int index,unsigned int *words,unsigned int count)
{
 int ret;
 if(io->read(io->context,access)&0x80000000U)return -EBUSY;
 ret=io->indirect(io->context,access,0x80000000U|index);
 if(ret)return ret;
 for(unsigned int i=0;i<count;i++)words[i]=io->read(io->context,data-4*i);
 return 0;
}
static inline int rtl_fe_mc_route_write(const struct rtl_dq_io *io,unsigned int access,
 unsigned int data,unsigned int index,const unsigned int *words,unsigned int count)
{
 unsigned int actual[2];
 int ret;
 if(io->read(io->context,access)&0x80000000U)return -EBUSY;
 for(unsigned int i=0;i<count;i++)io->write(io->context,data-4*i,words[i]);
 ret=io->indirect(io->context,access,0xc0000000U|index);
 if(ret)return ret;
 ret=rtl_fe_mc_route_read(io,access,data,index,actual,count);
 if(ret)return ret;
 for(unsigned int i=0;i<count;i++)if(actual[i]!=words[i])return -EIO;
 return 0;
}
static inline int rtl_fe_mc_route_update(const struct rtl_dq_io *io,unsigned int reg,
 unsigned int mask,unsigned int value)
{
 io->write(io->context,reg,(io->read(io->context,reg)&~mask)|(value&mask));
 return (io->read(io->context,reg)&mask)==(value&mask)?0:-EIO;
}

static inline int rtl_fe_mc_route_restore(const struct rtl_dq_io *io,struct rtl_fe_mc_route *route)
{
 unsigned int i;
 int ret;
 if(!route->owned)return 0;
 route->ready=0;
 /* No classifier may reference a multicast group at this point. Exposed
  * NI/action indices remain quarantined in their separate owner. */
 if(route->dbuf_owned) {
  ret=rtl_fe_mc_route_write(io,0x164c,0x1650,route->dbuf_index,&route->dbuf_saved,1);
  if(ret)return ret;
  route->dbuf_owned=0;
 }
 for(i=0;i<17;i++)if(route->ports&(1U<<i)) {
  ret=rtl_fe_mc_route_write(io,0x1420,0x1428,i,route->elp[i],2);
  if(ret)return ret;
  route->ports&=~(1U<<i);
 }
 for(i=0;i<2;i++) {
  ret=rtl_fe_mc_route_write(io,0x1654,0x1658,27+(i<<7),route->map+i,1);
  if(ret)return ret;
 }
 ret=rtl_fe_mc_route_write(io,0x1420,0x1428,27,route->connector_elp,2);
 if(ret)return ret;
 for(i=0;i<7;i++) {
  ret=rtl_fe_mc_route_update(io,rtl_fe_mc_route_regs[i],rtl_fe_mc_route_masks[i],route->registers[i]);
  if(ret)return ret;
 }
 route->owned=0;
 return 0;
}

static inline int rtl_fe_mc_route_prepare(const struct rtl_dq_io *io,struct rtl_fe_mc_route *route)
{
 unsigned int i,value;
 int ret;
 if(route->ready)return 0;
 /* Keeping the source across MCE relies on its original source-to-POL
  * copy. A foreign flush or changed copy policy is not ours to replace. */
 if((io->read(io->context,0xaa60)&(3U<<17))!=(1U<<17))return -EUCLEAN;
 /* Matching register reset: source27, nondeep, FE bypass. This is the
  * MCE return path, independent of the outbound logical27 destination. */
 if((io->read(io->context,0xa1c0)&255)!=0x9b)return -EUCLEAN;
 if(!route->owned) {
  for(i=0;i<7;i++)route->registers[i]=io->read(io->context,rtl_fe_mc_route_regs[i]);
  for(i=0;i<2;i++) {
   ret=rtl_fe_mc_route_read(io,0x1654,0x1658,27+(i<<7),route->map+i,1);
   if(ret)return ret;
  }
  ret=rtl_fe_mc_route_read(io,0x1420,0x1428,27,route->connector_elp,2);
  if(ret)return ret;
  if((route->connector_elp[0]&0xfffc0004U) || (route->connector_elp[1]&255U))return -EOPNOTSUPP;
  route->owned=1;
 }
 /* MC1/DEEPQ0 resolves to logical27, reaches L2 arbitration and PDPID11.
  * Its actions carry the recipients; QM still owns the existing pools. */
 value=11;
 for(i=0;i<2;i++) {
  ret=rtl_fe_mc_route_write(io,0x1654,0x1658,27+(i<<7),&value,1);
  if(ret)return ret;
 }
 {
  const unsigned int words[2]={(route->connector_elp[0]&~15U)|3,route->connector_elp[1]};
  ret=rtl_fe_mc_route_write(io,0x1420,0x1428,27,words,2);
  if(ret)return ret;
 }
 ret=rtl_fe_mc_route_update(io,0x30a4,0x3f000,27U<<12);
 if(ret)return ret;
 ret=rtl_fe_mc_route_update(io,0xa1dc,3U<<22,2U<<22);
 if(ret)return ret;
 /* As in aal_arb_init, dbuf selection must not depend on HeaderA enable;
  * replica-specific P6 destination selection is added below. */
 ret=rtl_fe_mc_route_update(io,0x1600,0x80000100U,0x80000000U);
 if(ret)return ret;
 /* aal_ni_init reserves enough MCE FIFO space for the original maximum
  * frame. Preserve the other controls and do not clear inherited groups. */
 ret=rtl_fe_mc_route_update(io,0xaa60,1023,23);
 if(ret)return ret;
 /* Original L2 VLAN initialization enables the enhanced TPID selectors.
  * Keep mod_orig_tag clear: the replica commands specify final S/C tags. */
 ret=rtl_fe_mc_route_update(io,0x1700,0x104,0x100);
 if(ret)return ret;
 ret=rtl_fe_mc_route_update(io,0x2388,0x00ff00ff,0x000600ff);
 if(ret)return ret;
 ret=rtl_fe_mc_route_update(io,0x2300,1U<<11,1U<<11);
 if(!ret)route->ready=1;
 return ret;
}

static inline int rtl_fe_mc_route_members(const struct rtl_dq_io *io,unsigned int source,
 const struct rtl_fe_mc_replica *replicas,unsigned int count)
{
 unsigned int words[2],required=0,i;
 int ret;

 if((source>4 && source!=6) || !count || count>RTL_FE_MC_ACTIONS)return -EINVAL;
 for(i=0;i<count;i++) {
  if((replicas[i].destination>6 && replicas[i].destination!=16) || replicas[i].destination==5)return -EINVAL;
  required|=1U<<replicas[i].destination;
 }
 /* Native TC supplies a complete member set. If a previous owner enabled
  * the additional L2 port-isolation check, verify that it permits both
  * connector and replica traversal. Never turn off or widen that policy. */
 if(!(io->read(io->context,0x160c)&1))return 0;
 ret=rtl_fe_mc_route_read(io,0x1574,0x157c,source,words,2);
 if(ret)return ret;
 if((words[0]&(required|(1U<<27)))!=(required|(1U<<27)))return -EOPNOTSUPP;
 ret=rtl_fe_mc_route_read(io,0x1574,0x157c,27,words,2);
 if(ret)return ret;
 return (words[0]&required)==required?0:-EOPNOTSUPP;
}

static inline int rtl_fe_mc_route_port(const struct rtl_dq_io *io,struct rtl_fe_mc_route *route,
 unsigned int port)
{
 unsigned int words[2],i;
 int ret;
 if(!route->ready || (port>6 && port!=16) || port==5)return -EINVAL;
 /* QM initializes both my-MAC map cases before native FE can be enabled.
  * Refuse a changed mapping rather than sending a replica to another port. */
 for(i=0;i<2;i++) {
  unsigned int index=port+(i<<7)+(port==6?64:0);
  ret=rtl_fe_mc_route_read(io,0x1654,0x1658,index,words,1);
  if(ret)return ret;
  if((words[0]&15)!=(port==6?8:port==16?9:port))return -EUCLEAN;
 }
 /* CPU0 uses the normal Header A/CPU path, never a radio EPP ring. */
 if(port==16 && ((io->read(io->context,0x6558)&7) ||
    (io->read(io->context,0xa1dc)&3) || (io->read(io->context,0xa1ec)&3)))return -EUCLEAN;
 if(port==6 && ((io->read(io->context,0x1600)>>4)&15)!=8)return -EUCLEAN;
 if(!(route->ports&(1U<<port))) {
  ret=rtl_fe_mc_route_read(io,0x1420,0x1428,port,words,2);
  if(ret)return ret;
  /* Preserve a foreign egress classifier, VLAN lookup or marking policy by
   * rejecting this output. Do not disable it for unrelated port users. */
  if((words[0]&0xfffc0004U) || (words[1]&255U))return -EOPNOTSUPP;
  route->elp[port][0]=words[0];route->elp[port][1]=words[1];
  route->ports|=1U<<port;
 }
 /* Explicit replica actions own VLAN/COS; no second egress lookup. */
 words[0]=(route->elp[port][0]&~15U)|3;words[1]=route->elp[port][1];
 ret=rtl_fe_mc_route_write(io,0x1420,0x1428,port,words,2);
 if(ret || port!=6)return ret;
 /* Find a free port-deep-buffer rule. Never overwrite a foreign active
  * rule, including FC's source17 rule. Matching final P6 sends it through
  * the already initialized private0/shared2 pools and QM port14. */
 if(!route->dbuf_owned) {
  for(i=0;i<8;i++) {
   ret=rtl_fe_mc_route_read(io,0x164c,0x1650,i,words,1);
   if(ret)return ret;
   if(!(words[0]&1))break;
  }
  if(i==8)return -ENOSPC;
  route->dbuf_index=i;route->dbuf_saved=words[0];route->dbuf_owned=1;
 }
 words[0]=(route->dbuf_saved&~0x7fffU)|1U|2U|(6U<<2);
 return rtl_fe_mc_route_write(io,0x164c,0x1650,route->dbuf_index,words,1);
}
#endif
