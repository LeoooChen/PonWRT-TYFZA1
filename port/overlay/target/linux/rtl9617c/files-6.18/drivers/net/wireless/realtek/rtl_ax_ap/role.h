/* SPDX-License-Identifier: GPL-2.0-only */
/* Native AP self-role payloads. Original TYFZA1 ELF:
 * _hal_stainfo_to_macrinfo@1a8390 -> mac_add_role@22f1f0 ->
 * set_macid_pause@21c82c, mac_fw_role_maintain@22db0c,
 * mac_h2c_join_info@22dc7c, mac_upd_addr_cam@1e7cd8.
 * The independently encoded wire format is shared by C and XB.
 */
#ifndef RTL_AX_AP_ROLE_H
#define RTL_AX_AP_ROLE_H

#include "transport.h"

struct rtl_ax_station {
	u8 macid;
};

static inline void rtl_ax_station_drop(u8 out[24], unsigned int macid, unsigned int ac)
{
	/* Original h2c_pkt_drop; both board radios use SCC band0/port0. */
	memset(out, 0, 24);
	rtl_ax_store32(out, ac | macid << 8);
}

/* Actual C/XB ops+0x118 selects the chip-specific CCTL V1 encoder.
 * rtw_hal_add_sta_entry sets host antenna fields, but this encoder does
 * not put those legacy fields into its command. Both actual callbacks
 * encode only these selected initial masks. */
static inline void rtl_ax_station_cmac(unsigned char out[68], unsigned int macid)
{
	memset(out, 0, 68);
	rtl_ax_store32(out, macid | 0x80);
	rtl_ax_store32(out + 60, 0x0f0c0000);
}

/* Original rtw_hal_update_cctrl_tbl -> mac_upd_cctl_info for a legacy
 * AP station: fallback off, port/MBID0, retry limits255 (hardware default),
 * no aggregation/PPE override. Wire layout follows the actual V1 ops. */
static inline void rtl_ax_station_cmac_connected(unsigned char out[68],
	unsigned int macid, bool band5g, unsigned int aggregate)
{
	static const u32 words[17] = {
		0x80, 0x0600019b, 0x00080000, 0x00002000, 0, 0, 0, 0, 0,
		0x060001ff, 0xf1ff0800, 0x000060c0, 0x00000300, 0x000010f7,
		0, 0x000200ff, 0,
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(words); i++)
		rtl_ax_store32(out + i * 4, words[i]);
	rtl_ax_store32(out, macid | 0x80);
	/* Original RTS fallback is CCK1 on2G and OFDM6 on5G. */
	if (band5g)
		rtl_ax_store32(out + 8, words[2] | BIT(30));
	if (aggregate) {
		/* Actual rtw_hal_update_cctrl_tbl -> CCTL V1 output for num_ampdu:
		 * masks/packing differ from the same-named generic CCTL encoder. */
		rtl_ax_store32(out + 16, (aggregate - 1) & 0xfe);
		rtl_ax_store32(out + 48, 0x3ff);
	}
}

static inline u8 rtl_ax_he_padding(const u8 *phy, const u8 *ppe, unsigned int nss)
{
	u8 ru_mask, padding = 0x55;
	unsigned int stream, bw, position = 7;

	if (!(phy[6] & IEEE80211_HE_PHY_CAP6_PPE_THRESHOLD_PRESENT))
		return (phy[9] >> IEEE80211_HE_PHY_CAP9_NOMINAL_PKT_PADDING_POS) * 0x55;
	ru_mask = (ppe[0] >> 3) & 15;
	/* Original PPE parser stores zero for omitted RUs; CCTL maps it to 8us. */
	for (stream = 0; stream <= (ppe[0] & 7); stream++)
		for (bw = 0; bw < 4; bw++) {
			unsigned int shift, value, pad;

			if (!(ru_mask & BIT(bw))) continue;
			shift = position & 7;
			value = ppe[position / 8] >> shift;
			if (shift > 2) value |= ppe[position / 8 + 1] << (8 - shift);
			position += 6;
			if (stream + 1 != nss) continue;
			pad = ((value >> 3) & 7) != 7 ? 1 : (value & 7) != 7 ? 2 : 0;
			padding = (padding & ~(3U << (bw * 2))) | pad << (bw * 2);
		}
	if ((ru_mask & 12) == 4)
		padding = (padding & 63) | ((padding >> 4) & 3) << 6;
	return padding;
}

