/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RATE_REPORT_H
#define RTL_AX_AP_RATE_REPORT_H

/* C2H BB/RA report: current firmware rate, not a per-packet TX result. */
static inline bool rtl_ax_rate_report(const u8 feedback[8], struct rate_info *rate)
{
	static const u8 bandwidths[] = { RATE_INFO_BW_20, RATE_INFO_BW_40, RATE_INFO_BW_80, RATE_INFO_BW_160 };
	if (feedback[4] & BIT(7)) return false;
	memset(rate, 0, sizeof(*rate));
	rate->bw = bandwidths[(feedback[5] >> 5) & 3];
	if ((feedback[5] & 3) == 3) {
		u8 giltf = (feedback[5] >> 2) & 7;

		rate->flags = RATE_INFO_FLAGS_HE_MCS;
		rate->mcs = feedback[4] & 15;
		rate->nss = ((feedback[4] >> 4) & 7) + 1;
		rate->he_gi = giltf == 1 || giltf == 3 || giltf == 5 ? NL80211_RATE_INFO_HE_GI_0_8 :
			giltf == 2 || giltf == 4 ? NL80211_RATE_INFO_HE_GI_1_6 : NL80211_RATE_INFO_HE_GI_3_2;
	} else if ((feedback[5] & 3) == 2) {
		rate->flags = RATE_INFO_FLAGS_VHT_MCS;
		rate->mcs = feedback[4] & 15;
		rate->nss = ((feedback[4] >> 4) & 7) + 1;
	} else if ((feedback[5] & 3) == 1) {
		rate->flags = RATE_INFO_FLAGS_MCS;
		rate->mcs = feedback[4] & 127;
	} else {
		static const u16 legacy[] = { 10, 20, 55, 110, 60, 90, 120, 180, 240, 360, 480, 540 };

		if (feedback[4] >= ARRAY_SIZE(legacy)) return false;
		rate->legacy = legacy[feedback[4]];
		rate->bw = RATE_INFO_BW_20;
		return true;
	}
	if ((feedback[5] & 3) != 3 && ((feedback[5] >> 2) & 7))
		rate->flags |= RATE_INFO_FLAGS_SHORT_GI;
	return true;
}

#endif
