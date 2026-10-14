/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_SECURITY_H
#define RTL_AX_AP_SECURITY_H
#include "transport.h"

#define RTL_AX_SECURITY_ENTRIES 128

static inline u64 rtl_ax_security_read_pn(const struct rtl_ax_io *io,
	unsigned int macid)
{
	u32 low, high, before;

	/* Stock mac_sram_dbg_read: DMAC table selector13, 32 bytes per MACID. */
	/* Hardware TX can carry from low16 between two debug reads. */
	do {
		io->write32(io->ctx, 0xc04, 0x18800004 + macid * 32);
		before = io->read32(io->ctx, 0x40000);
		io->write32(io->ctx, 0xc04, 0x18800000 + macid * 32);
		low = io->read32(io->ctx, 0x40000);
		io->write32(io->ctx, 0xc04, 0x18800004 + macid * 32);
		high = io->read32(io->ctx, 0x40000);
	} while (before != high);
	return (u64)high << 16 | low >> 16;
}

struct rtl_ax_security_role {
	u8 valid, keyid[7], cam[7], tx_keyid;
};

/* Original C/XB SEC_CAM H2C: index/length plus a 20-byte cipher/key entry. */
static inline void rtl_ax_security_cam(u8 out[24], unsigned int index,
	const u8 *key)
{
	memset(out, 0, 24);
	rtl_ax_store32(out, index | 20U << 16);
	if (key) {
		rtl_ax_store32(out + 4, 6); /* CCMP128 */
		memcpy(out + 8, key, 16);
	}
}

/* Original upt_dctl_secinfo_entry -> C/XB DCTL V1. Slots0/1 are PTK,
 * slots2..5 are GTK. Each TX descriptor supplies its own packet number. */
static inline void rtl_ax_security_dctl(u8 out[68], unsigned int macid,
	const struct rtl_ax_security_role *role)
{
	u32 ids = 2U << 16 | (u32)role->tx_keyid << 9;
	unsigned int i;

	memset(out, 0, 68);
	for (i = 0; i < 7; i++)
		ids |= (u32)role->keyid[i] << (18 + i * 2);
	rtl_ax_store32(out, macid | 0x80);
	rtl_ax_store32(out + 20, ids);
	rtl_ax_store32(out + 24, role->valid | (u32)role->cam[0] << 8 |
		(u32)role->cam[1] << 16 | (u32)role->cam[2] << 24);
	rtl_ax_store32(out + 28, role->cam[3] | (u32)role->cam[4] << 8 |
		(u32)role->cam[5] << 16 | (u32)role->cam[6] << 24);
	rtl_ax_store32(out + 52, 0xffff8600);
	rtl_ax_store32(out + 56, 0xffffffff);
	rtl_ax_store32(out + 60, 0xffffffff);
}

static inline void rtl_ax_security_tx(u8 *descriptor, unsigned int index,
	unsigned int keyid, u64 pn)
{
	/* SW_SEC_IV selects this descriptor's PN; the chip inserts the IV. */
	rtl_ax_store32(descriptor + 4, rtl_ax_ap_read32(descriptor + 4) |
		RTL_AX_BIT(6) | 6 | keyid << 4);
	rtl_ax_store32(descriptor + 16, (u32)(pn & 0xffff) << 16);
	rtl_ax_store32(descriptor + 20, pn >> 16);
	rtl_ax_store32(descriptor + 40, rtl_ax_ap_read32(descriptor + 40) |
		RTL_AX_BIT(8) | index);
}
#endif
