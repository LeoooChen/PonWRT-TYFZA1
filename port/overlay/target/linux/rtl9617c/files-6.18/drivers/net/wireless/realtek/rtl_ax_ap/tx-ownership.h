/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_TX_OWNERSHIP_H
#define RTL_AX_TX_OWNERSHIP_H
/* Request word5 bit15 is independent of word4 bit23 (STA addressing). */
#define RTL_AX_TX_AGG_PRELOAD (1U << 15)
/* PE-visible DDR tables. Software request IDs and PCIe WP tags are separate. */
#define RTL_AX_NI_REQUESTS 2048
#define RTL_AX_NI_DISCARD RTL_AX_NI_REQUESTS
#define RTL_AX_NI_OWNER_SLOTS (RTL_AX_NI_REQUESTS + 1)
#define RTL_AX_NI_ID_BASE 256
#define RTL_AX_NI_ID_END (RTL_AX_NI_ID_BASE + RTL_AX_NI_REQUESTS)
#define RTL_AX_NI_OWNERS 0x20000
#define RTL_AX_NI_RATES 0x25000
#define RTL_AX_NI_GENERATIONS 0x28000
#define RTL_AX_NI_TIDS 0x2b000
#define RTL_AX_NI_FREE 0x2e000
#define RTL_AX_NI_COMPLETIONS 0x30000
#define RTL_AX_NI_COMPLETION_MASK 4095
#define RTL_AX_TX_OWNER_STATE 0x34000
#define RTL_AX_NI_COOKIES 0x35000
/* Per-peer data statistics/policy are independent of recyclable request IDs. */
#define RTL_AX_TX_PEER_STATS 0x38000
#define RTL_AX_TX_PEER_AMSDU 0x39000
/* PE-owned per-TID byte count, timer origin/armed and batch drain state. */
#define RTL_AX_TX_AMSDU_QUEUES 0x3a000
#define RTL_AX_TX_NI_AC_PENDING 0x3e000
#define RTL_AX_TX_ARM_AC_PENDING 0x3e800
#define RTL_AX_TX_WMM_QUOTA 0x3f000
#define RTL_AX_WP_TAG_MAP 0x40000
#define RTL_AX_WP_FREE 0x58000
#define RTL_AX_TX_PEER_FLOWS 0x60000
#define RTL_AX_WP_TAGS 4095
#define RTL_AX_WP_TAG_INVALID 4095
#define RTL_AX_WP_TAG_STRIDE 4096
/* state: NI free read/write, DMA0 free read/write, per-ring tag cursors. */
static inline unsigned int rtl_ax_tx_id_is_ni(unsigned int id)
{
	return id >= RTL_AX_NI_ID_BASE && id < RTL_AX_NI_ID_END;
}
static inline void rtl_ax_tx_ownership_init(void *memory, unsigned int requests)
{
	unsigned char *base = memory;
	unsigned int *state = (void *)(base + RTL_AX_TX_OWNER_STATE);
	unsigned short *free_owner = (void *)(base + RTL_AX_NI_FREE);
	unsigned short *free_tag = (void *)(base + RTL_AX_WP_FREE);
	for (unsigned int i = 0; i < requests; i++) free_owner[i] = i;
	for (unsigned int i = 0; i < RTL_AX_WP_TAGS; i++) free_tag[i] = i;
	state[0] = 0; state[1] = requests;
	state[2] = 0; state[3] = 0;
	for (unsigned int i = 0; i < 6; i++) state[4 + i] = 1;
}
#endif
