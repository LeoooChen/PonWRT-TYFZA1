/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_SEQ_H
#define RTL9617C_SEQ_H
#define RTL_SEQ_BASE 0x1fd00
#define RTL_SEQ_EXTRA_BASE 0x68c00
#define RTL_SEQ_DOMAINS 12
#define RTL_SEQ_CPU_TOKENS 0x68800
#define RTL_SEQ_DOMAIN_SHIFT 27
#define RTL_SEQ_GENERATION_MASK ((1U << RTL_SEQ_DOMAIN_SHIFT) - 1)
#define RTL_SEQ_REQUEST 31
#define RTL_SEQ_CAP_MAILBOX 214
#define RTL_SEQ_CAPABILITY 0x53455132U
#define RTL_SEQ_ASSIGNED 0x80000000U

/* One non-QoS sequence space per interface: port0 MBID0..7 and ports1..4. The
 * sequence is the next12-bit value, not a sequence-control/fragment field.
 * ARM CPUs serialize locally; ARM and PE1 use the same bounded two-party
 * handshake as shared GTK PN. All fields are coherent little-endian. */
struct rtl_seq_shared {
	unsigned int generation, arm, pe, turn, next;
};
_Static_assert(RTL_SEQ_BASE + sizeof(struct rtl_seq_shared) <= 0x20000,
	"shared sequence context bounds");
/* WD role identity is port[6:4], MBID[3:0]. Invalid combinations never
 * alias another interface. The root domain preserves its existing address. */
static inline unsigned int rtl_seq_domain(unsigned int role)
{
	unsigned int port = role >> 4, mbid = role & 15;
	return port ? (port <= 4 && !mbid ? 7 + port : RTL_SEQ_DOMAINS) :
		(mbid < 8 ? mbid : RTL_SEQ_DOMAINS);
}
static inline unsigned int rtl_seq_offset(unsigned int domain)
{
	return domain ? RTL_SEQ_EXTRA_BASE + (domain - 1) * sizeof(struct rtl_seq_shared) : RTL_SEQ_BASE;
}
_Static_assert(RTL_SEQ_EXTRA_BASE + (RTL_SEQ_DOMAINS - 1) * sizeof(struct rtl_seq_shared) <= 0x80000,
	"per-VIF sequence context bounds");
static inline unsigned int rtl_seq_token(void *memory, unsigned int role)
{
	unsigned int domain = rtl_seq_domain(role);
	if (domain >= RTL_SEQ_DOMAINS) return 0;
	volatile struct rtl_seq_shared *s = (void *)((unsigned char *)memory + rtl_seq_offset(domain));
	unsigned int generation = s->generation;
	return generation ? generation | domain << RTL_SEQ_DOMAIN_SHIFT : 0;
}
static inline int rtl_seq_current(void *memory, unsigned int token)
{
	unsigned int domain = token >> RTL_SEQ_DOMAIN_SHIFT;
	if (!(token & RTL_SEQ_GENERATION_MASK) || domain >= RTL_SEQ_DOMAINS) return 0;
	volatile struct rtl_seq_shared *s = (void *)((unsigned char *)memory + rtl_seq_offset(domain));
	return s->generation == (token & RTL_SEQ_GENERATION_MASK);
}
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
