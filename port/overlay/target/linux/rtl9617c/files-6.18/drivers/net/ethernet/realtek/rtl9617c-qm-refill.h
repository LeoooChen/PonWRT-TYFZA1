/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_QM_REFILL_H
#define RTL9617C_QM_REFILL_H

#define RTL_QM_REFILL_SIZE 256
struct rtl_qm_refill {
	unsigned int address[2][RTL_QM_REFILL_SIZE];
	unsigned int head[2], count[2], pending;
	unsigned int retries, returned, peak;
};

/* Each queue holds at most its hardware pool's 256 buffers. The caller checks
 * space before retiring an EPP entry; only NAPI changes queue ownership. */
static inline void rtl_qm_refill_add(struct rtl_qm_refill *queue, unsigned int command)
{
	unsigned int eq = (command & 15) - 4;
	unsigned int tail = (queue->head[eq] + queue->count[eq]) % RTL_QM_REFILL_SIZE;

	queue->address[eq][tail] = command & 0xffffff80U;
	queue->count[eq]++;
	queue->pending++;
	if (queue->pending > queue->peak)
		queue->peak = queue->pending;
}

static inline void rtl_qm_refill_drain(struct rtl_qm_refill *queue, void *context,
	int (*push)(void *context, unsigned int address, unsigned int eq), unsigned int budget)
{
	unsigned int eq, attempted = 0;

	/* A stalled EQ4 must not prevent EQ5 from returning its buffers. */
	for (eq = 0; eq < 2; eq++) {
		unsigned int limit = budget / 2;

		while (queue->count[eq] && limit-- && attempted++ < budget) {
			if (push(context, queue->address[eq][queue->head[eq]], eq + 4)) {
				queue->retries++;
				break;
			}
			queue->head[eq] = (queue->head[eq] + 1) % RTL_QM_REFILL_SIZE;
			queue->count[eq]--;
			queue->pending--;
			queue->returned++;
		}
	}
}
#endif
