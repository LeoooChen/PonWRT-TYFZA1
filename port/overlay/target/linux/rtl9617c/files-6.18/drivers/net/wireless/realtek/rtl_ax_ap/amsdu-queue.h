/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AMSDU_QUEUE_H
#define RTL_AX_AMSDU_QUEUE_H
/* txsc_amsdu_enqueue@eaf00 and txsc_amsdu_deque_check@ead18,
 * ordinary tx_mode=0, size mode=1. Bits above the size/count carry
 * the host's two-second traffic decisions to the PE queue owner. */
#define RTL_AX_AMSDU_BATCH (1U << 24)
#define RTL_AX_AMSDU_SHORT_TIMER (1U << 25)
static inline unsigned int rtl_ax_amsdu_queue_policy(unsigned int bytes,
	unsigned int num, unsigned int bandwidth, unsigned int band5g,
	unsigned int tx_kbits, unsigned int rx_kbits)
{
	unsigned int tx = tx_kbits >> 10, rx = rx_kbits >> 10;
	return bytes | num << 16 |
		(bandwidth == 3 && tx > 1300 ? RTL_AX_AMSDU_BATCH : 0) |
		(band5g && rx > 16 && rx < 680 && tx < 16 ? RTL_AX_AMSDU_SHORT_TIMER : 0);
}
static inline unsigned int rtl_ax_amsdu_queue_ready(unsigned int policy,
	unsigned int count, unsigned int bytes)
{
	unsigned int num = policy >> 16 & 255, size = policy & 65535;
	if (!num) return 1;
	if (policy & RTL_AX_AMSDU_BATCH) return count >= 128;
	return count >= num && (bytes + count * 8 >= size || count >= 9);
}
static inline unsigned int rtl_ax_amsdu_queue_timeout(unsigned int policy)
{
	return policy & RTL_AX_AMSDU_SHORT_TIMER ? 4000 : 10000;
}
#endif
