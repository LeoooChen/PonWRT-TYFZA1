// SPDX-License-Identifier: GPL-2.0-only
/* One native NI complex owns accelerated bridge multicast on this SoC. */
#include <linux/atomic.h>
#include <linux/dma-mapping.h>
#include <linux/spinlock.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/mutex.h>
#include <linux/netdevice.h>
#include <linux/rcupdate.h>
#include <linux/skbuff.h>
#include <linux/rtl9617c-bridge.h>
#include <linux/rtl9617c-fttr-pe.h>

static DEFINE_MUTEX(rtl_bridge_lock);
/* This built-in owner outlives driver unload and any queued skb extensions. */
static atomic64_t rtl_bridge_ticket = ATOMIC64_INIT(0);
static atomic64_t rtl_wifi_policy_generation = ATOMIC64_INIT(1);
static const struct rtl9617c_bridge_mcast_ops __rcu *rtl_bridge_mcast;

int rtl9617c_bridge_mcast_register(const struct rtl9617c_bridge_mcast_ops *ops)
{
	int ret = 0;

	mutex_lock(&rtl_bridge_lock);
	if (rcu_access_pointer(rtl_bridge_mcast))
		ret = -EBUSY;
	else {
		ops->change_begin(ops->context, NULL);
		rcu_assign_pointer(rtl_bridge_mcast, ops);
		/* Exclude writers that began before publication with a NULL token. */
		synchronize_net();
		ops->change_end(ops->context);
	}
	mutex_unlock(&rtl_bridge_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_register);

void rtl9617c_bridge_mcast_unregister(const struct rtl9617c_bridge_mcast_ops *ops)
{
	mutex_lock(&rtl_bridge_lock);
	if (rcu_access_pointer(rtl_bridge_mcast) == ops) {
		ops->change_begin(ops->context, NULL);
		RCU_INIT_POINTER(rtl_bridge_mcast, NULL);
		/* begin/end pins the owner across the bridge writer. */
		synchronize_net();
		ops->change_end(ops->context);
	}
	mutex_unlock(&rtl_bridge_lock);
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_unregister);

const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_mcast_begin(struct net_device *bridge)
{
	const struct rtl9617c_bridge_mcast_ops *ops;

	rcu_read_lock();
	ops = rcu_dereference(rtl_bridge_mcast);
	if (ops)
		ops->change_begin(ops->context, bridge);
	return ops;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_begin);

const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_wifi_policy_begin(struct net_device *dev)
{
	const struct rtl9617c_bridge_mcast_ops *ops = rtl9617c_bridge_mcast_begin(dev);
	atomic64_inc(&rtl_wifi_policy_generation);
	return ops;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_wifi_policy_begin);

u64 rtl9617c_bridge_wifi_policy_generation(void)
{
	return atomic64_read(&rtl_wifi_policy_generation);
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_wifi_policy_generation);

void rtl9617c_bridge_mcast_end(const struct rtl9617c_bridge_mcast_ops *ops)
{
	if (ops)
		ops->change_end(ops->context);
	rcu_read_unlock();
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_end);

/* Packet callbacks are short and only copy a bounded recipient snapshot.
 * A numeric ticket is carried in skb extensions; delayed/queued copies
 * cannot retain a freed driver pointer. */
u64 rtl9617c_bridge_mcast_capture(struct sk_buff *skb, bool local)
{
	const struct rtl9617c_bridge_mcast_ops *ops;
	u64 transaction = 0;

	rcu_read_lock();
	ops = rcu_dereference(rtl_bridge_mcast);
	if (ops && ops->capture_begin) {
		u64 ticket = atomic64_inc_return(&rtl_bridge_ticket);
		if (ticket)
			transaction = ops->capture_begin(ops->context, skb, local, ticket);
	}
	rcu_read_unlock();
	return transaction;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_capture);

void rtl9617c_bridge_mcast_select(struct sk_buff *skb, struct net_device *output)
{
	const struct rtl9617c_bridge_mcast_ops *ops;

	rcu_read_lock();
	ops = rcu_dereference(rtl_bridge_mcast);
	if (ops && ops->capture_select)
		ops->capture_select(ops->context, skb, output);
	rcu_read_unlock();
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_select);

void rtl9617c_bridge_mcast_select_addr(struct sk_buff *skb, struct net_device *output, const u8 *address)
{
	const struct rtl9617c_bridge_mcast_ops *ops;

	rcu_read_lock();
	ops = rcu_dereference(rtl_bridge_mcast);
	if (ops && ops->capture_select_addr)
		ops->capture_select_addr(ops->context, skb, output, address);
	rcu_read_unlock();
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_select_addr);

void rtl9617c_bridge_mcast_output(struct sk_buff *skb)
{
	const struct rtl9617c_bridge_mcast_ops *ops;

	rcu_read_lock();
	ops = rcu_dereference(rtl_bridge_mcast);
	if (ops && ops->capture_output)
		ops->capture_output(ops->context, skb);
	rcu_read_unlock();
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_output);

void rtl9617c_bridge_mcast_complete(u64 transaction, bool complete)
{
	const struct rtl9617c_bridge_mcast_ops *ops;

	if (!transaction)
		return;
	rcu_read_lock();
	ops = rcu_dereference(rtl_bridge_mcast);
	if (ops && ops->capture_end)
		ops->capture_end(ops->context, transaction, complete);
	rcu_read_unlock();
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_complete);

/* A host copy is already replicated. Missing/retired owners must drop it;
 * falling through as an ordinary miss would duplicate bridge forwarding. */
int rtl9617c_bridge_mcast_receive(struct sk_buff *skb, unsigned int group, unsigned int source)
{
 const struct rtl9617c_bridge_mcast_ops *ops;
 int ret=-ENODEV;
 rcu_read_lock();
 ops=rcu_dereference(rtl_bridge_mcast);
 if(ops && ops->host_receive)ret=ops->host_receive(ops->context,skb,group,source);
 rcu_read_unlock();
 return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_receive);

int rtl9617c_bridge_mcast_local(struct sk_buff *skb, struct net_device *bridge, u16 vid)
{
 const struct rtl9617c_bridge_frame *frame=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 const struct rtl9617c_bridge_mcast_ops *ops;
 int ret=-ESTALE;
 if(!frame || !frame->multicast_host_group)return 0;
 rcu_read_lock();
 ops=rcu_dereference(rtl_bridge_mcast);
 if(ops && ops->host_local)ret=ops->host_local(ops->context,skb,bridge,vid);
 rcu_read_unlock();
 if(ret>0) {
  struct rtl9617c_bridge_frame *saved=skb_ext_add(skb,SKB_EXT_RTL9617C_BRIDGE);
  if(!saved)return -ENOMEM;
  /* This bridge consumed the local-copy decision. A stacked bridge or
   * later routing must make its own decision, as for offload_fwd_mark. */
  saved->multicast_host_group=0;saved->multicast_host_owner=0;
 }
 return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_local);

/* A copy may wait in qdisc/mac80211 after host_receive returned. Keep its
 * original complete-set decision attached until TX consumes the packet.
 */
bool rtl9617c_bridge_mcast_stale(const struct sk_buff *skb)
{
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 const struct rtl9617c_bridge_mcast_ops *ops;
 bool valid=false;
 if(!saved || !saved->multicast_direct)return false;
 rcu_read_lock();
 ops=rcu_dereference(rtl_bridge_mcast);
 if(ops && ops->host_tx_valid)valid=ops->host_tx_valid(ops->context,skb);
 rcu_read_unlock();
 return !valid;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_stale);

bool rtl9617c_bridge_mcast_differs(const struct sk_buff *a,const struct sk_buff *b)
{
 const struct rtl9617c_bridge_frame *x=skb_ext_find(a,SKB_EXT_RTL9617C_BRIDGE);
 const struct rtl9617c_bridge_frame *y=skb_ext_find(b,SKB_EXT_RTL9617C_BRIDGE);
 bool xd=x && x->multicast_direct,yd=y && y->multicast_direct;
 if(xd!=yd)return true;
 return xd && (x->multicast_host_owner!=y->multicast_host_owner ||
  x->multicast_host_group!=y->multicast_host_group || x->multicast_epoch!=y->multicast_epoch);
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_differs);

void rtl9617c_bridge_mcast_copy(struct sk_buff *to,const struct sk_buff *from)
{
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(from,SKB_EXT_RTL9617C_BRIDGE);
 /* Sharing extensions is reference-counted and cannot fail allocation. */
 if(saved && saved->multicast_direct)skb_ext_copy(to,from);
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_copy);

/* Independent from FE epoch/cookie reuse and from FTTR ingress generation.
 * All pending CPU copies expire together; policy writers cannot sleep for
 * radio completion. Zero permanently closes stamps on counter exhaustion.
 */
static DEFINE_SPINLOCK(rtl_mcast_cpu_lock);
static u32 rtl_mcast_cpu_generation = 1;
static u32 rtl_mcast_cpu_inflight;
static unsigned int *rtl_mcast_cpu_mirrors[2];
static struct rtl_fttr_pe_drain *rtl_mcast_drains[2];

void rtl9617c_bridge_mcast_invalidate(void)
{
 unsigned long flags;
 spin_lock_irqsave(&rtl_mcast_cpu_lock,flags);
 if(rtl_mcast_cpu_generation)rtl_mcast_cpu_generation++;
 for(unsigned int i=0;i<2;i++)if(rtl_mcast_cpu_mirrors[i]) {
  WRITE_ONCE(*rtl_mcast_cpu_mirrors[i],cpu_to_le32(rtl_mcast_cpu_generation));
  WRITE_ONCE(rtl_mcast_drains[i]->request,cpu_to_le32(rtl_mcast_cpu_generation));
 }
 dma_wmb();
 spin_unlock_irqrestore(&rtl_mcast_cpu_lock,flags);
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_invalidate);

int rtl9617c_bridge_mcast_pe_register(unsigned int *mirror,struct rtl_fttr_pe_drain *drain)
{
 unsigned long flags;
 unsigned int slot=2;
 int ret=0;
 if(!mirror || !drain)return -EINVAL;
 spin_lock_irqsave(&rtl_mcast_cpu_lock,flags);
 for(unsigned int i=0;i<2;i++) {
  if(rtl_mcast_cpu_mirrors[i]==mirror){ret=-EEXIST;goto out;}
  if(!rtl_mcast_cpu_mirrors[i])slot=i;
 }
 if(slot==2){ret=-ENOSPC;goto out;}
 rtl_mcast_cpu_mirrors[slot]=mirror;rtl_mcast_drains[slot]=drain;
 WRITE_ONCE(drain->request,cpu_to_le32(rtl_mcast_cpu_generation));
 WRITE_ONCE(*mirror,cpu_to_le32(rtl_mcast_cpu_generation));dma_wmb();
out:
 spin_unlock_irqrestore(&rtl_mcast_cpu_lock,flags);return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_pe_register);

void rtl9617c_bridge_mcast_pe_unregister(unsigned int *mirror)
{
 unsigned long flags;
 spin_lock_irqsave(&rtl_mcast_cpu_lock,flags);
 for(unsigned int i=0;i<2;i++)if(rtl_mcast_cpu_mirrors[i]==mirror) {
  WRITE_ONCE(*mirror,0);dma_wmb();rtl_mcast_cpu_mirrors[i]=NULL;rtl_mcast_drains[i]=NULL;
 }
 spin_unlock_irqrestore(&rtl_mcast_cpu_lock,flags);
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_pe_unregister);

int rtl9617c_bridge_mcast_tx_stamp(const struct sk_buff *skb,u32 *stamp)
{
 const struct rtl9617c_bridge_frame *saved=skb_ext_find(skb,SKB_EXT_RTL9617C_BRIDGE);
 u32 generation;
 *stamp=0;
 if(!saved || !saved->multicast_direct)return 0;
 generation=READ_ONCE(rtl_mcast_cpu_generation);smp_rmb();
 if(!generation || rtl9617c_bridge_mcast_stale(skb))return -ESTALE;
 smp_rmb();
 if(generation!=READ_ONCE(rtl_mcast_cpu_generation))return -ESTALE;
 *stamp=generation;return 0;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_tx_stamp);

/* No callback or wait under this lock. The FE caller keeps its learned gate
 * closed while sampling idle, so no new valid stamp can join that interval.
 */
bool rtl9617c_bridge_mcast_tx_get(u32 stamp)
{
 unsigned long flags;
 bool accepted=false;
 if(!stamp)return true;
 spin_lock_irqsave(&rtl_mcast_cpu_lock,flags);
 if(stamp==rtl_mcast_cpu_generation && rtl_mcast_cpu_inflight!=~0U) {
  rtl_mcast_cpu_inflight++;accepted=true;
 }
 spin_unlock_irqrestore(&rtl_mcast_cpu_lock,flags);
 return accepted;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_tx_get);

void rtl9617c_bridge_mcast_tx_put(bool held)
{
 unsigned long flags;
 if(!held)return;
 spin_lock_irqsave(&rtl_mcast_cpu_lock,flags);
 if(!WARN_ON_ONCE(!rtl_mcast_cpu_inflight))rtl_mcast_cpu_inflight--;
 spin_unlock_irqrestore(&rtl_mcast_cpu_lock,flags);
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_tx_put);

bool rtl9617c_bridge_mcast_tx_idle(void)
{
 unsigned long flags;
 bool idle;
 spin_lock_irqsave(&rtl_mcast_cpu_lock,flags);
 idle=!rtl_mcast_cpu_inflight && rtl_mcast_cpu_generation;
 for(unsigned int i=0;idle && i<2;i++)if(rtl_mcast_drains[i]) {
  struct rtl_fttr_pe_drain *d=rtl_mcast_drains[i];
  u32 request=le32_to_cpu(READ_ONCE(d->request));
  if(!request || le32_to_cpu(READ_ONCE(d->ack))!=request)idle=false;
  dma_rmb();
  if(READ_ONCE(d->pending))idle=false;
 }
 spin_unlock_irqrestore(&rtl_mcast_cpu_lock,flags);
 return idle;
}
EXPORT_SYMBOL_GPL(rtl9617c_bridge_mcast_tx_idle);
