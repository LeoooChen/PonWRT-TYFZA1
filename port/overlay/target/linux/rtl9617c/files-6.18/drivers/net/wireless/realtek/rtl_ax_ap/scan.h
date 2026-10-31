/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_SCAN_H
#define RTL_AX_AP_SCAN_H

/* MAC PPDU header precedes the PHY header, even for unassociated BSSes.
 * RPL in PHY IE0/1 is U8.1 with a -110 dBm origin (not header TD RSSI). */
static inline int rtl_ax_scan_rpl(const u8 *data, unsigned int bytes)
{
	u32 first, second, users, offset;
	const u8 *phy;
	unsigned int length, id, page;

	if (bytes < 8) return -EINVAL;
	first = get_unaligned_le32(data);
	second = get_unaligned_le32(data + 4);
	users = first & 15;
	if (users > 8) return -EINVAL;
	offset = 8 + ((users + 1) & ~1U) * 4 +
		(first & BIT(29) ? 96 : 0) + ((second >> 16) & 255) * 8;
	if (offset > bytes || bytes - offset < 8) return -EINVAL;
	phy = data + offset;
	length = phy[1] * 8;
	page = phy[0] & 31;
	if (!(phy[0] & BIT(7)) || length < 24 || length > bytes - offset)
		return -EINVAL;
	if (page < 11 || page > 15) return -EINVAL;
	id = phy[8] & 31;
	if (id != (page == 11 ? 0 : 1) || (id == 1 && length < 40)) return -EINVAL;
	return phy[9];
}

static inline bool rtl_ax_scan_ppdu_match(u32 frame0, u32 frame1,
	u32 report0, u32 report1)
{
	/* Original C/XB WD: BB22, PPDU type0:3/count4:6, rate16:24,
	 * GI25:27 and BW29:31. Never reuse an RSSI from a different PPDU. */
	return !((frame0 ^ report0) & BIT(22)) &&
		!((frame1 ^ report1) & 0xefff007f);
}
#endif
