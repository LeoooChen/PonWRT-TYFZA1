/* SPDX-License-Identifier: GPL-2.0-only */
/* Linux receive integration for the native C 5GHz and XB2.4GHz paths.
 * Hardware channel and filters reuse the original-backed native controls. */
#ifndef RTL_AX_AP_MONITOR_H
#define RTL_AX_AP_MONITOR_H
#include <linux/rtl9617c-wifi.h>
#include "tx-format.h"
#include "rate.h"

static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw)
{
	return *(struct rtl_ax_ap **)hw->priv;
}

static int rtl_ax_monitor_start(struct ieee80211_hw *hw)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret;

	mutex_lock(&radio->command_mutex);
	ret = rtl_ax_receive_configure(radio, hw->conf.chandef.chan->hw_value,
		ieee80211_frequency_to_channel(hw->conf.chandef.center_freq1),
		hw->conf.chandef.width == NL80211_CHAN_WIDTH_80 ? 2 :
		hw->conf.chandef.width == NL80211_CHAN_WIDTH_40);
	if (ret) {
		mutex_unlock(&radio->command_mutex);
		return ret;
	}
	rtl_ax_receive_mode(&radio->io, radio->ap_started ? 4 : 0);
	WRITE_ONCE(radio->monitor_started, true);
	rtl_ax_update32(&radio->io, 0x9c18, 3, 1);
	radio->io.write32(radio, 0xce40, 0x12);
	radio->ra_sampling = false;
	rtl_ax_rx_open(radio->rx);
	rtl_ax_tx_open(radio->tx);
	mutex_unlock(&radio->command_mutex);
	wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, 0);
	wiphy_delayed_work_queue(hw->wiphy, &radio->ra_work, HZ);
	return 0;
}

static void rtl_ax_monitor_stop(struct ieee80211_hw *hw, bool suspend)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	wiphy_delayed_work_cancel(hw->wiphy, &radio->cleanup_work);
	wiphy_delayed_work_cancel(hw->wiphy, &radio->ra_work);
	mutex_lock(&radio->command_mutex);
	WRITE_ONCE(radio->monitor_started, false);
	rtl_ax_update32(&radio->io, 0xce40, BIT(0), 0);
	tasklet_kill(&radio->tx_tasklet);
	rtl_ax_update32(&radio->io, 0xc400, BIT(12), 0);
	radio->beacon_enabled = false;
	rtl_ax_tx_pause(radio->tx);
	rtl_ax_rx_close(radio->rx);
	mutex_unlock(&radio->command_mutex);
}

static int rtl_ax_monitor_config(struct ieee80211_hw *hw, int radio_idx, u32 changed)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret;

	if (changed & IEEE80211_CONF_CHANGE_POWER)
		radio->requested_tx_dbm = hw->conf.power_level;
	if (!(changed & IEEE80211_CONF_CHANGE_CHANNEL)) {
		if ((changed & IEEE80211_CONF_CHANGE_POWER) && radio->channel.configured) {
			mutex_lock(&radio->command_mutex);
			ret = rtl_ax_power_apply(&radio->io, radio->chip, &radio->power_inputs,
				radio->channel.band, radio->channel.primary, radio->channel.center,
				radio->channel.bandwidth, radio->power_country,
				radio->requested_tx_dbm, &radio->power_hardware);
			mutex_unlock(&radio->command_mutex);
			return ret;
		}
		return 0;
	}
	if (hw->conf.chandef.width != NL80211_CHAN_WIDTH_20 &&
	    !(radio->chip == RTL_AX_8852C &&
	      (hw->conf.chandef.width == NL80211_CHAN_WIDTH_40 || hw->conf.chandef.width == NL80211_CHAN_WIDTH_80)) &&
	    hw->conf.chandef.width != NL80211_CHAN_WIDTH_20_NOHT)
		return -EOPNOTSUPP;
	mutex_lock(&radio->command_mutex);
	ret = rtl_ax_receive_configure(radio, hw->conf.chandef.chan->hw_value,
		ieee80211_frequency_to_channel(hw->conf.chandef.center_freq1),
		hw->conf.chandef.width == NL80211_CHAN_WIDTH_80 ? 2 :
		hw->conf.chandef.width == NL80211_CHAN_WIDTH_40);
	mutex_unlock(&radio->command_mutex);
	return ret;
}

static int rtl_ax_monitor_add_interface(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	if (vif->type == NL80211_IFTYPE_MONITOR)
		return 0;
	if (vif->type != NL80211_IFTYPE_AP)
		return -EOPNOTSUPP;
	if (radio->ap_vif)
		return -EBUSY;
	radio->ap_vif = vif;
	rtl_ax_tx_ap_address(radio->tx, vif->addr);
	return 0;
}

static void rtl_ax_monitor_remove_interface(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	if (radio->ap_vif == vif)
		radio->ap_vif = NULL;
}

static int rtl_ax_ap_template(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_bss_conf *conf)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct ieee80211_mutable_offsets offsets = {};
	struct sk_buff *skb;
	unsigned int rate = radio->chip == RTL_AX_8852C ? 4 : 0;
	int ret;

	skb = ieee80211_beacon_get_template(hw, vif, &offsets, conf->link_id);
	if (!skb)
		return -ENOMEM;
	if (conf->basic_rates)
		rate = radio->rates[__ffs(conf->basic_rates)].hw_value;
	ret = rtl_ax_beacon_update(radio, skb->data, skb->len, offsets.tim_offset, rate);
	dev_kfree_skb(skb);
	return ret;
}

static int rtl_ax_ap_start(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_bss_conf *conf)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret;

	ret = rtl_ax_self_role_prepare(radio, vif->addr, conf->beacon_int, conf->dtim_period);
	if (!ret && radio->security_roles[0].valid) {
		u8 dctl[68];

		rtl_ax_security_dctl(dctl, 0, &radio->security_roles[0]);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 9, dctl, sizeof(dctl), true);
	}
	if (!ret)
		ret = rtl_ax_ap_template(hw, vif, conf);
	radio->ap_result = ret;
	if (ret) {
		rtl_ax_update32(&radio->io, 0xc400, BIT(12), 0);
		return ret;
	}
	rtl_ax_port0_color(&radio->io, conf->he_bss_color.enabled ? conf->he_bss_color.color : 0);
	rtl_ax_receive_mode(&radio->io, 4);
	rtl_ax_update32(&radio->io, 0xc400, BIT(12), BIT(12));
	WRITE_ONCE(radio->ap_started, true);
	radio->beacon_enabled = true;
	return 0;
}

static void rtl_ax_offload_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, offload_work);
	unsigned int macid;
	if (!radio->ap_started) return;
	for (macid = 1; macid < 128; macid++) {
		int ret;
		if (!radio->peer_authorized[macid] || !radio->tx_ba[macid].aggregation[0] ||
		    !(radio->security_roles[macid].valid & 3)) continue;
		ret = rtl_ax_tx_ni_peer(radio->tx, ieee80211_vif_to_wdev(radio->ap_vif)->netdev, macid);
		radio->offload_result = min(ret, 0);
		if (ret > 0) radio->offload_started++;
	}
}

static void rtl_ax_offload_ready(void *context)
{
	struct rtl_ax_ap *radio = context;
	wiphy_work_queue(radio->hw->wiphy, &radio->offload_work);
}

static void rtl_ax_tim_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, beacon_work);

	if (!radio->ap_started)
		return;
	radio->ap_result = rtl_ax_ap_template(radio->hw, radio->ap_vif, &radio->ap_vif->bss_conf);
	if (!radio->ap_result)
		radio->tim_updates++;
}

static int rtl_ax_set_tim(struct ieee80211_hw *hw, struct ieee80211_sta *sta, bool set)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	radio->tim_requests++;
	wiphy_work_queue(hw->wiphy, &radio->beacon_work);
	return 0;
}

static void rtl_ax_ap_stop(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_bss_conf *conf)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	rtl_ax_update32(&radio->io, 0xc400, BIT(12), 0);
	radio->beacon_enabled = false;
	WRITE_ONCE(radio->ap_started, false);
	wiphy_work_cancel(hw->wiphy, &radio->offload_work);
	wiphy_work_cancel(hw->wiphy, &radio->beacon_work);
	rtl_ax_receive_mode(&radio->io, 0);
}

