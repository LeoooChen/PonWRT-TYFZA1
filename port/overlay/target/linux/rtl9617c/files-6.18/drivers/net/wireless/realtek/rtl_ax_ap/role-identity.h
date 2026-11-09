/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_ROLE_IDENTITY_H
#define RTL_AX_ROLE_IDENTITY_H

/* One identity feeds every hardware role representation. MAC band0 on this
 * board is independent of RF band. Original AP2/VAP3/STA1/mesh6 role values. */
struct rtl_ax_role_identity {
	u8 port, mbid, macid, type;
};
/* Retained in the radio until hardware cleanup, independent of vif memory. */
struct rtl_ax_self_role {
 u8 stage, type, color;
 bool retiring, started, beacon, stopping;
 int bss_result;
 int result;
 u8 cam[60];
};
struct rtl_ax_vif {
	struct rtl_ax_role_identity identity;
 u32 generation;
};

static inline void rtl_ax_identity_maintain(u8 out[4], const struct rtl_ax_role_identity *identity)
{
	u32 word = rtl_ax_ap_read32(out);

	rtl_ax_store32(out, (word & ~0x1e0ffU) | identity->macid | (u32)identity->type << 13);
}

static inline void rtl_ax_identity_join(u8 out[4], const struct rtl_ax_role_identity *identity)
{
	u32 word = rtl_ax_ap_read32(out);

	rtl_ax_store32(out, (word & ~0x3ce000ffU) | identity->macid |
		(u32)identity->port << 21 | (u32)identity->type << 26);
}

static inline void rtl_ax_identity_cam(u8 out[60], const struct rtl_ax_role_identity *identity)
{
	u32 word = rtl_ax_ap_read32(out + 32);

	/* CAM port[10:8] and TSF synchronization port[13:11]. */
	rtl_ax_store32(out + 32, (word & ~0x3fffU) | identity->macid |
		(u32)identity->port << 8 | (u32)identity->port << 11);
}

static inline void rtl_ax_identity_cmac(u8 out[68], const struct rtl_ax_role_identity *identity)
{
	/* C/XB CCTL V1 word5, independently masked from RA and HE fields. */
	u32 word = rtl_ax_ap_read32(out + 20);
	u32 mask = rtl_ax_ap_read32(out + 52) & 0xf7;

	rtl_ax_store32(out + 20, (word & ~mask) |
		((identity->port | (u32)identity->mbid << 4) & mask));
}

static inline void rtl_ax_identity_beacon(u8 out[16], const struct rtl_ax_role_identity *identity)
{
	out[0] = identity->port;
	out[1] = identity->mbid;
	out[2] = 0; /* MAC band0, not the physical RF band. */
	out[4] = identity->macid;
}
#endif
