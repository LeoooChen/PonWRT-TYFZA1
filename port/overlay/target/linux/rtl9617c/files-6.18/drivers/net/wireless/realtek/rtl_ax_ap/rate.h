/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RATE_H
#define RTL_AX_AP_RATE_H

struct rtl_ax_ra_sample { u8 macid, rssi[3], paths; bool beacon; };
struct rtl_ax_ra_rssi {
	u16 ma[2][3], count[2]; /* Data/beacon; overall and independent RF paths. */
	u8 factor[2];
};

static inline void rtl_ax_ra_rssi_init(struct rtl_ax_ra_rssi *state)
{
	memset(state, 0, sizeof(*state));
	state->factor[0] = state->factor[1] = 2;
}

/* Actual halbb_rx_pkt_su_store_in_sta_info: samples are U8.1, MA is sample*16.
 * Each zero MA initializes independently; subtraction rounds before addition. */
static inline void rtl_ax_ra_rssi_sample(struct rtl_ax_ra_rssi *state,
	const struct rtl_ax_ra_sample *sample, u8 rssi[3])
{
	unsigned int kind = sample->beacon, shift = 4 - state->factor[kind], i;

	for (i = 0; i < 3; i++) {
		u16 *ma = &state->ma[kind][i];

		if (i && !(sample->paths & BIT(i - 1))) continue;
		*ma = !*ma ? sample->rssi[i] << 4 :
			*ma + (sample->rssi[i] << shift) - ((*ma << shift) >> 4);
	}
	state->count[kind]++;
	if (!kind) {
		rssi[0] = (state->ma[0][0] + 8) >> 4;
		rssi[1] = state->ma[0][1] >> 4;
		rssi[2] = state->ma[0][2] >> 4;
	}
}

/* halbb_cmn_info_self_update selects the next period's factor, then clears
 * the qualifying PHY-report counts from continuous collection. */
static inline void rtl_ax_ra_rssi_watchdog(struct rtl_ax_ra_rssi *state)
{
	u16 data = state->count[0];

	state->factor[0] = data > 100 ? 4 : data > 20 ? 3 : data > 5 ? 2 : 1;
	state->factor[1] = state->count[1] > 5 ? 2 : 1;
	state->count[0] = state->count[1] = 0;
}

/* Normal halbb_cmn_info_self_update selects data, then beacon RSSI. */
static inline u8 rtl_ax_bb_rssi(const struct rtl_ax_ra_rssi *state)
{
	u8 data = (state->ma[0][0] + 8) >> 4;

	return data ? data : (state->ma[1][0] + 8) >> 4;
}

/* Board hal_handle_rx_buffer -> halbb_physts_parsing -> halbb_cmn_rpt.
 * SU is WD1 PPDU type !=6/9/10; the parser uses only user0's MACID/flags. */
static inline int rtl_ax_ppdu_ra_signal(const u8 *data, unsigned int bytes,
	u8 ppdu_type, bool to_self, struct rtl_ax_ra_sample *sample)
{
	u32 first, second, users, offset, user;
	const u8 *phy;
	u8 average, a, b, type;

	if (!to_self || ppdu_type == 6 || ppdu_type == 9 || ppdu_type == 10) return 0;
	if (bytes < 8) return -EINVAL;
	first = get_unaligned_le32(data); second = get_unaligned_le32(data + 4);
	users = first & 15;
	if (users > 8) return -EINVAL;
	offset = 8 + ((users + 1) & ~1U) * 4 + (first & BIT(29) ? 96 : 0) + ((second >> 16) & 255) * 8;
	if (offset > bytes || bytes - offset < 8) return -EINVAL;
	phy = data + offset;
	if (!(phy[0] & BIT(7)) || phy[1] * 8 != bytes - offset || phy[1] < 1) return -EINVAL;
	type = phy[0] & 31;
	/* halbb_physts_parsing@2c5744: types0..5 and8 clear to-self/user flags. */
	if (!users || type <= 5 || type == 8) return 0;
	user = get_unaligned_le32(data + 8);
	if (!(user & BIT(0)) || !(user & (BIT(1) | BIT(4))) || ((user >> 8) & 255) >= 128) return 0;
	if (phy[1] < 3 || (phy[8] & 31) > 1) return 0;
	if ((phy[8] & 31) == 1 && phy[1] < 5) return 0;
	sample->macid = (user >> 8) & 255;
	sample->beacon = !!(user & BIT(4));
	/* IE0 byte15[7:4], IE1 DWORD0[31:28] are rx_path_en. */
	sample->paths = (phy[8] & 31) ? phy[11] >> 4 : phy[23] >> 4;
	a = sample->paths & BIT(0) ? phy[4] : 0;
	b = sample->paths & BIT(1) ? phy[5] : 0;
	average = (sample->paths & 3) == 3 ? phy[3] : a > b ? a : b;
	sample->rssi[0] = phy[9];
	sample->rssi[1] = a ? phy[9] + a - average : 0;
	sample->rssi[2] = b ? phy[9] + b - average : 0;
	return 1;
}