static void rtl_ax_edca_apply(struct rtl_ax_ap *radio, struct ieee80211_vif *vif, unsigned int ac)
{
	static const u16 addresses[] = { 0xc30c, 0xc308, 0xc300, 0xc304 };
	const struct ieee80211_tx_queue_params *params = &radio->edca[ac];
	unsigned int sifs = radio->chip == RTL_AX_8852C ? 16 : 10;
	unsigned int slot = vif->bss_conf.use_short_slot || radio->chip == RTL_AX_8852C ? 9 : 20;

	/* Actual rtw_hal_mac_set_edca@1b1e04 -> set_hw_edca_param@1ea960:
	 * SCC MAC band0/WMM0, TXOP[26:16], ECWmax[15:12], ECWmin[11:8], AIFS us[7:0]. */
	radio->edca_words[ac] = (params->txop & 0x7ff) << 16 |
		ilog2(params->cw_max + 1) << 12 | ilog2(params->cw_min + 1) << 8 |
		(sifs + params->aifs * slot);
	rtl_ax_write32(radio, addresses[ac], radio->edca_words[ac]);
}

static int rtl_ax_ap_conf_tx(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	unsigned int link_id, u16 ac, const struct ieee80211_tx_queue_params *params)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	mutex_lock(&radio->command_mutex);
	radio->edca[ac] = *params;
	radio->edca_valid |= BIT(ac);
	rtl_ax_edca_apply(radio, vif, ac);
	mutex_unlock(&radio->command_mutex);
	return 0;
}

static void rtl_ax_ap_changed(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_bss_conf *conf, u64 changed)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned int ac;

	if (vif->type != NL80211_IFTYPE_AP)
		return;
	if (changed & BSS_CHANGED_ERP_SLOT) {
		mutex_lock(&radio->command_mutex);
		for (ac = 0; ac < IEEE80211_NUM_ACS; ac++)
			if (radio->edca_valid & BIT(ac))
				rtl_ax_edca_apply(radio, vif, ac);
		mutex_unlock(&radio->command_mutex);
	}
	if (!radio->ap_started)
		return;
	if (changed & BSS_CHANGED_HE_BSS_COLOR)
		rtl_ax_port0_color(&radio->io, conf->he_bss_color.enabled ? conf->he_bss_color.color : 0);
	if (changed & BSS_CHANGED_BEACON) {
		radio->ap_result = rtl_ax_ap_template(hw, vif, conf);
		if (radio->ap_result)
			dev_err(&radio->pdev->dev, "beacon template update failed: %d\n", radio->ap_result);
	}
	if (changed & BSS_CHANGED_BEACON_ENABLED) {
		rtl_ax_update32(&radio->io, 0xc400, BIT(12), conf->enable_beacon ? BIT(12) : 0);
		radio->beacon_enabled = conf->enable_beacon;
	}
}

static int rtl_ax_drop_station(struct rtl_ax_ap *radio, unsigned int macid, unsigned int tid)
{
	u8 drop[24];
	unsigned int ac;
	int ret;

	if (rtl_ax_tx_station_pending(radio->tx, macid, tid))
		for (ac = 0; ac < 4; ac++) {
			if (tid != IEEE80211_NUM_TIDS && ac != rtl_ax_tx_dma_channel(tid & 7, 0))
				continue;
			rtl_ax_station_drop(drop, macid, ac);
			ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 9, 27, drop, sizeof(drop), false);
			if (ret)
				return ret;
		}
	/* PKT_DROP has no ACK. RPQ release, not H2C submission, ends DMA ownership. */
	return rtl_ax_tx_wait_station(radio->tx, macid, tid);
}

static int rtl_ax_rx_ba_remove(struct rtl_ax_ap *radio, unsigned int index)
{
	u8 ba[8];
	int ret;

	radio->rx_ba[index].deleting = true;
	rtl_ax_store32(ba, 2 | radio->rx_ba[index].macid << 8);
	rtl_ax_store32(ba + 4, (index + 8) << 28);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 12, 0, ba, sizeof(ba), true);
	radio->rx_ba_result = ret;
	if (!ret) {
		radio->rx_ba[index].valid = false;
		radio->rx_ba[index].deleting = false;
		radio->rx_ba_stopped++;
	}
	return ret;
}

static int rtl_ax_station_release(struct rtl_ax_ap *radio, unsigned int macid)
{
	u8 command[4], cam[60];
	u8 security[24], dctl[68];
	struct rtl_ax_security_role empty = {};
	struct rtl_ax_security_role *keys = &radio->security_roles[macid];
	unsigned int i;
	int ret;

	ret = rtl_ax_tx_ni_retire_peer(radio->tx, macid);
	if (ret) return ret;
	ret = rtl_ax_drop_station(radio, macid, IEEE80211_NUM_TIDS);
	if (ret)
		return ret;
	if (keys->valid) {
		rtl_ax_security_dctl(dctl, macid, &empty);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 9, dctl, sizeof(dctl), true);
		if (ret)
			return ret;
		for (i = 0; i < ARRAY_SIZE(keys->cam); i++)
			if (keys->valid & BIT(i)) {
				rtl_ax_security_cam(security, keys->cam[i], NULL);
				ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 10, 1, security, sizeof(security), true);
				if (ret)
					return ret;
				__clear_bit(keys->cam[i], radio->security_ids);
				keys->valid &= ~BIT(i);
				radio->keys_removed++;
			}
		memset(keys, 0, sizeof(*keys));
	}
	for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++)
		if (radio->rx_ba[i].valid && radio->rx_ba[i].macid == macid) {
			ret = rtl_ax_rx_ba_remove(radio, i);
			if (ret)
				return ret;
		}
	/* Actual _remove_role: FW role REMOVE followed by address CAM valid0;
	 * BSSID CAM remains valid while the AP's self-role uses it. */
	rtl_ax_store32(command, macid | 0x4600);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 4, command, sizeof(command), true);
	if (ret)
		return ret;
	rtl_ax_peer_cam(cam, macid, radio->retired_stations[macid].self,
		radio->retired_stations[macid].peer, radio->retired_stations[macid].aid, false);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (!ret) {
		radio->ra_registered[macid] = false;
		rtl_ax_tx_peer_qos(radio->tx, macid, false);
		__clear_bit(macid, radio->station_ids);
		radio->retired_stations[macid].pending = false;
		radio->station_removed++;
	}
	return ret;
}

static int rtl_ax_tx_ba_finish(struct rtl_ax_ap *radio, unsigned int macid, unsigned int tid)
{
	u8 cmac[68];
	unsigned int i, aggregate = 0;
	bool flush = radio->tx_ba[macid].flushing & BIT(tid);
	int ret;

	ret = flush ? rtl_ax_drop_station(radio, macid, tid) :
		rtl_ax_tx_wait_station(radio->tx, macid, tid);
	if (ret)
		return ret;
	for (i = 0; i < IEEE80211_NUM_TIDS; i++)
		aggregate = max_t(unsigned int, aggregate, radio->tx_ba[macid].aggregation[i]);
	rtl_ax_station_cmac_connected(cmac, macid, radio->chip == RTL_AX_8852C, aggregate);
	/* BA changes retain the peer's negotiated HE packet-extension padding. */
	rtl_ax_store32(cmac + 60, rtl_ax_ap_read32(cmac + 60) & ~0xffU);
	if (radio->ra_registered[macid])
		rtl_ax_store32(cmac + 36, rtl_ax_ap_read32(cmac + 36) & ~0x1ffU);
	if (!aggregate)
		rtl_ax_store32(cmac + 48, 0x3ff);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 2, cmac, sizeof(cmac), true);
	if (ret)
		return ret;
	radio->tx_ba[macid].stopping &= ~BIT(tid);
	radio->tx_ba[macid].flushing &= ~BIT(tid);
	if (!flush)
		ieee80211_stop_tx_ba_cb_irqsafe(radio->ap_vif, radio->tx_ba[macid].address, tid);
	return 0;
}

