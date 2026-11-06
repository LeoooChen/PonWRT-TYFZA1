// SPDX-License-Identifier: GPL-2.0-only
/* One native NI complex owns accelerated bridge multicast on this SoC. */
#include <linux/atomic.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/mutex.h>
#include <linux/netdevice.h>
#include <linux/rcupdate.h>
#include <linux/rtl9617c-bridge.h>

static DEFINE_MUTEX(rtl_bridge_lock);
/* This built-in owner outlives driver unload and any queued skb extensions. */
static atomic64_t rtl_bridge_ticket = ATOMIC64_INIT(0);
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