static inline void rtl_ax_station_cmac_he(u8 out[68], u8 padding)
{
	/* Original CCTL V1: HE BSR format in word6, four padding fields in word7. */
	rtl_ax_store32(out + 24, rtl_ax_ap_read32(out + 24) | BIT(13));
	rtl_ax_store32(out + 56, rtl_ax_ap_read32(out + 56) | BIT(13));
	rtl_ax_store32(out + 28, (rtl_ax_ap_read32(out + 28) & ~0xffU) | padding);
}

static inline void rtl_ax_peer_cam(unsigned char out[60], unsigned int macid,
	const u8 *self, const u8 *peer, unsigned int aid, bool valid)
{
	unsigned int i, self_hash = 0, peer_hash = 0;

	for (i = 0; i < 6; i++) {
		self_hash ^= self[i];
		peer_hash ^= peer[i];
	}
	memset(out, 0, 60);
	rtl_ax_store32(out + 4, macid | 32U << 16);
	rtl_ax_store32(out + 8, (valid ? 7 : 6) | self_hash << 16 | peer_hash << 24);
	memcpy(out + 16, self, 6);
	memcpy(out + 22, peer, 6);
	rtl_ax_store32(out + 32, macid);
	rtl_ax_store32(out + 36, aid & 0xfff);
	rtl_ax_store32(out + 48, 8U << 16);
	out[52] = 0xfd;
	memcpy(out + 54, self, 6);
}

static inline void rtl_ax_role_unpause(unsigned char out[32], unsigned int macid)
{
	memset(out, 0, 32);
	/* First four words are the pause bitmap; last four are its mask.
	 * role_init clears this MACID's pause bit, retaining the other IDs. */
	rtl_ax_store32(out + 16 + (macid / 32) * 4, RTL_AX_BIT(macid % 32));
}

static inline void rtl_ax_role_create(unsigned char out[4], unsigned int macid)
{
	/* self_role AP=1, update CREATE=0, wifi_role AP=2. */
	rtl_ax_store32(out, macid | (1U << 8) | (2U << 13));
}

static inline void rtl_ax_role_join(unsigned char out[4], unsigned int macid)
{
	/* Original open legacy self-station creation: DISCONN=1,
	 * SCC band0/port0, TF padding1, net_type AP3, wifi AP2, self AP1.
	 * AP enable and peer association have subsequent role transitions. */
	rtl_ax_store32(out, macid | 0x4b040100);
}

static inline void rtl_ax_role_cam(unsigned char out[60], unsigned int macid,
				   const unsigned char address[6])
{
	unsigned int hash = 0, i;

	for (i = 0; i < 6; i++)
		hash ^= address[i];
	memset(out, 0, 60);
	/* role_info_init/get_addr_cam_size: 32-byte address CAM, index0;
	 * valid1, network AP3, band0, full six-byte SMA/TMA hash. */
	rtl_ax_store32(out + 4, 32U << 16);
	rtl_ax_store32(out + 8, 7 | hash << 16 | hash << 24);
	memcpy(out + 16, address, 6);
	memcpy(out + 22, address, 6);
	rtl_ax_store32(out + 32, macid);
	/* BSSID CAM index0, length8, valid1, band0, address mask3f,
	 * BSS color0 and same BSSID as the self-station MAC. */
	rtl_ax_store32(out + 48, 8U << 16);
	out[52] = 0xfd;
	memcpy(out + 54, address, 6);
}

#endif