static bool rtl_ax_security_cleanup(struct rtl_ax_ap *radio)
{
	unsigned int index;
	bool retry = false;
	u8 cam[24];

	for_each_set_bit(index, radio->security_deleting, RTL_AX_SECURITY_ENTRIES) {
		rtl_ax_security_cam(cam, index, NULL);
		if (rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 10, 1, cam, sizeof(cam), true)) {
			retry = true;
			continue;
		}
		__clear_bit(index, radio->security_deleting);
		__clear_bit(index, radio->security_ids);
		radio->keys_removed++;
	}
	return retry;
}

static void rtl_ax_cleanup_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, cleanup_work.work);
	unsigned int i, tid;
	bool retry = rtl_ax_security_cleanup(radio);

	for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++)
		if (radio->rx_ba[i].deleting && rtl_ax_rx_ba_remove(radio, i))
			retry = true;
	for (i = 1; i < ARRAY_SIZE(radio->retired_stations); i++)
		if (radio->retired_stations[i].pending && rtl_ax_station_release(radio, i))
			retry = true;
	for (i = 1; i < ARRAY_SIZE(radio->tx_ba); i++)
		for (tid = 0; tid < IEEE80211_NUM_TIDS; tid++)
			if ((radio->tx_ba[i].stopping & BIT(tid)) && rtl_ax_tx_ba_finish(radio, i, tid))
				retry = true;
	if (retry && radio->monitor_started)
		wiphy_delayed_work_queue(wiphy, &radio->cleanup_work, HZ);
}

static int rtl_ax_station_remove(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct rtl_ax_station *station = (void *)sta->drv_priv;
	unsigned int macid = station->macid;
	int ret;

	if (!macid)
		return 0;
	ether_addr_copy(radio->retired_stations[macid].self, vif->addr);
	ether_addr_copy(radio->retired_stations[macid].peer, sta->addr);
	radio->retired_stations[macid].aid = sta->aid;
	radio->retired_stations[macid].pending = true;
	radio->tx_ba[macid].stopping = radio->tx_ba[macid].flushing = 0;
	station->macid = 0;
	ret = rtl_ax_station_release(radio, macid);
	if (ret) {
		dev_err(&radio->pdev->dev, "MACID%u release pending: %d\n", macid, ret);
		wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
	}
	return 0;
}

static int rtl_ax_station_add(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct rtl_ax_station *station = (void *)sta->drv_priv;
	u8 unpause[32], command[4], cam[60];
	unsigned int id = find_next_zero_bit(radio->station_ids, 128, 1);
	int ret;

	if (id == 128)
		return -ENOSPC;
	rtl_ax_role_unpause(unpause, id);
	ret = rtl_ax_command_flags(radio, RTL_AX_H2C_CMD, 1, 9, 8, unpause, sizeof(unpause), true, false);
	if (ret)
		return ret;
	rtl_ax_store32(command, id | 0x4200);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 4, command, sizeof(command), true);
	if (ret)
		return ret;
	station->macid = id;
	__set_bit(id, radio->station_ids);
	memset(&radio->tx_ba[id], 0, sizeof(radio->tx_ba[id]));
	ether_addr_copy(radio->tx_ba[id].address, sta->addr);
	rtl_ax_peer_cam(cam, id, vif->addr, sta->addr, 0, true);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (ret)
		goto remove;
	ret = rtl_ax_station_tables(radio, id);
	if (ret)
		goto remove;
	ret = rtl_ax_station_connected_table(radio, id, sta);
	if (ret)
		goto remove;
	/* Original connect transition: self AP_CLIENT2, AP3, legacy TF padding1. */
	rtl_ax_store32(command, id | 0x8b040000);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 0, command, sizeof(command), true);
	if (ret)
		goto remove;
	rtl_ax_peer_cam(cam, id, vif->addr, sta->addr, sta->aid, true);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (ret)
		goto remove;
	rtl_ax_tx_peer_qos(radio->tx, id, sta->wme);
	rtl_ax_rx_uapsd(radio->rx, id, sta->uapsd_queues);
	if (sta->deflink.he_cap.has_he) {
		const struct ieee80211_sta_he_cap *he = &sta->deflink.he_cap;
		bool band5g = radio->chip == RTL_AX_8852C;
		u8 bandwidth = min_t(unsigned int, radio->channel.bandwidth, sta->deflink.bandwidth);
		u32 caps = BIT(17) | min_t(unsigned int, sta->cur->max_amsdu_len, 7935) << 18;
		bool ldpc = he->he_cap_elem.phy_cap_info[1] & IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD;

		rtl_ax_ra_he(radio->ra_config[id], id, sta->deflink.supp_rates[radio->band.band] << (band5g ? 4 : 0),
			le16_to_cpu(he->he_mcs_nss_supp.rx_mcs_80), he->he_cap_elem.phy_cap_info[1],
			he->he_cap_elem.phy_cap_info[7], band5g, bandwidth);
		ret = rtl_ax_command_flags(radio, RTL_AX_H2C_LONG_DATA, 2, 1, 0,
			radio->ra_config[id], 16, false, false);
		if (ret) goto remove;
		radio->ra_registered[id] = true;
		radio->ra_rssi_samples[id] = 0;
		radio->ra_reports[id] = 0;
		memset(radio->ra_feedback[id], 0, 8);
		rtl_ax_tx_peer_rate(radio->tx, id, 0x180 | bandwidth << 16 | (ldpc ? BIT(21) : 0), caps);
	} else if (radio->chip == RTL_AX_8852C && sta->deflink.vht_cap.vht_supported) {
		u16 ht = sta->deflink.ht_cap.mcs.rx_mask[0] | sta->deflink.ht_cap.mcs.rx_mask[1] << 8;
		bool sgi = sta->deflink.vht_cap.cap & IEEE80211_VHT_CAP_SHORT_GI_80;
		u32 caps = ht | (sgi ? BIT(16) : 0) | BIT(17) |
			min_t(unsigned int, sta->cur->max_amsdu_len, 7935) << 18;

		rtl_ax_ra_vht80(radio->ra_config[id], id, sta->deflink.supp_rates[NL80211_BAND_5GHZ] << 4,
			le16_to_cpu(sta->deflink.vht_cap.vht_mcs.rx_mcs_map), sgi);
		ret = rtl_ax_command_flags(radio, RTL_AX_H2C_LONG_DATA, 2, 1, 0,
			radio->ra_config[id], 16, false, false);
		if (ret) goto remove;
		radio->ra_registered[id] = true;
		radio->ra_rssi_samples[id] = 0;
		radio->ra_reports[id] = 0;
		memset(radio->ra_feedback[id], 0, 8);
		rtl_ax_tx_peer_rate(radio->tx, id, 0x100 | 2 << 16, caps);
	} else if (sta->deflink.ht_cap.ht_supported) {
		u16 ht = sta->deflink.ht_cap.mcs.rx_mask[0] | sta->deflink.ht_cap.mcs.rx_mask[1] << 8;
		bool sgi = sta->deflink.ht_cap.cap & IEEE80211_HT_CAP_SGI_20;
		u32 caps = ht | (sgi ? BIT(16) : 0) | BIT(17) |
			min_t(unsigned int, sta->cur->max_amsdu_len, 7935) << 18;
		rtl_ax_ra_ht20(radio->ra_config[id], id,
			sta->deflink.supp_rates[radio->band.band], ht, sgi);
		ret = rtl_ax_command_flags(radio, RTL_AX_H2C_LONG_DATA, 2, 1, 0,
			radio->ra_config[id], 16, false, false);
		if (ret) goto remove;
		radio->ra_registered[id] = true;
		radio->ra_rssi_samples[id] = 0;
		radio->ra_reports[id] = 0;
		memset(radio->ra_feedback[id], 0, 8);
		rtl_ax_tx_peer_rate(radio->tx, id, 0x80, caps);
	}
	radio->station_added++;
	dev_info(&radio->pdev->dev, "native AP station MACID%u AID%u connected\n", id, sta->aid);
	return 0;
remove:
	rtl_ax_station_remove(hw, vif, sta);
	return ret;
}

