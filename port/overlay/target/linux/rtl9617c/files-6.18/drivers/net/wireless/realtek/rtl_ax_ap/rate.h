/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RATE_H
#define RTL_AX_AP_RATE_H

struct rtl_ax_phy_signal { u8 macid, rssi[3]; };

static inline int rtl_ax_ppdu_signal(const u8 *data, unsigned int bytes,
	struct rtl_ax_phy_signal signal[8])
{
	u32 first, second, users, offset;
	unsigned int i, count = 0;
	const u8 *phy;
	u8 rpl;

	if (bytes < 8) return -EINVAL;
	first = get_unaligned_le32(data); second = get_unaligned_le32(data + 4);
	users = first & 15;
	if (users > 8) return -EINVAL;
	offset = 8 + ((users + 1) & ~1U) * 4 + (first & BIT(29) ? 96 : 0) + ((second >> 16) & 255) * 8;
	if (offset > bytes || bytes - offset < 8) return -EINVAL;
	phy = data + offset;
	if (!(phy[0] & BIT(7)) || phy[1] * 8 > bytes - offset || phy[1] < 1) return -EINVAL;
	/* Original halbb converts TD RSSI to the IE0/1 frequency-domain RPL. */
	if (phy[1] < 3 || (phy[8] & 31) > 1) return 0;
	rpl = phy[9];
	for (i = 0; i < users; i++) {
		u32 user = get_unaligned_le32(data + 8 + i * 4);
		unsigned int macid = (user >> 8) & 255;
		if (!(user & BIT(0)) || macid >= 128) continue;
		signal[count].macid = macid;
		signal[count].rssi[0] = rpl;
		signal[count].rssi[1] = phy[4] ? rpl + phy[4] - phy[3] : 0;
		signal[count].rssi[2] = phy[5] ? rpl + phy[5] - phy[3] : 0;
		count++;
	}
	return count;
}

static inline void rtl_ax_ra_ht20(u8 command[16], u8 macid, u16 legacy, u16 ht, bool sgi)
{
	u64 mask = (legacy & 0x15) | (u64)(ht & 255) << 12 |
		(u64)(ht >> 8) << 24 | BIT_ULL(63);

	memset(command, 0, 16);
	command[0] = 0x0e; /* CCK/OFDM/HT, 20 MHz, firmware RA enabled. */
	command[1] = macid;
	command[2] = 0x10 | (sgi ? 0x20 : 0); /* Update all; RSSI unknown, init level0. */
	command[3] = !!(ht >> 8);
	put_unaligned_le64(mask, command + 4);
	command[13] = 0x38; /* Original RFE54 fixed CSI GI/LTF. */
	command[15] = 0x80; /* Original 8192XB RA debug-register format. */
}

static inline void rtl_ax_ra_vht80(u8 command[16], u8 macid, u16 legacy, u16 mcs, bool sgi)
{
	u64 mask = (legacy & 0x10) | BIT_ULL(63);
	u8 streams = 0;
	unsigned int i;

	for (i = 0; i < 2; i++) {
		unsigned int cap = (mcs >> (i * 2)) & 3;
		if (cap == 3) continue;
		mask |= ((1ULL << (8 + cap)) - 1) << (12 + i * 12);
		streams = i;
	}
	memset(command, 0, 16);
	command[0] = 0x94; /* OFDM/VHT, 80 MHz. */
	command[1] = macid;
	command[2] = 0x10 | (sgi ? 0x20 : 0);
	command[3] = streams;
	put_unaligned_le64(mask, command + 4);
	command[13] = 0x38;
	command[15] = 0x20; /* 5 GHz, original 8852C report format. */
}
static inline void rtl_ax_ra_he(u8 command[16], u8 macid, u16 legacy, u16 mcs,
	u8 phy1, u8 phy7, bool band5g, u8 bandwidth)
{
	u64 mask = (legacy & (band5g ? 0x10 : 0x15)) | BIT_ULL(63);
	u8 streams = 0;
	unsigned int i;
	/* RA byte3 carries optional GI/LTF capability bits, not a PHY rate enum. */
	u8 giltf_caps = phy7 & IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI ? 1 :
		phy1 & IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US ? 2 : 0;

	for (i = 0; i < 2; i++) {
		unsigned int cap = (mcs >> (i * 2)) & 3;
		if (cap == 3) continue;
		mask |= ((1ULL << (8 + cap * 2)) - 1) << (12 + i * 12);
		streams = i;
	}
	memset(command, 0, 16);
	/* Original mode mapping: OFDM2/HE16, plus CCK1 on 2G; BW in bits7:6. */
	command[0] = (band5g ? 0x24 : 0x26) | bandwidth << 6;
	command[1] = macid;
	command[2] = 0x10 | (phy1 & IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD ? 0x40 : 0);
	command[3] = streams | giltf_caps << 3;
	put_unaligned_le64(mask, command + 4);
	command[13] = 0x38;
	command[15] = band5g ? 0x20 : 0x80;
}
#endif
