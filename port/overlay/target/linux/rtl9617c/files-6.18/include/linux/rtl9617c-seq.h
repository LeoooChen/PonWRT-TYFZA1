/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_SEQ_H
#define RTL9617C_SEQ_H
#define RTL_SEQ_BASE 0x1fd00
#define RTL_SEQ_REQUEST 31
#define RTL_SEQ_CAP_MAILBOX 214
#define RTL_SEQ_CAPABILITY 0x53455131U
#define RTL_SEQ_ASSIGNED 0x80000000U

/* One non-QoS sequence space per radio's single AP/STA interface. The
 * sequence is the next12-bit value, not a sequence-control/fragment field.
 * ARM CPUs serialize locally; ARM and PE1 use the same bounded two-party
 * handshake as shared GTK PN. All fields are coherent little-endian. */
struct rtl_seq_shared {
	unsigned int generation, arm, pe, turn, next;
};
_Static_assert(RTL_SEQ_BASE + sizeof(struct rtl_seq_shared) <= 0x20000,
	"shared sequence context bounds");
#ifdef __KERNEL__
#include <linux/types.h>
struct rtl_seq_binding {
	struct rtl_seq_shared *shared;
	unsigned int generation;
};
int rtl9617c_seq_bind(struct rtl_seq_binding *binding, struct rtl_seq_shared *shared,
	unsigned int generation, u16 *sequence);
int rtl9617c_seq_unbind(struct rtl_seq_binding *binding, u16 *sequence);
int rtl9617c_seq_next(struct rtl_seq_binding *binding, u16 *sequence, u16 *result);
struct ieee80211_vif;
int ieee80211_rtl9617c_seq_bind(struct ieee80211_vif *vif, struct rtl_seq_shared *shared,
	unsigned int generation);
int ieee80211_rtl9617c_seq_unbind(struct ieee80211_vif *vif);
int ieee80211_rtl9617c_next_sequence(struct ieee80211_vif *vif, u16 *sequence);
#endif
#endif