static int rtl_ax_sta_state(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta, enum ieee80211_sta_state old, enum ieee80211_sta_state new)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned int macid = ((struct rtl_ax_station *)sta->drv_priv)->macid;
	if (old == IEEE80211_STA_AUTH && new == IEEE80211_STA_ASSOC)
		return rtl_ax_station_add(hw, vif, sta);
	if (old == IEEE80211_STA_ASSOC && new == IEEE80211_STA_AUTH)
		return rtl_ax_station_remove(hw, vif, sta);
	if (new == IEEE80211_STA_AUTHORIZED) {
		radio->peer_authorized[macid] = true;
		rtl_ax_tx_peer_authorized(radio->tx, macid, true);
		rtl_ax_offload_ready(radio);
	} else if (old == IEEE80211_STA_AUTHORIZED) {
		radio->peer_authorized[macid] = false;
		rtl_ax_tx_peer_authorized(radio->tx, macid, false);
		radio->offload_result = rtl_ax_tx_ni_retire_peer(radio->tx, macid);
	}
	return 0;
}

static int rtl_ax_ampdu_action(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_ampdu_params *params)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct rtl_ax_station *station = (void *)params->sta->drv_priv;
	u8 cmac[68], ba[8];
	unsigned int i, aggregate = 0;
	int ret;

	switch (params->action) {
	case IEEE80211_AMPDU_TX_START:
		{
			ret = rtl_ax_tx_peer_ssn(radio->tx, station->macid, params->tid);
			if (ret < 0) return ret;
			params->ssn = ret;
		}
		return IEEE80211_AMPDU_TX_START_IMMEDIATE;
	case IEEE80211_AMPDU_TX_OPERATIONAL:
		aggregate = min_t(unsigned int, params->buf_size, 64);
		/* rtw_hal_cfg_tx_ampdu(count64) -> set_hw_ampdu_cfg, band0,
		 * normal/non-single mode, maximum time165 and number64. */
		rtl_ax_update8(&radio->io, 0xc604, 7, 0);
		rtl_ax_update32(&radio->io, 0xc610, 0xff0000ff, 0xa500003f);
		rtl_ax_station_cmac_connected(cmac, station->macid, radio->chip == RTL_AX_8852C, aggregate);
		rtl_ax_store32(cmac + 60, rtl_ax_ap_read32(cmac + 60) & ~0xffU);
		/* The stock VW_NO_WDINFO default must not overwrite firmware RA. */
		if (radio->ra_registered[station->macid])
			rtl_ax_store32(cmac + 36, rtl_ax_ap_read32(cmac + 36) & ~0x1ffU);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 2, cmac, sizeof(cmac), true);
		if (ret)
			return ret;
		WRITE_ONCE(radio->tx_ba[station->macid].aggregation[params->tid], aggregate);
		rtl_ax_tx_peer_ba(radio->tx, station->macid, params->tid, aggregate);
		rtl_ax_offload_ready(radio);
		dev_info(&radio->pdev->dev, "native TX BA MACID%u TID%u size%u operational\n",
			station->macid, params->tid, aggregate);
		return 0;
	case IEEE80211_AMPDU_TX_STOP_CONT:
	case IEEE80211_AMPDU_TX_STOP_FLUSH:
	case IEEE80211_AMPDU_TX_STOP_FLUSH_CONT:
		{
			ret = rtl_ax_tx_ni_retire_peer(radio->tx, station->macid);
			if (ret) return ret;
		}
		WRITE_ONCE(radio->tx_ba[station->macid].aggregation[params->tid], 0);
		rtl_ax_tx_peer_ba(radio->tx, station->macid, params->tid, 0);
		radio->tx_ba[station->macid].stopping |= BIT(params->tid);
		if (params->action != IEEE80211_AMPDU_TX_STOP_CONT)
			radio->tx_ba[station->macid].flushing |= BIT(params->tid);
		ret = rtl_ax_tx_ba_finish(radio, station->macid, params->tid);
		if (ret)
			wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
		return 0;
	case IEEE80211_AMPDU_RX_START:
		for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++)
			if (radio->rx_ba[i].valid && radio->rx_ba[i].macid == station->macid &&
			    radio->rx_ba[i].tid == params->tid)
				break;
		if (i == ARRAY_SIZE(radio->rx_ba)) {
			for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++)
				if (!radio->rx_ba[i].valid)
					break;
		}
		if (i == ARRAY_SIZE(radio->rx_ba))
			return -ENOSPC;
		/* Actual C/XB MAC ops+496 reports extended standard entries8..15.
		 * ops+504 selects mac_bacam_info@23ca2c: index belongs to word1,
		 * standard-enable bit8, band0; own Linux allocation retains that ABI. */
		rtl_ax_store32(ba, 3 | (params->tid & 15) << 4 | station->macid << 8 |
			(params->ssn & 0xfff) << 20);
		rtl_ax_store32(ba + 4, (i + 8) << 28 | BIT(8));
		radio->rx_ba[i].macid = station->macid;
		radio->rx_ba[i].tid = params->tid;
		radio->rx_ba[i].valid = true;
		radio->rx_ba[i].deleting = true;
		ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 12, 0, ba, sizeof(ba), true);
		radio->rx_ba_result = ret;
		if (ret) {
			wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
			return ret;
		}
		radio->rx_ba[i].deleting = false;
		radio->rx_ba_started++;
		dev_info(&radio->pdev->dev, "native RX BA MACID%u TID%u entry%u SSN%u FW completed\n",
			station->macid, params->tid, i + 8, params->ssn);
		return 0;
	case IEEE80211_AMPDU_RX_STOP:
		ret = rtl_ax_tx_ni_retire_peer(radio->tx, station->macid);
		if (ret) return ret;
		for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++)
			if (radio->rx_ba[i].valid && radio->rx_ba[i].macid == station->macid &&
			    radio->rx_ba[i].tid == params->tid)
				break;
		if (i == ARRAY_SIZE(radio->rx_ba))
			return 0;
		ret = rtl_ax_rx_ba_remove(radio, i);
		if (ret) {
			wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
			return ret;
		}
		dev_info(&radio->pdev->dev, "native RX BA MACID%u TID%u entry%u removed\n",
			station->macid, params->tid, i + 8);
		return 0;
	}
	return -EOPNOTSUPP;
}

static void rtl_ax_monitor_filter(struct ieee80211_hw *hw,
	unsigned int changed, unsigned int *flags, u64 multicast)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	*flags &= FIF_ALLMULTI | FIF_BCN_PRBRESP_PROMISC | FIF_CONTROL |
		FIF_OTHER_BSS | FIF_PSPOLL | FIF_PROBE_REQ | FIF_MCAST_ACTION;
	mutex_lock(&radio->command_mutex);
	rtl_ax_receive_mode(&radio->io, radio->ap_started ? 4 : 0);
	mutex_unlock(&radio->command_mutex);
}

static void rtl_ax_monitor_tx(struct ieee80211_hw *hw,
	struct ieee80211_tx_control *control, struct sk_buff *skb)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct ieee80211_hdr *header = (void *)skb->data;
	const struct ieee80211_tx_rate *selected = &IEEE80211_SKB_CB(skb)->control.rates[0];
	unsigned int rate = radio->chip == RTL_AX_8852C ? 4 : 0;
	unsigned int gi = 0, bandwidth = 0;
	unsigned int macid = control->sta ? ((struct rtl_ax_station *)control->sta->drv_priv)->macid : 0;
	bool encap = IEEE80211_SKB_CB(skb)->flags & IEEE80211_TX_CTL_HW_80211_ENCAP;
	unsigned int aggregate = 0, density = 0;

	if (skb->len >= (encap ? ETH_HLEN : 24) && skb->len <= 0x3fff &&
	    (encap || ieee80211_is_mgmt(header->frame_control) || ieee80211_is_data(header->frame_control))) {
		/* Original RTW_DATA_RATE_MCS0=0x80, GI0=long/GI1=short.
		 * mac80211's MCS index must not index the legacy bitrate array. */
		if (selected->flags & IEEE80211_TX_RC_VHT_MCS) {
			rate = 0x100 + (ieee80211_rate_get_vht_nss(selected) - 1) * 16 +
				ieee80211_rate_get_vht_mcs(selected);
			gi = !!(selected->flags & IEEE80211_TX_RC_SHORT_GI);
			bandwidth = selected->flags & IEEE80211_TX_RC_80_MHZ_WIDTH ? 2 :
				!!(selected->flags & IEEE80211_TX_RC_40_MHZ_WIDTH);
		} else if (selected->flags & IEEE80211_TX_RC_MCS) {
			rate = 0x80 + selected->idx;
			gi = !!(selected->flags & IEEE80211_TX_RC_SHORT_GI);
			bandwidth = !!(selected->flags & IEEE80211_TX_RC_40_MHZ_WIDTH);
		} else if (selected->idx >= 0 && selected->idx < radio->band.n_bitrates)
			rate = radio->band.bitrates[selected->idx].hw_value;
		if (control->sta && (IEEE80211_SKB_CB(skb)->flags & IEEE80211_TX_CTL_AMPDU)) {
			unsigned int tid = encap ? skb->priority & 7 : *ieee80211_get_qos_ctl(header) & IEEE80211_QOS_CTL_TID_MASK;
			aggregate = READ_ONCE(radio->tx_ba[macid].aggregation[tid]);
			density = control->sta->deflink.ht_cap.ampdu_density;
		}
		if (!rtl_ax_tx_submit(radio->tx, hw, skb, rate, gi, bandwidth, macid, aggregate, density))
			return;
	} else
		radio->tx_unimplemented++;
	ieee80211_free_txskb(hw, skb);
}

