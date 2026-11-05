/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_TRAFFIC_H
#define RTL_AX_TRAFFIC_H
#include "amsdu-queue.h"
/* Board core counters are sampled every two seconds, in binary kbit/s. */
struct rtl_ax_traffic_peer {
	u32 tx_seen, rx_seen, packets_seen;
	u32 tx_kbits, rx_kbits, smooth_tx, smooth_rx, packets;
	u16 rate, max_bytes, amsdu_bytes;
	u8 max_num, amsdu_num, vendor, bandwidth, nss, mode, smooth_mode;
	bool band5g, he, vht;
};

struct rtl_ax_traffic_wmm {
	u32 ac_seen[4], timeout;
	u8 streams;
	bool enabled;
};

static inline bool rtl_ax_traffic_wmm_update(struct rtl_ax_traffic_wmm *w, const u32 ac[4])
{
	u8 streams = 0;
	for (unsigned int i = 0; i < 4; i++) {
		streams += (u32)(ac[i] - w->ac_seen[i]) > 500;
		w->ac_seen[i] = ac[i];
	}
	if (streams > 1) {
		w->timeout = w->timeout >= 4 ? 33 : w->timeout + 1;
		if (streams > w->streams) w->streams = streams;
		w->enabled = true;
	} else {
		if (w->timeout) w->timeout--;
		if (w->timeout < 3) {
			w->timeout = 0; w->streams = 1; w->enabled = false;
		}
	}
	return w->enabled;
}

/* Actual WMM quota divides the radio's request pool by peak active ACs.
 * Disabled normal WMM leaves _txreq_lmt_check's limit gate closed. */
static inline u32 rtl_ax_traffic_wmm_quota(const struct rtl_ax_traffic_wmm *w, u32 requests)
{
	return w->enabled ? requests / w->streams : 0;
}

static inline u8 rtl_ax_traffic_mode(u32 tx, u32 rx)
{
	if (tx > 8192 && tx > (rx << 3)) return 1;
	if (rx > 8192 && rx > (tx << 3)) return 2;
	if (tx > 8192 && rx > 8192) return 3;
	return 0;
}

static inline void rtl_ax_traffic_sample(struct rtl_ax_traffic_peer *p, u32 tx, u32 rx, u32 packets)
{
	p->tx_kbits = (tx - p->tx_seen) >> 8;
	p->rx_kbits = (rx - p->rx_seen) >> 8;
	p->packets = packets - p->packets_seen;
	p->tx_seen = tx; p->rx_seen = rx; p->packets_seen = packets;
	p->smooth_tx = p->smooth_tx * 6 / 10 + p->tx_kbits * 4 / 10;
	p->smooth_rx = p->smooth_rx * 6 / 10 + p->rx_kbits * 4 / 10;
	p->mode = rtl_ax_traffic_mode(p->tx_kbits, p->rx_kbits);
	p->smooth_mode = rtl_ax_traffic_mode(p->smooth_tx, p->smooth_rx);
}

static inline bool rtl_ax_traffic_high_rate(const struct rtl_ax_traffic_peer *p)
{
	u16 rate = p->rate;
	return p->smooth_mode == 2 || rate == 0x8e || rate == 0x8f ||
		(rate >= 0x117 && rate <= 0x119) || (rate >= 0x197 && rate <= 0x19b) ||
		(p->nss == 1 && (rate == 0x87 || (rate >= 0x107 && rate <= 0x109) ||
		(rate >= 0x187 && rate <= 0x18b)));
}

static inline u16 rtl_ax_traffic_amsdu_size(u8 num)
{
	return num < 2 ? 0 : num <= 2 ? 3793 : num <= 4 ? 7433 : 10896;
}

/* sta_dynamic_control@cf800: normal AP branch, no forced/test A-MSDU. */
static inline void rtl_ax_traffic_amsdu(struct rtl_ax_traffic_peer *p, u32 pps)
{
	u32 tp = p->tx_kbits >> 10;
	u8 num, old = p->amsdu_num;
	if (p->bandwidth == 3 && tp > 1300) num = p->max_num <= 4 ? p->max_num : 7;
	else if (!p->mode && !p->vendor) num = 0;
	else if (tp > (p->band5g ? 600 : 300) || rtl_ax_traffic_high_rate(p)) num = p->max_num;
	else if (tp > (p->band5g ? 300 : 100)) num = p->max_num / 2;
	else num = 0;
	if (num == 1) num = 0;
	if (p->mode == 1 && p->packets < (old ? pps / 2 : pps)) num = 0;
	p->amsdu_num = num;
	p->amsdu_bytes = rtl_ax_traffic_amsdu_size(num);
}

/* throughput_dynamic_control@cfc4c: actual AP normal branches. Test-mode
 * overrides and TXSC's three independent cached LAN flows are explicit inputs. */
static inline u32 rtl_ax_traffic_edca(const struct rtl_ax_traffic_peer *p,
	bool rx_ba, u16 rx_window, u8 flows, u8 channel_busy, bool *busy)
{
	if (p->tx_kbits <= p->rx_kbits * 4)
		return p->vendor == 18 && p->vht && !p->he ? 0x321f : 0x642b;
	if (!p->band5g || (p->vht && !p->he)) return 0x0138641f;
	if (p->he && ((p->bandwidth == 3 &&
	    (p->vendor == 3 || p->vendor == 20 || p->vendor == 23)) ||
	    (p->bandwidth <= 2 && p->vendor == 22))) return 0x0138642b;
	if (p->bandwidth == 3 && (p->tx_kbits >> 10) > 1300) {
		if (rx_ba && rx_window <= 64) return 0x01786615;
		return flows == 3 ? 0x00bc643f : 0x00bc863f;
	}
	*busy = channel_busy >= 10 || (*busy && channel_busy >= 8);
	if (*busy) return 0x0138642b;
	return p->bandwidth == 2 && flows == 1 ? 0x005e862b : 0x005e642b;
}
#endif
