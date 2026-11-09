// SPDX-License-Identifier: GPL-2.0-only
#include <linux/delay.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/spinlock.h>
#include <linux/rtl9617c-seq.h>

static DEFINE_SPINLOCK(sequence_lock);

static int sequence_peer_lock(struct rtl_seq_shared *s)
{
	unsigned int i;
	WRITE_ONCE(s->arm, 1); mb();
	WRITE_ONCE(s->turn, 1); mb();
	for (i = 0; i < 1000; i++) {
		if (!READ_ONCE(s->pe) || !READ_ONCE(s->turn)) { mb(); return 0; }
		udelay(1);
	}
	WRITE_ONCE(s->arm, 0); mb();
	return -ETIMEDOUT;
}

static void sequence_peer_unlock(struct rtl_seq_shared *s)
{
	mb(); WRITE_ONCE(s->arm, 0); mb();
}

int rtl9617c_seq_bind(struct rtl_seq_binding *binding, struct rtl_seq_shared *s,
	unsigned int generation, u16 *sequence)
{
	unsigned long flags;
	int ret;
	if (!s || !generation || generation > RTL_SEQ_GENERATION_MASK) return -EINVAL;
	spin_lock_irqsave(&sequence_lock, flags);
	if (binding->shared || binding->generation || READ_ONCE(s->generation)) { ret = -EBUSY; goto out; }
	ret = sequence_peer_lock(s);
	if (ret) goto out;
	WRITE_ONCE(s->next, *sequence >> 4);
	mb(); WRITE_ONCE(s->generation, generation);
	binding->shared = s; binding->generation = generation;
	sequence_peer_unlock(s);
out:
	spin_unlock_irqrestore(&sequence_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_seq_bind);

int rtl9617c_seq_unbind(struct rtl_seq_binding *binding, u16 *sequence)
{
	struct rtl_seq_shared *s;
	unsigned long flags;
	int ret = 0;
	spin_lock_irqsave(&sequence_lock, flags);
	s = binding->shared;
	if (!s) goto out;
	if (READ_ONCE(s->generation) != binding->generation) { ret = -ESTALE; goto detach; }
	WRITE_ONCE(s->generation, 0); mb();
	ret = sequence_peer_lock(s);
	if (ret) goto detach;
	*sequence = READ_ONCE(s->next) << 4;
	sequence_peer_unlock(s);
detach:
	/* remove_interface cannot retain a pointer into the radio allocation.
	 * A timeout poisons this interface until it is destroyed, since a PE
	 * already in its critical section can still consume another sequence. */
	binding->shared = NULL;
	binding->generation = ret ? ~0U : 0;
out:
	spin_unlock_irqrestore(&sequence_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_seq_unbind);

int rtl9617c_seq_next(struct rtl_seq_binding *binding, u16 *sequence, u16 *result)
{
	struct rtl_seq_shared *s;
	unsigned long flags;
	int ret = 0;
	spin_lock_irqsave(&sequence_lock, flags);
	s = binding->shared;
	if (!s) {
		if (binding->generation) ret = -ESTALE;
		else { *result = *sequence; *sequence += 0x10; }
		goto out;
	}
	if (READ_ONCE(s->generation) != binding->generation) { ret = -ESTALE; goto out; }
	ret = sequence_peer_lock(s);
	if (ret) goto out;
	*result = READ_ONCE(s->next) << 4;
	*sequence = *result + 0x10;
	WRITE_ONCE(s->next, *sequence >> 4);
	sequence_peer_unlock(s);
out:
	spin_unlock_irqrestore(&sequence_lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(rtl9617c_seq_next);