static void rtl_ax_tx_queues(struct tasklet_struct *tasklet)
{
	struct rtl_ax_ap *radio = from_tasklet(radio, tasklet, tx_tasklet);
	struct ieee80211_hw *hw = radio->hw;
	struct ieee80211_txq *queue;
	unsigned int ac;

	if (!READ_ONCE(radio->monitor_started))
		return;
	rcu_read_lock();
	for (ac = 0; ac < hw->queues; ac++) {
		ieee80211_txq_schedule_start(hw, ac);
		while ((queue = ieee80211_next_txq(hw, ac))) {
			struct ieee80211_tx_control control = { .sta = queue->sta };
			struct sk_buff *skb;

			while (!ieee80211_queue_stopped(hw, ac) &&
			       (skb = ieee80211_tx_dequeue(hw, queue)))
				rtl_ax_monitor_tx(hw, &control, skb);
			ieee80211_return_txq(hw, queue, false);
			if (ieee80211_queue_stopped(hw, ac))
				break;
		}
		ieee80211_txq_schedule_end(hw, ac);
	}
	rcu_read_unlock();
}

static void rtl_ax_wake_tx_queue(struct ieee80211_hw *hw, struct ieee80211_txq *queue)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	/* Let one NI/NAPI batch reach mac80211 before draining its queues,
	 * so A-MSDU can retain the payloads as separate DMA segments. */
	if (READ_ONCE(radio->monitor_started))
		tasklet_schedule(&radio->tx_tasklet);
}

static int rtl_ax_net_setup_tc(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct net_device *netdev, enum tc_setup_type type, void *data)
{
	return rtl9617c_wifi_setup_tc(netdev, type, data);
}

static int rtl_ax_set_key(struct ieee80211_hw *hw, enum set_key_cmd cmd,
	struct ieee80211_vif *vif, struct ieee80211_sta *sta,
	struct ieee80211_key_conf *key)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned int macid = sta ? ((struct rtl_ax_station *)sta->drv_priv)->macid : 0;
	unsigned int slot = key->flags & IEEE80211_KEY_FLAG_PAIRWISE ? key->keyidx : 2 + key->keyidx;
	struct rtl_ax_security_role role = radio->security_roles[macid];
	u8 cam[24], dctl[68];
	unsigned int index;
	int ret;

	if (key->cipher != WLAN_CIPHER_SUITE_CCMP)
		return -EOPNOTSUPP;
	if (key->flags & IEEE80211_KEY_FLAG_PAIRWISE) {
		ret = rtl_ax_tx_ni_retire_peer(radio->tx, macid);
		if (cmd == DISABLE_KEY) rtl_ax_rx_key(radio->rx, macid, NULL);
		if (ret) return ret;
	}
	if (cmd == SET_KEY) {
		index = find_first_zero_bit(radio->security_ids, RTL_AX_SECURITY_ENTRIES);
		if (index == RTL_AX_SECURITY_ENTRIES)
			return -ENOSPC;
		rtl_ax_security_cam(cam, index, key->key);
		ret = rtl_ax_command_flags(radio, RTL_AX_H2C_CMD, 1, 10, 1,
			cam, sizeof(cam), false, true);
		if (ret)
			goto done;
		role.valid |= BIT(slot);
		role.keyid[slot] = key->keyidx;
		role.cam[slot] = index;
		if (key->flags & IEEE80211_KEY_FLAG_PAIRWISE)
			role.tx_keyid = key->keyidx;
	} else {
		index = key->hw_key_idx;
		ret = rtl_ax_tx_flush_key(radio->tx, index);
		if (ret)
			goto done;
		role.valid &= ~BIT(slot);
		role.keyid[slot] = 0;
		role.cam[slot] = 0;
	}
	rtl_ax_security_dctl(dctl, macid, &role);
	if (cmd == SET_KEY && (key->flags & IEEE80211_KEY_FLAG_PAIRWISE)) {
		u64 pn = atomic64_read(&key->tx_pn);
		rtl_ax_store32(dctl + 4, (u32)(pn & 0xffff) << 16);
		rtl_ax_store32(dctl + 8, pn >> 16);
		rtl_ax_store32(dctl + 36, 0xffff0000);
		rtl_ax_store32(dctl + 40, 0xffffffff);
	}
	ret = rtl_ax_command_flags(radio, RTL_AX_H2C_DATA, 1, 5, 9,
		dctl, sizeof(dctl), false, true);
	if (ret)
		goto done;
	radio->security_roles[macid] = role;
	if (cmd == SET_KEY) {
		__set_bit(index, radio->security_ids);
		key->hw_key_idx = index;
		key->flags |= IEEE80211_KEY_FLAG_SW_MGMT_TX;
		if (key->flags & IEEE80211_KEY_FLAG_PAIRWISE) rtl_ax_rx_key(radio->rx, macid, key);
		radio->keys_installed++;
	} else {
		__set_bit(index, radio->security_deleting);
		rtl_ax_security_cam(cam, index, NULL);
		ret = rtl_ax_command_flags(radio, RTL_AX_H2C_CMD, 1, 10, 1,
			cam, sizeof(cam), false, true);
		if (!ret) {
			__clear_bit(index, radio->security_ids);
			__clear_bit(index, radio->security_deleting);
			radio->keys_removed++;
		}
	}
done:
	if (ret && test_bit(index, radio->security_deleting))
		wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
	radio->key_result = ret;
	dev_info(&radio->pdev->dev, "native CCMP key %s MACID%u slot%u CAM%u result=%d\n",
		cmd == SET_KEY ? "install" : "remove", macid, slot, index, ret);
	return ret;
}

static int rtl_ax_net_fill_forward_path(struct ieee80211_hw *hw,
	struct ieee80211_vif *vif, struct ieee80211_sta *sta,
	struct net_device_path_ctx *ctx, struct net_device_path *path)
{
	path->type = DEV_PATH_ETHERNET;
	path->dev = ctx->dev;
	ctx->dev = NULL;
	return 0;
}

static void rtl_ax_sta_notify(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	enum sta_notify_cmd command, struct ieee80211_sta *sta)
{
	struct rtl_ax_station *station = (void *)sta->drv_priv;

	rtl_ax_tx_peer_sleep(rtl_ax_monitor_radio(hw)->tx, station->macid,
		command == STA_NOTIFY_SLEEP);

}

static void rtl_ax_release_buffered(struct ieee80211_hw *hw, struct ieee80211_sta *sta,
	u16 tids, int num_frames, enum ieee80211_frame_release_type reason, bool more_data)
{
	struct rtl_ax_station *station = (void *)sta->drv_priv;

	rtl_ax_tx_ps_release(rtl_ax_monitor_radio(hw)->tx, sta, station->macid, tids,
		reason == IEEE80211_FRAME_RELEASE_UAPSD, more_data);
}

