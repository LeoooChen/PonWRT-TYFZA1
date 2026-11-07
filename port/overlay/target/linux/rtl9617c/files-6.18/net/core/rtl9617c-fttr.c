// SPDX-License-Identifier: GPL-2.0-only
/* FTTR software packet lifetime, independent of FE ownership and skb marks. */
#include <linux/atomic.h>
#include <linux/dma-mapping.h>
#include <linux/delay.h>
#include <linux/export.h>
#include <linux/errno.h>
#include <linux/if_vlan.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/jiffies.h>
#include <linux/rtl9617c-fttr.h>
#include <linux/spinlock.h>

static atomic64_t rtl_fttr_generation = ATOMIC64_INIT(1);
static DEFINE_SPINLOCK(rtl_fttr_pe_lock);
static struct rtl_fttr_pe_generation *rtl_fttr_pe_radios[2];
static struct rtl_fttr_pe_drain *rtl_fttr_pe_drains[2];
static u32 rtl_fttr_drain_sequence;
static u64 rtl_fttr_drained_generation;
static atomic_t rtl_fttr_tx_pending = ATOMIC_INIT(0);

static void rtl_fttr_pe_publish(struct rtl_fttr_pe_generation *mirror, u64 generation)
{
	WRITE_ONCE(mirror->low, cpu_to_le32((u32)generation | 1));
	dma_wmb();
	WRITE_ONCE(mirror->high, cpu_to_le32(generation >> 32));
	dma_wmb();
	WRITE_ONCE(mirror->low, cpu_to_le32((u32)generation));
	dma_wmb();
}

int rtl9617c_fttr_pe_register(struct rtl_fttr_pe_generation *mirror,
			    struct rtl_fttr_pe_drain *drain)
{
	unsigned long flags;
	int slot, ret = -ENOSPC;

	spin_lock_irqsave(&rtl_fttr_pe_lock, flags);
	for (slot = 0; slot < 2; slot++)
		if (rtl_fttr_pe_radios[slot] == mirror) { ret = -EEXIST; goto out; }
	for (slot = 0; slot < 2; slot++)
		if (!rtl_fttr_pe_radios[slot]) {
			rtl_fttr_pe_publish(mirror, rtl9617c_fttr_generation_read());
			WRITE_ONCE(drain->request, cpu_to_le32(rtl_fttr_drain_sequence));
			dma_wmb();
			rtl_fttr_pe_drains[slot] = drain;
			rtl_fttr_pe_radios[slot] = mirror;
			ret = 0;
			break;
		}
out:
	spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_pe_register);

void rtl9617c_fttr_pe_unregister(struct rtl_fttr_pe_generation *mirror)
{
	unsigned long flags;
	int slot;

	spin_lock_irqsave(&rtl_fttr_pe_lock, flags);
	for (slot = 0; slot < 2; slot++)
		if (rtl_fttr_pe_radios[slot] == mirror) {
			WRITE_ONCE(mirror->low, cpu_to_le32(1));
			dma_wmb();
			rtl_fttr_pe_radios[slot] = NULL;
			rtl_fttr_pe_drains[slot] = NULL;
		}
	spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_pe_unregister);

u64 rtl9617c_fttr_generation_read(void)
{
	return atomic64_read_acquire(&rtl_fttr_generation);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_generation_read);

/* Caller holds the registry lock so a joined radio cannot free its status
 * while ARM samples it. ACK is published after the final EQ PUSH fence. */
static bool rtl_fttr_pe_drained(u64 generation)
{
	int slot;

	for (slot = 0; slot < 2; slot++) {
		struct rtl_fttr_pe_drain *drain = rtl_fttr_pe_drains[slot];
		if (!drain) continue;
		if (rtl_fttr_drained_generation != generation) return false;
		if (le32_to_cpu(READ_ONCE(drain->ack)) != rtl_fttr_drain_sequence) return false;
		dma_rmb();
		if (READ_ONCE(drain->pending)) return false;
	}
	return true;
}

int rtl9617c_fttr_generation_set(bool active)
{
	u64 generation;
	unsigned long flags;
	int slot;

	ASSERT_RTNL();
	generation = rtl9617c_fttr_generation_read();
	if ((generation & 1) == !active)
		return 0;
	/* Do not make old skb identities valid again after counter exhaustion. */
	if (generation == U64_MAX)
		return -EOVERFLOW;
	spin_lock_irqsave(&rtl_fttr_pe_lock, flags);
	/* No old accepted radio request may cross a successful reopen. */
	if (active && (atomic_read_acquire(&rtl_fttr_tx_pending) || !rtl_fttr_pe_drained(generation))) {
		spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
		return -EBUSY;
	}
	/* Close PE mirrors before CPU observes closure; on open, both mirrors
	 * must be ready before a newly stamped skb can reach either radio. */
	for (slot = 0; slot < 2; slot++)
		if (rtl_fttr_pe_radios[slot])
			rtl_fttr_pe_publish(rtl_fttr_pe_radios[slot], generation + 1);
	atomic64_set_release(&rtl_fttr_generation, generation + 1);
	spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
	return 0;
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_generation_set);

