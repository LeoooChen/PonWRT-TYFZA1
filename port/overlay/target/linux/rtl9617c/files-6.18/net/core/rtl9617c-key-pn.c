// SPDX-License-Identifier: GPL-2.0-only
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/spinlock.h>
#include <linux/rtl9617c-key-pn.h>

static DEFINE_SPINLOCK(key_pn_lock);

static u64 key_pn_read(struct rtl_key_pn_shared *s)
{
	u32 before, after, low, high;
	do {
		before = READ_ONCE(s->published);
		dma_rmb();
		low = READ_ONCE(s->low[before & 1]);
		high = READ_ONCE(s->high[before & 1]);
		dma_rmb();
		after = READ_ONCE(s->published);
	} while (before != after);
	return (u64)high << 32 | low;
}

static int key_pn_lock_peer(struct rtl_key_pn_shared *s)
{
	unsigned int i;
	WRITE_ONCE(s->arm, 1);
	mb();
	WRITE_ONCE(s->turn, 1);
	mb();
	for (i = 0; i < 1000; i++) {
		if (!READ_ONCE(s->pe) || !READ_ONCE(s->turn)) {
			mb();
			return 0;
		}
		udelay(1);
	}
	WRITE_ONCE(s->arm, 0);
	mb();
	return -ETIMEDOUT;
}

static void key_pn_unlock_peer(struct rtl_key_pn_shared *s)
{
	mb();
	WRITE_ONCE(s->arm, 0);
	mb();
}

int rtl9617c_key_pn_bind(struct rtl_key_pn *binding, struct rtl_key_pn_shared *s,
	unsigned int generation, atomic64_t *fallback)
{
	unsigned long flags;
	u64 initial;
	int ret;
	if (!s || !generation) return -EINVAL;
	spin_lock_irqsave(&key_pn_lock, flags);
	/* A software CCMP sender can race hardware installation. Read the
	 * initial PN inside the same lock used by that sender. */
	initial = atomic64_read(fallback) & 0xffffffffffffULL;
	if (binding->shared || binding->generation || READ_ONCE(s->generation)) { ret = -EBUSY; goto out; }
	ret = key_pn_lock_peer(s);
	if (ret) goto out;
	WRITE_ONCE(s->low[0], initial);
	WRITE_ONCE(s->high[0], initial >> 32);
	dma_wmb();
	WRITE_ONCE(s->published, 0);
	dma_wmb();
	WRITE_ONCE(s->generation, generation);
	binding->shared = s;
	binding->generation = generation;
	key_pn_unlock_peer(s);
out:
	spin_unlock_irqrestore(&key_pn_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_key_pn_bind);

int rtl9617c_key_pn_unbind(struct rtl_key_pn *binding, atomic64_t *fallback)
{
	struct rtl_key_pn_shared *s;
	unsigned long flags;
	int ret = 0;
	spin_lock_irqsave(&key_pn_lock, flags);
	s = binding->shared;
	if (!s) goto out;
	if (READ_ONCE(s->generation) && READ_ONCE(s->generation) != binding->generation) {
		binding->shared = NULL;
		binding->generation = ~0U;
		ret = -ESTALE; goto out;
	}
	WRITE_ONCE(s->generation, 0);
	mb();
	ret = key_pn_lock_peer(s);
	if (ret) {
		/* Linux can free a disabled key despite driver failure. Detach the
		 * borrowed memory but poison this key's software fallback: a PE
		 * already inside its critical section may still commit one PN. */
		binding->shared = NULL;
		binding->generation = ~0U;
		goto out;
	}
	atomic64_set(fallback, key_pn_read(s));
	binding->shared = NULL;
	binding->generation = 0;
	key_pn_unlock_peer(s);
out:
	spin_unlock_irqrestore(&key_pn_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_key_pn_unbind);

int rtl9617c_key_pn_next(struct rtl_key_pn *binding, atomic64_t *fallback, u64 *pn)
{
	struct rtl_key_pn_shared *s;
	unsigned long flags;
	u32 published;
	int ret = 0;
	spin_lock_irqsave(&key_pn_lock, flags);
	s = binding->shared;
	if (!s) {
		if (binding->generation) ret = -EKEYREVOKED;
		else *pn = atomic64_inc_return(fallback);
		goto out;
	}
	if (!binding->generation || READ_ONCE(s->generation) != binding->generation) {
		ret = -EKEYREVOKED; goto out;
	}
	ret = key_pn_lock_peer(s);
	if (ret) goto out;
	*pn = (key_pn_read(s) + 1) & 0xffffffffffffULL;
	published = READ_ONCE(s->published) + 1;
	WRITE_ONCE(s->low[published & 1], *pn);
	WRITE_ONCE(s->high[published & 1], *pn >> 32);
	dma_wmb();
	WRITE_ONCE(s->published, published);
	atomic64_set(fallback, *pn);
	key_pn_unlock_peer(s);
out:
	spin_unlock_irqrestore(&key_pn_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_key_pn_next);

u64 rtl9617c_key_pn_read(struct rtl_key_pn *binding, atomic64_t *fallback)
{
	unsigned long flags;
	u64 pn;
	spin_lock_irqsave(&key_pn_lock, flags);
	pn = binding->shared ?
		key_pn_read(binding->shared) : atomic64_read(fallback);
	spin_unlock_irqrestore(&key_pn_lock, flags);
	return pn;
}
EXPORT_SYMBOL_GPL(rtl9617c_key_pn_read);

unsigned int rtl9617c_key_pn_token(struct rtl_key_pn *binding)
{
	unsigned long flags;
	u32 generation = 0;
	spin_lock_irqsave(&key_pn_lock, flags);
	if (binding->shared && READ_ONCE(binding->shared->generation) == binding->generation)
		generation = binding->generation;
	spin_unlock_irqrestore(&key_pn_lock, flags);
	return generation;
}
EXPORT_SYMBOL_GPL(rtl9617c_key_pn_token);

int rtl9617c_key_pn_set(struct rtl_key_pn *binding, atomic64_t *fallback, u64 pn)
{
	unsigned long flags;
	int ret = 0;
	spin_lock_irqsave(&key_pn_lock, flags);
	/* debugfs must not silently alter only the stale fallback, or rewind
	 * a shared key while PE/PCIe still owns encrypted requests. */
	if (binding->shared || binding->generation) ret = -EBUSY;
	else atomic64_set(fallback, pn);
	spin_unlock_irqrestore(&key_pn_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_key_pn_set);