static void rtl_ax_sta_rate_update(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct rtl_ax_station *station = (void *)sta->drv_priv;
	struct ieee80211_sta_rates *rates;
	u32 rate, caps;

	/* Firmware RA owns these peers; a Minstrel HT update loses HE/LDPC state. */
	if (!station->macid || radio->ra_registered[station->macid]) return;
	rcu_read_lock();
	rates = rcu_dereference(sta->rates);
	if (rates && (rates->rate[0].flags & IEEE80211_TX_RC_MCS)) {
		rate = 0x80 + rates->rate[0].idx;
		if (rates->rate[0].flags & IEEE80211_TX_RC_SHORT_GI) rate |= BIT(12);
		caps = sta->deflink.ht_cap.mcs.rx_mask[0] | sta->deflink.ht_cap.mcs.rx_mask[1] << 8;
		if (sta->deflink.ht_cap.cap & IEEE80211_HT_CAP_SGI_20) caps |= BIT(16);
		unsigned int amsdu = sta->cur->max_amsdu_len;
		if (sta->cur->max_rc_amsdu_len) amsdu = min(amsdu, sta->cur->max_rc_amsdu_len);
		caps |= min(amsdu, 7935U) << 18;
		rtl_ax_tx_peer_rate(radio->tx, station->macid, rate, caps);
	}
	rcu_read_unlock();
}

static void rtl_ax_ra_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, ra_work.work);
	u8 command[1024] = {};
	unsigned int i, entries = 0;
	unsigned long flags;

	if (!radio->monitor_started) return;
	/* PHY reports include neighbouring traffic. Sample for each two-second
	 * firmware RSSI update instead of dispatching every received PPDU. */
	if (!radio->ra_sampling) {
		radio->ra_sampling = true;
		rtl_ax_update32(&radio->io, 0xce40, 0, BIT(0));
		wiphy_delayed_work_queue(wiphy, &radio->ra_work, msecs_to_jiffies(20));
		return;
	}
	rtl_ax_update32(&radio->io, 0xce40, BIT(0), 0);
	radio->ra_sampling = false;
	spin_lock_irqsave(&radio->command_lock, flags);
	for (i = 1; i < 128; i++) {
		u8 *setting = command + entries * 8;
		if (!radio->ra_registered[i] || !radio->ra_rssi_samples[i]) continue;
		setting[0] = i;
		setting[1] = BIT(7) | (radio->ra_rssi[i][1] >> 1);
		setting[2] = BIT(7); /* No client beacon RSSI on an AP. */
		setting[7] = radio->ra_rssi[i][2] >> 1;
		entries++;
	}
	spin_unlock_irqrestore(&radio->command_lock, flags);
	if (entries) {
		command[entries * 8 - 1] |= BIT(7);
		command[entries * 8] = 0xff;
		radio->ra_rssi_result = rtl_ax_command_flags(radio, RTL_AX_H2C_LONG_DATA,
			2, 1, 1, command, sizeof(command), false, false);
		radio->ra_rssi_sent += !radio->ra_rssi_result;
	}
	wiphy_delayed_work_queue(wiphy, &radio->ra_work, 2 * HZ);
}

static void rtl_ax_sta_statistics(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta, struct station_info *info)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned int id = ((struct rtl_ax_station *)sta->drv_priv)->macid;
	unsigned long flags;
	u8 feedback[8], rssi;
	u64 reports, signals;
	static const u8 bandwidths[] = { RATE_INFO_BW_20, RATE_INFO_BW_40, RATE_INFO_BW_80, RATE_INFO_BW_160 };

	spin_lock_irqsave(&radio->command_lock, flags);
	memcpy(feedback, radio->ra_feedback[id], 8);
	reports = radio->ra_reports[id];
	signals = radio->ra_rssi_samples[id];
	rssi = radio->ra_rssi[id][0];
	spin_unlock_irqrestore(&radio->command_lock, flags);
	if (signals) {
		info->signal = (int)(rssi >> 1) - 110;
		info->filled |= BIT_ULL(NL80211_STA_INFO_SIGNAL);
	}
	if (!reports || (feedback[4] & BIT(7))) return;
	memset(&info->txrate, 0, sizeof(info->txrate));
	info->txrate.bw = bandwidths[(feedback[5] >> 5) & 3];
	if ((feedback[5] & 3) == 3) {
		u8 giltf = (feedback[5] >> 2) & 7;

		info->txrate.flags = RATE_INFO_FLAGS_HE_MCS;
		info->txrate.mcs = feedback[4] & 15;
		info->txrate.nss = ((feedback[4] >> 4) & 7) + 1;
		info->txrate.he_gi = giltf == 1 || giltf == 3 || giltf == 5 ? NL80211_RATE_INFO_HE_GI_0_8 :
			giltf == 2 || giltf == 4 ? NL80211_RATE_INFO_HE_GI_1_6 : NL80211_RATE_INFO_HE_GI_3_2;
	} else if ((feedback[5] & 3) == 2) {
		info->txrate.flags = RATE_INFO_FLAGS_VHT_MCS;
		info->txrate.mcs = feedback[4] & 15;
		info->txrate.nss = ((feedback[4] >> 4) & 7) + 1;
	} else if ((feedback[5] & 3) == 1) {
		info->txrate.flags = RATE_INFO_FLAGS_MCS;
		info->txrate.mcs = feedback[4] & 127;
	} else return;
	if ((feedback[5] & 3) != 3 && ((feedback[5] >> 2) & 7))
		info->txrate.flags |= RATE_INFO_FLAGS_SHORT_GI;
	info->filled |= BIT_ULL(NL80211_STA_INFO_TX_BITRATE);
}

static void rtl_ax_get_key_seq(struct ieee80211_hw *hw,
	struct ieee80211_key_conf *key, struct ieee80211_key_seq *seq)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	u64 pn = atomic64_read(&key->tx_pn);
	unsigned int macid, slot, i;
	unsigned long flags;
	if (key->flags & IEEE80211_KEY_FLAG_PAIRWISE)
		for (macid = 1; macid < 128; macid++)
			for (slot = 0; slot < 2; slot++)
				if ((radio->security_roles[macid].valid & BIT(slot)) &&
				    radio->security_roles[macid].cam[slot] == key->hw_key_idx) {
					spin_lock_irqsave(&radio->pn_lock, flags);
					pn = rtl_ax_security_read_pn(&radio->io, macid);
					spin_unlock_irqrestore(&radio->pn_lock, flags);
					goto found;
				}
found:
	for (i = 0; i < 6; i++) seq->ccmp.pn[i] = pn >> (i * 8);
}

static const struct ieee80211_ops rtl_ax_monitor_ops = {
	.tx = rtl_ax_monitor_tx,
	.wake_tx_queue = rtl_ax_wake_tx_queue,
	.net_setup_tc = rtl_ax_net_setup_tc,
	.net_fill_forward_path = rtl_ax_net_fill_forward_path,
	.start = rtl_ax_monitor_start,
	.stop = rtl_ax_monitor_stop,
	.config = rtl_ax_monitor_config,
	.add_interface = rtl_ax_monitor_add_interface,
	.remove_interface = rtl_ax_monitor_remove_interface,
	.start_ap = rtl_ax_ap_start,
	.stop_ap = rtl_ax_ap_stop,
	.bss_info_changed = rtl_ax_ap_changed,
	.conf_tx = rtl_ax_ap_conf_tx,
	.ampdu_action = rtl_ax_ampdu_action,
	.sta_state = rtl_ax_sta_state,
	.sta_notify = rtl_ax_sta_notify,
	.release_buffered_frames = rtl_ax_release_buffered,
	.sta_rate_tbl_update = rtl_ax_sta_rate_update,
	.sta_statistics = rtl_ax_sta_statistics,
	.set_key = rtl_ax_set_key,
	.get_key_seq = rtl_ax_get_key_seq,
	.set_tim = rtl_ax_set_tim,
	.configure_filter = rtl_ax_monitor_filter,
	.add_chanctx = ieee80211_emulate_add_chanctx,
	.remove_chanctx = ieee80211_emulate_remove_chanctx,
	.change_chanctx = ieee80211_emulate_change_chanctx,
};