/* Actual physts_parsing@2c55d0 IE lengths (.rodata+24a70) and IE24@2c4af0. */
static inline int rtl_ax_ppdu_aci(const u8 *data, unsigned int bytes, u8 *aci)
{
	static const u8 units[32] = {
		2,4,3,3,1,1,1,1,255,1,255,22,255,255,255,255,
		255,255,2,3,255,255,255,0,3,3,3,3,4,4,4,4,
	};
	u32 first, second, users, offset, seen = 0;
	const u8 *phy;
	unsigned int length, at, type;
	bool found = false;

	if (bytes < 8) return -EINVAL;
	first = get_unaligned_le32(data);
	second = get_unaligned_le32(data + 4);
	users = first & 15;
	if (users > 8) return -EINVAL;
	offset = 8 + ((users + 1) & ~1U) * 4 + (first & BIT(29) ? 96 : 0) +
		((second >> 16) & 255) * 8;
	if (offset > bytes || bytes - offset < 8) return -EINVAL;
	phy = data + offset;
	length = phy[1] * 8;
	if (!(phy[0] & BIT(7)) || length < 8 || length > bytes - offset) return -EINVAL;
	type = phy[0] & 31;
	if ((type >= 4 && type <= 7) || (type >= 9 && type <= 11)) return 0;
	for (at = 8; at < length;) {
		unsigned int id = phy[at] & 31, size = units[id] * 8;

		if (seen & BIT(id)) return -EINVAL;
		seen |= BIT(id);
		if (units[id] == 255) {
			if (length - at < 2) return -EINVAL;
			size = ((phy[at] >> 5) | (phy[at + 1] & 15) << 3) * 8;
		}
		if (!size || size > length - at) return -EINVAL;
		if (id == 24) {
			*aci = phy[at + 16] & 127;
			found = true;
		}
		at += size;
	}
	return found;
}

static inline void rtl_ax_ra_limit(u8 command[16], u8 bandwidth, u8 nss, bool update)
{
	u64 mask = get_unaligned_le64(command + 4);
	nss = nss < (command[3] & 7) + 1 ? nss : (command[3] & 7) + 1;
	if (nss == 1) mask &= BIT_ULL(63) | ((1ULL << 24) - 1);
	/* VHT MCS9 at20MHz is invalid for either of this board's two NSS. */
	if ((command[0] & 0x3f) == 0x14 && !bandwidth)
		mask &= ~(BIT_ULL(21) | BIT_ULL(33));
	command[0] = (command[0] & 0x3f) | bandwidth << 6;
	command[3] = (command[3] & ~7U) | (nss - 1);
	if (update) {
		/* Actual rtw_halbb_raupdate clears UpdateAll and sets BW/NSS/mask. */
		command[2] &= ~BIT(4);
		command[3] = (command[3] & ~BIT(7)) | BIT(6);
	}
	put_unaligned_le64(mask, command + 4);
}

/* Board halbb_ra_update_mask_watchdog: keep the capability mask as input,
 * apply RSSI hysteresis each watchdog, and publish UpdateMask (not UpdateAll). */
static inline u8 rtl_ax_ra_rssi_mask(u8 command[16], u8 rssi, u8 previous)
{
	static const u8 thresholds[] = {30, 44, 48, 52, 56, 60, 100};
	static const u32 remove[] = {0, 0xf, 0x101f, 0x303f, 0x707f, 0xf0ff, 0xf0ff};
	u8 level = 0;
	u64 mask = get_unaligned_le64(command + 4) & ~BIT_ULL(63), filtered;

	for (; level < 7; level++)
		if (rssi < thresholds[level] + (level >= previous ? 3 : 0)) break;
	if (level == 7) level = 0;
	filtered = mask & ~((u64)remove[level]);
	if (!(filtered >> 12)) filtered |= mask & ~0xfffULL;
	if (!filtered) filtered = mask & 0xfff;
	command[2] &= ~BIT(4);
	command[3] = (command[3] & ~BIT(6)) | BIT(7);
	put_unaligned_le64(filtered, command + 4);
	return level;
}

/* Actual register/update/watchdog commands use background NHM ratio1..254.
 * 0xff is the factory failed-sample value, not a noisy environment. */
static inline void rtl_ax_ra_environment(u8 command[16], u8 nhm_ratio)
{
	command[15] = (command[15] & ~BIT(0)) | (nhm_ratio && nhm_ratio != 255);
}

static inline void rtl_ax_ra_legacy(u8 command[16], u8 macid, u16 legacy, bool band5g)
{
	u8 mode = band5g || !(legacy & 15) ? 4 : (legacy & 0xff0) ? 6 : 2;
	u16 mask = legacy & (mode == 2 ? 15 : mode == 4 ? 0xff0 : 0xff5);

	memset(command, 0, 16);
	command[0] = mode;
	command[1] = macid;
	command[2] = 0x10;
	put_unaligned_le64(mask | BIT_ULL(63), command + 4);
	command[13] = 0x38;
	command[15] = band5g ? 0x20 : 0x80;
}

static inline void rtl_ax_ra_ht20(u8 command[16], u8 macid, u16 legacy, u16 ht, bool sgi,
	bool band5g)
{
	u64 mask = (legacy & (band5g ? 0x10 : 0x15)) | (u64)(ht & 255) << 12 |
		(u64)(ht >> 8) << 24 | BIT_ULL(63);

	memset(command, 0, 16);
	command[0] = band5g ? 0x0c : 0x0e; /* 5 GHz excludes CCK; BW updated by ra_limit. */
	command[1] = macid;
	command[2] = 0x10 | (sgi ? 0x20 : 0); /* Update all; RSSI unknown, init level0. */
	command[3] = !!(ht >> 8);
	put_unaligned_le64(mask, command + 4);
	command[13] = 0x38; /* Original RFE54 fixed CSI GI/LTF. */
	command[15] = band5g ? 0x20 : 0x80; /* Original C/XB report formats. */
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
