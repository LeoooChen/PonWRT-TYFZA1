/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_KEY_PN_H
#define RTL9617C_KEY_PN_H

#define RTL_KEY_PN_BASE 0x1e900
#define RTL_KEY_PN_COUNT 128
#define RTL_KEY_PN_REQUEST 30
#define RTL_KEY_PN_CAP_MAILBOX 213
#define RTL_KEY_PN_CAPABILITY 0x504e3031U

/* ARM CPUs serialize through one IRQ-safe lock. ARM and PE1 are the two
 * Peterson participants; coherent uncached storage and full barriers are
 * required. PE never waits: it yields a publication turn on contention.
 * The inactive counter bank is filled before publishing its sequence, so
 * a read can always return a committed PN even if its writer is paused.
 * No CAM generation is reused during a radio context's lifetime. */
struct rtl_key_pn_shared {
	unsigned int generation, arm, pe, turn, published;
	unsigned int low[2], high[2], reserved;
};
_Static_assert(sizeof(struct rtl_key_pn_shared) == 40, "shared key PN ABI");
_Static_assert(RTL_KEY_PN_BASE + RTL_KEY_PN_COUNT * sizeof(struct rtl_key_pn_shared) <= 0x20000,
	"shared key PN context bounds");

#ifdef __KERNEL__
#include <linux/atomic.h>
struct rtl_key_pn {
	struct rtl_key_pn_shared *shared;
	unsigned int generation;
};
int rtl9617c_key_pn_bind(struct rtl_key_pn *binding, struct rtl_key_pn_shared *shared,
	unsigned int generation, atomic64_t *fallback);
int rtl9617c_key_pn_unbind(struct rtl_key_pn *binding, atomic64_t *fallback);
int rtl9617c_key_pn_next(struct rtl_key_pn *binding, atomic64_t *fallback, u64 *pn);
u64 rtl9617c_key_pn_read(struct rtl_key_pn *binding, atomic64_t *fallback);
unsigned int rtl9617c_key_pn_token(struct rtl_key_pn *binding);
int rtl9617c_key_pn_set(struct rtl_key_pn *binding, atomic64_t *fallback, u64 pn);
#endif
#endif