static int rtl_ax_monitor_receive(struct rtl_ax_ap *radio,
	const struct rtl_ax_rx_packet *packet)
{
	static const u8 bandwidths[] = {
		RATE_INFO_BW_20, RATE_INFO_BW_40, RATE_INFO_BW_80, RATE_INFO_BW_160,
	};
	static const u8 he_gi[] = {
		NL80211_RATE_INFO_HE_GI_3_2, NL80211_RATE_INFO_HE_GI_0_8,
		NL80211_RATE_INFO_HE_GI_1_6, NL80211_RATE_INFO_HE_GI_0_8,
		NL80211_RATE_INFO_HE_GI_1_6, NL80211_RATE_INFO_HE_GI_0_8,
	};
	struct sk_buff *skb;
	struct ieee80211_rx_status *status;
	u32 word = rtl_ax_ap_read32(packet->descriptor + 4);
	u16 rate = (word >> 16) & 0x1ff;
	u8 bandwidth = word >> 29, gi_ltf = (word >> 25) & 7;
	bool profile = READ_ONCE(rtl_ax_datapath_profile);
	u64 begin = profile ? ktime_get_ns() : 0, delivered = 0;

	/* mac80211 RX_INCLUDES_FCS requires a MAC header before the FCS.
	 * Hardware can report FCS-only records during receive transitions. */
	if (packet->bytes < 2 + FCS_LEN)
		return -EINVAL;
	/* Actual hal_parsing_rx_wd_C/XB word1 carries rate16:24,
	 * GI/LTF25:27 and bandwidth29:31; no PPDU-derived RSSI is invented. */
	if (bandwidth >= ARRAY_SIZE(bandwidths))
		return -EOPNOTSUPP;
	if ((rate >> 7) == 0 && (rate > 11 || (radio->chip == RTL_AX_8852C && rate < 4)))
		return -EOPNOTSUPP;
	if ((rate >> 7) == 3 && gi_ltf >= ARRAY_SIZE(he_gi))
		return -EOPNOTSUPP;
	skb = dev_alloc_skb(packet->bytes);
	if (!skb)
		return -ENOMEM;
	skb_put_data(skb, packet->payload, packet->bytes);
	status = IEEE80211_SKB_RXCB(skb);
	memset(status, 0, sizeof(*status));
	status->flag = RX_FLAG_NO_SIGNAL_VAL;
	if (!READ_ONCE(radio->ap_started))
		status->flag |= RX_FLAG_ONLY_MONITOR;
	/* The chip retains IV/MIC, so mac80211 still checks the receive PN. */
	word = rtl_ax_ap_read32(packet->descriptor + 12);
	if ((word & BIT(2)) && !(word & BIT(1))) {
		if (packet->icv_error) {
			dev_kfree_skb(skb);
			return -EBADMSG;
		}
		status->flag |= RX_FLAG_DECRYPTED;
		radio->rx_decrypted++;
	}
	if (packet->crc_error)
		status->flag |= RX_FLAG_FAILED_FCS_CRC;
	status->band = radio->chip == RTL_AX_8852C ? NL80211_BAND_5GHZ : NL80211_BAND_2GHZ;
	status->freq = ieee80211_channel_to_frequency(radio->channel.primary, status->band);
	status->bw = bandwidths[bandwidth];
	switch (rate >> 7) {
	case 0:
		status->encoding = RX_ENC_LEGACY;
		status->rate_idx = radio->chip == RTL_AX_8852C ? rate - 4 : rate;
		break;
	case 1:
		status->encoding = RX_ENC_HT;
		status->rate_idx = rate & 31;
		if (gi_ltf)
			status->enc_flags |= RX_ENC_FLAG_SHORT_GI;
		break;
	case 2:
		status->encoding = RX_ENC_VHT;
		status->rate_idx = rate & 15;
		status->nss = ((rate >> 4) & 7) + 1;
		if (gi_ltf)
			status->enc_flags |= RX_ENC_FLAG_SHORT_GI;
		break;
	case 3:
		status->encoding = RX_ENC_HE;
		status->rate_idx = rate & 15;
		status->nss = ((rate >> 4) & 7) + 1;
		status->he_gi = he_gi[gi_ltf];
		break;
	}
	if (packet->bytes >= 2 && ieee80211_is_beacon(*(__le16 *)packet->payload))
		status->boottime_ns = ktime_get_boottime_ns();
	radio->mac80211_packets++;
	if (profile) {
		delivered = ktime_get_ns();
		radio->rx_copy_bytes += packet->bytes;
		radio->rx_prepare_ns += delivered - begin;
	}
	/* RX dispatch runs in rtl_ax_rx_work, not the hardware IRQ. Process
	 * the frame here so rx_close's synchronous work cancellation also
	 * completes mac80211 delivery before .stop returns. */
	ieee80211_rx_ni(radio->hw, skb);
	if (profile)
		radio->rx_mac80211_ns += ktime_get_ns() - delivered;
	return 0;
}

static void rtl_ax_regulatory_notify(struct wiphy *wiphy, struct regulatory_request *request)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(wiphy_to_ieee80211_hw(wiphy));
	int ret;

	mutex_lock(&radio->command_mutex);
	radio->power_country[0] = request->alpha2[0];
	radio->power_country[1] = request->alpha2[1];
	if (radio->channel.configured) {
		ret = rtl_ax_power_apply(&radio->io, radio->chip, &radio->power_inputs,
			radio->channel.band, radio->channel.primary, radio->channel.center,
			radio->channel.bandwidth, radio->power_country,
			radio->requested_tx_dbm, &radio->power_hardware);
		if (ret)
			dev_err(&radio->pdev->dev, "country power table update failed: %d\n", ret);
	}
	mutex_unlock(&radio->command_mutex);
}