bool rtl9617c_fttr_tx_get(u64 generation)
{
	unsigned long flags;
	bool accepted;

	if (!generation) return true;
	spin_lock_irqsave(&rtl_fttr_pe_lock, flags);
	accepted = !(generation & 1) && generation == rtl9617c_fttr_generation_read();
	if (accepted) atomic_inc(&rtl_fttr_tx_pending);
	spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
	return accepted;
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_tx_get);

void rtl9617c_fttr_tx_put(u64 generation)
{
	/* Keep the payload unmap/disposition before the zero seen by drain. */
	if (generation) atomic_dec_return_release(&rtl_fttr_tx_pending);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_tx_put);

int rtl9617c_fttr_tx_drain(void)
{
	unsigned long flags, deadline = jiffies + msecs_to_jiffies(1000);
	u64 generation;
	int slot;
	bool done;

	ASSERT_RTNL();
	/* RTNL keeps the generation closed throughout this sleep. tx_get uses
	 * the same lock as closure, including a sender already mapping an skb. */
	generation = rtl9617c_fttr_generation_read();
	if (!(generation & 1)) return -EBUSY;
	spin_lock_irqsave(&rtl_fttr_pe_lock, flags);
	rtl_fttr_drained_generation = 0;
	if (rtl_fttr_drain_sequence == U32_MAX) {
		spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
		return -EOVERFLOW;
	}
	rtl_fttr_drain_sequence++;
	for (slot = 0; slot < 2; slot++)
		if (rtl_fttr_pe_drains[slot])
			WRITE_ONCE(rtl_fttr_pe_drains[slot]->request, cpu_to_le32(rtl_fttr_drain_sequence));
	dma_wmb();
	spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
	for (;;) {
		spin_lock_irqsave(&rtl_fttr_pe_lock, flags);
		/* Tentatively identify this request for the shared ACK predicate;
		 * the lock prevents reopen observing it before the result is known. */
		rtl_fttr_drained_generation = generation;
		done = !atomic_read_acquire(&rtl_fttr_tx_pending) && rtl_fttr_pe_drained(generation);
		if (!done) rtl_fttr_drained_generation = 0;
		spin_unlock_irqrestore(&rtl_fttr_pe_lock, flags);
		if (done) return 0;
		if (time_after_eq(jiffies, deadline)) return -ETIMEDOUT;
		/* PE has no ARM callback. Poll only for this bounded control action;
		 * neither NI/FE/radio locks nor borrowed pointers span the sleep. */
		usleep_range(1000, 2000);
	}
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_tx_drain);

bool rtl9617c_fttr_stamp(struct sk_buff *skb)
{
	if (!skb->rtl9617c_fttr_generation)
		skb->rtl9617c_fttr_generation = rtl9617c_fttr_generation_read();
	return !rtl9617c_fttr_stale(skb);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_stamp);

bool rtl9617c_fttr_enqueue(struct sk_buff *skb, struct net_device *dev)
{
	bool tagged = skb_vlan_tag_present(skb);
	u8 endpoint;
	__be16 protocol = skb->protocol;

	while (is_vlan_dev(dev)) {
		tagged = true;
		dev = vlan_dev_real_dev(dev);
	}
	endpoint = READ_ONCE(dev->rtl9617c_fttr_egress);
	if (!endpoint)
		return true;
	/* AF_PACKET can declare ETH_P_ALL while supplying a VLAN on the wire.
	 * Inspect a present Ethernet header at enqueue; routed/NFQUEUE packets
	 * without it retain their IP protocol and cannot appear as management. */
	if (skb_mac_header_was_set(skb) && skb_mac_header(skb) == skb->data) {
		struct ethhdr storage;
		const struct ethhdr *eth = skb_header_pointer(skb, 0, sizeof(storage), &storage);
		if (!eth) return false;
		protocol = eth->h_proto;
	}
	if (!tagged && endpoint == 1 && READ_ONCE(dev->rtl9617c_fttr_omci) &&
	    protocol == htons(READ_ONCE(dev->rtl9617c_fttr_omci)))
		return true;
	return rtl9617c_fttr_stamp(skb);
}
EXPORT_SYMBOL_GPL(rtl9617c_fttr_enqueue);