static int rtl_ax_monitor_register(struct rtl_ax_ap *radio)
{
	static const u16 rates[] = { 10, 20, 55, 110, 60, 90, 120, 180, 240, 360, 480, 540 };
	struct ieee80211_hw *hw;
	unsigned int i, first_rate = radio->chip == RTL_AX_8852C ? 4 : 0;
	unsigned int channels = radio->chip == RTL_AX_8852C ? 4 : 13;
	enum nl80211_band band = radio->chip == RTL_AX_8852C ? NL80211_BAND_5GHZ : NL80211_BAND_2GHZ;
	int ret;

	hw = ieee80211_alloc_hw(sizeof(radio), &rtl_ax_monitor_ops);
	if (!hw)
		return -ENOMEM;
	*(struct rtl_ax_ap **)hw->priv = radio;
	SET_IEEE80211_DEV(hw, &radio->pdev->dev);
	SET_IEEE80211_PERM_ADDR(hw, radio->flash_calibration.wlan_address);
	/* Both radios share one factory WLAN base; give 5 GHz its own local address. */
	if (radio->chip == RTL_AX_8852C) {
		hw->wiphy->perm_addr[0] |= 2;
		hw->wiphy->perm_addr[5] += 3;
	}
	hw->queues = 4;
	hw->max_rates = 1;
	hw->max_report_rates = 1;
	radio->requested_tx_dbm = 20;
	memcpy(radio->power_country, "00", 3);
	hw->wiphy->reg_notifier = rtl_ax_regulatory_notify;
	hw->sta_data_size = sizeof(struct rtl_ax_station);
	ieee80211_hw_set(hw, RX_INCLUDES_FCS);
	ieee80211_hw_set(hw, AMPDU_AGGREGATION);
	ieee80211_hw_set(hw, SUPPORTS_AMSDU_IN_AMPDU);
	/* RPQ releases individual MPDUs without the transmitted PPDU length. */
	ieee80211_hw_set(hw, TX_STATUS_NO_AMPDU_LEN);
	ieee80211_hw_set(hw, SUPPORT_FAST_XMIT);
	ieee80211_hw_set(hw, SUPPORTS_TX_ENCAP_OFFLOAD);
	ieee80211_hw_set(hw, TX_AMSDU);
	ieee80211_hw_set(hw, TX_FRAG_LIST);
	hw->max_tx_fragments = 4;
	hw->max_tx_aggregation_subframes = 64;
	hw->max_rx_aggregation_subframes = 64;
	hw->wiphy->interface_modes = BIT(NL80211_IFTYPE_MONITOR) | BIT(NL80211_IFTYPE_AP);
	hw->wiphy->available_antennas_rx = 3;
	for (i = 0; i < channels; i++) {
		radio->channels[i].band = band;
		radio->channels[i].hw_value = band == NL80211_BAND_5GHZ ? 36 + i * 4 : i + 1;
		radio->channels[i].center_freq = ieee80211_channel_to_frequency(radio->channels[i].hw_value, band);
	}
	for (i = first_rate; i < ARRAY_SIZE(rates); i++) {
		radio->rates[i - first_rate].bitrate = rates[i];
		radio->rates[i - first_rate].hw_value = i;
	}
	radio->band.channels = radio->channels;
	radio->band.n_channels = channels;
	radio->band.bitrates = radio->rates;
	radio->band.n_bitrates = ARRAY_SIZE(rates) - first_rate;
	/* Board 2T2R is configured by the original normal-mode TRX tail.
	 * Publish the implemented 20MHz HT TX/RX path; BA/AMPDU follows separately. */
	radio->band.ht_cap.ht_supported = true;
	radio->band.ht_cap.cap = IEEE80211_HT_CAP_SGI_20 | IEEE80211_HT_CAP_MAX_AMSDU;
	radio->band.ht_cap.ampdu_factor = IEEE80211_HT_MAX_AMPDU_64K;
	radio->band.ht_cap.mcs.rx_mask[0] = 0xff;
	radio->band.ht_cap.mcs.rx_mask[1] = 0xff;
	radio->band.ht_cap.mcs.rx_highest = cpu_to_le16(144);
	radio->band.ht_cap.mcs.tx_params = IEEE80211_HT_MCS_TX_DEFINED;
	if (radio->chip == RTL_AX_8852C) {
		radio->band.ht_cap.cap |= IEEE80211_HT_CAP_SUP_WIDTH_20_40 | IEEE80211_HT_CAP_SGI_40;
		radio->band.ht_cap.mcs.rx_highest = cpu_to_le16(300);
		radio->band.vht_cap.vht_supported = true;
		/* Original VHT defaults: max MPDU code1, A-MPDU exponent7. */
		radio->band.vht_cap.cap = IEEE80211_VHT_CAP_SHORT_GI_80 | IEEE80211_VHT_CAP_MAX_MPDU_LENGTH_7991 |
			(IEEE80211_VHT_MAX_AMPDU_1024K << IEEE80211_VHT_CAP_MAX_A_MPDU_LENGTH_EXPONENT_SHIFT);
		/* Two streams MCS0..9; all higher NSS are unsupported. */
		radio->band.vht_cap.vht_mcs.rx_mcs_map = cpu_to_le16(0xfffa);
		radio->band.vht_cap.vht_mcs.tx_mcs_map = cpu_to_le16(0xfffa);
		radio->band.vht_cap.vht_mcs.rx_highest = cpu_to_le16(867);
		radio->band.vht_cap.vht_mcs.tx_highest = cpu_to_le16(867);
	}
	radio->he_band.types_mask = BIT(NL80211_IFTYPE_AP);
	radio->he_band.he_cap.has_he = true;
	radio->he_band.he_cap.he_cap_elem.mac_cap_info[0] = IEEE80211_HE_MAC_CAP0_HTC_HE;
	/* Original rtw_build_he_mac_caps extends the A-MPDU exponent by2. */
	radio->he_band.he_cap.he_cap_elem.mac_cap_info[3] = IEEE80211_HE_MAC_CAP3_MAX_AMPDU_LEN_EXP_EXT_2;
	radio->he_band.he_cap.he_cap_elem.mac_cap_info[4] = IEEE80211_HE_MAC_CAP4_AMSDU_IN_AMPDU;
	radio->he_band.he_cap.he_cap_elem.phy_cap_info[0] = band == NL80211_BAND_5GHZ ?
		IEEE80211_HE_PHY_CAP0_CHANNEL_WIDTH_SET_40MHZ_80MHZ_IN_5G : 0;
	radio->he_band.he_cap.he_cap_elem.phy_cap_info[1] = IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD |
		IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US;
	radio->he_band.he_cap.he_cap_elem.phy_cap_info[7] = IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI;
	radio->he_band.he_cap.he_mcs_nss_supp.rx_mcs_80 = cpu_to_le16(0xfffa);
	radio->he_band.he_cap.he_mcs_nss_supp.tx_mcs_80 = cpu_to_le16(0xfffa);
	radio->he_band.he_cap.he_mcs_nss_supp.rx_mcs_160 = cpu_to_le16(0xffff);
	radio->he_band.he_cap.he_mcs_nss_supp.tx_mcs_160 = cpu_to_le16(0xffff);
	radio->he_band.he_cap.he_mcs_nss_supp.rx_mcs_80p80 = cpu_to_le16(0xffff);
	radio->he_band.he_cap.he_mcs_nss_supp.tx_mcs_80p80 = cpu_to_le16(0xffff);
	_ieee80211_set_sband_iftype_data(&radio->band, &radio->he_band, 1);
	hw->wiphy->bands[band] = &radio->band;
	radio->hw = hw;
	spin_lock_init(&radio->pn_lock);
	wiphy_work_init(&radio->beacon_work, rtl_ax_tim_work);
	wiphy_work_init(&radio->offload_work, rtl_ax_offload_work);
	rtl_ax_tx_bind_ready(radio->tx, rtl_ax_offload_ready, radio);
	tasklet_setup(&radio->tx_tasklet, rtl_ax_tx_queues);
	wiphy_delayed_work_init(&radio->cleanup_work, rtl_ax_cleanup_work);
	wiphy_delayed_work_init(&radio->ra_work, rtl_ax_ra_work);
	ret = ieee80211_register_hw(hw);
	if (ret) {
		radio->hw = NULL;
		ieee80211_free_hw(hw);
	}
	return ret;
}

static void rtl_ax_monitor_unregister(struct rtl_ax_ap *radio)
{
	if (!radio->hw)
		return;
	WRITE_ONCE(radio->monitor_started, false);
	ieee80211_unregister_hw(radio->hw);
	tasklet_kill(&radio->tx_tasklet);
}

static void rtl_ax_monitor_free(struct rtl_ax_ap *radio)
{
	if (!radio->hw)
		return;
	ieee80211_free_hw(radio->hw);
	radio->hw = NULL;
}

static ssize_t radio_mac80211_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	unsigned int i, retired = 0, ba_active = 0, ba_deleting = 0, tx_stopping = 0;

	for (i = 1; i < ARRAY_SIZE(radio->retired_stations); i++) {
		retired += radio->retired_stations[i].pending;
		tx_stopping += hweight16(radio->tx_ba[i].stopping);
	}
	for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++) {
		ba_active += radio->rx_ba[i].valid;
		ba_deleting += radio->rx_ba[i].deleting;
	}

	return sysfs_emit(buffer, "registered=%u receive_active=%u handed_to_mac80211=%llu "
		"TX_unimplemented=%llu AP_started=%u AP_result=%d station_added=%llu station_removed=%llu "
		"RX_BA_started=%llu RX_BA_stopped=%llu RX_BA_result=%d "
		"keys_installed=%llu keys_removed=%llu keys_active=%u key_result=%d RX_decrypted=%llu "
		"TIM_requests=%llu TIM_updates=%llu "
		"profile=%u RX_copy_bytes=%llu RX_prepare_ns=%llu RX_mac80211_ns=%llu "
		"retired_MACIDs=%u RX_BA_active=%u RX_BA_deleting=%u TX_BA_stopping=%u "
		"auto_offload_starts=%llu auto_offload_result=%d\n",
		!!radio->hw, radio->monitor_started, radio->mac80211_packets, radio->tx_unimplemented,
		radio->ap_started, radio->ap_result, radio->station_added, radio->station_removed,
		radio->rx_ba_started, radio->rx_ba_stopped, radio->rx_ba_result,
		radio->keys_installed, radio->keys_removed,
		bitmap_weight(radio->security_ids, RTL_AX_SECURITY_ENTRIES), radio->key_result, radio->rx_decrypted,
		radio->tim_requests, radio->tim_updates,
		rtl_ax_datapath_profile, radio->rx_copy_bytes, radio->rx_prepare_ns, radio->rx_mac80211_ns,
		retired, ba_active, ba_deleting, tx_stopping, radio->offload_started, radio->offload_result);
}
static DEVICE_ATTR_RO(radio_mac80211);
#endif
