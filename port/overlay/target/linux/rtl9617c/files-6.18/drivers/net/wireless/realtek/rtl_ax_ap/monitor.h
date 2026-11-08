/* SPDX-License-Identifier: GPL-2.0-only */
/* Linux receive integration for the native C 5GHz and XB2.4GHz paths.
 * Hardware channel and filters reuse the original-backed native controls. */
#ifndef RTL_AX_AP_MONITOR_H
#define RTL_AX_AP_MONITOR_H
#include <linux/rtl9617c-wifi.h>
#include "tx-format.h"
#include "rate.h"
#include "rate-report.h"

static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw)
{
	return *(struct rtl_ax_ap **)hw->priv;
}

static int rtl_ax_set_rts_threshold(struct ieee80211_hw *hw, int radio_idx, u32 value)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	u32 time = value == (u32)-1 ? hw_rts_time : 255U << 5;
	u32 length = value == (u32)-1 ? hw_rts_length : value;

	/* Same C614 units as factory hw_rts_th; retain the board default. */
	mutex_lock(&radio->command_mutex);
	radio->io.write16(radio, 0xc614,
		min(time >> 5, 255U) << 8 | min(length >> 4, 255U));
	mutex_unlock(&radio->command_mutex);
	return 0;
}

static unsigned int rtl_ax_tx_basic_rate(struct rtl_ax_ap *radio, struct ieee80211_sta *sta,
	struct ieee80211_tx_info *info)
{
	struct ieee80211_vif *vif = info->control.vif;
	u32 basic = vif ? vif->bss_conf.basic_rates : 0;
	unsigned int index, rate;

	if (sta) basic &= sta->deflink.supp_rates[radio->band.band];
	if (info->flags & IEEE80211_TX_CTL_NO_CCK_RATE && radio->band.band == NL80211_BAND_2GHZ)
		basic &= ~15U;
	index = basic ? __ffs(basic) : radio->band.band == NL80211_BAND_2GHZ &&
		(info->flags & IEEE80211_TX_CTL_NO_CCK_RATE) ? 4 : 0;
	if (index >= radio->band.n_bitrates) index = 0;
	rate = radio->band.bitrates[index].hw_value;
	info->control.rates[0].idx = index;
	info->control.rates[0].flags = 0;
	info->control.rates[0].count = 1;
	return rate;
}

/* The simulated single-channel interface keeps mac80211 software scanning.
 * Patch208 also notifies config when only radar_enabled changes. */
static int rtl_ax_chandef_locked(struct rtl_ax_ap *radio, const struct cfg80211_chan_def *def)
{
	unsigned int bandwidth;

	if (radio->scanning && radio->scan_offload_result) return radio->scan_offload_result;
	switch (def->width) {
	case NL80211_CHAN_WIDTH_20_NOHT:
	case NL80211_CHAN_WIDTH_20: bandwidth = 0; break;
	case NL80211_CHAN_WIDTH_40: bandwidth = 1; break;
	case NL80211_CHAN_WIDTH_80: bandwidth = 2; break;
	case NL80211_CHAN_WIDTH_160: bandwidth = 3; break;
	default: return -EOPNOTSUPP;
	}
	if (radio->chip != RTL_AX_8852C && bandwidth > 1) return -EOPNOTSUPP;
	return rtl_ax_receive_configure(radio, def->chan->hw_value,
		ieee80211_frequency_to_channel(def->center_freq1), bandwidth);
}

static int rtl_ax_monitor_start(struct ieee80211_hw *hw)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret;

	mutex_lock(&radio->command_mutex);
	radio->radar_required = hw->conf.radar_enabled;
	ret = rtl_ax_chandef_locked(radio, &hw->conf.chandef);
	if (ret) {
		mutex_unlock(&radio->command_mutex);
		return ret;
	}
	rtl_ax_receive_mode(&radio->io, radio->ap_started ? 4 : radio->sta_vif ? 2 : 0);
	radio->dig.fahm_triggered = radio->dig.fahm_configured = false;
	WRITE_ONCE(radio->monitor_started, true);
	rtl_ax_update32(&radio->io, 0x9c18, 3, 1);
	/* Actual C/XB hal_start: enable MAC/PLCP/RX-count reports for CRC-OK. */
	radio->io.write32(radio, 0xce40, 0x2f);
	rtl_ax_rx_open(radio->rx);
	rtl_ax_tx_open(radio->tx);
	mutex_unlock(&radio->command_mutex);
	rtl_ax_set_rts_threshold(hw, -1, hw->wiphy->rts_threshold);
	wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, 0);
	wiphy_delayed_work_queue(hw->wiphy, &radio->ra_work, HZ);
	wiphy_delayed_work_queue(hw->wiphy, &radio->dm_work, 2 * HZ);
	return 0;
}

static void rtl_ax_monitor_stop(struct ieee80211_hw *hw, bool suspend)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned long flags;
	int ret;

	wiphy_delayed_work_cancel(hw->wiphy, &radio->cleanup_work);
	wiphy_delayed_work_cancel(hw->wiphy, &radio->ra_work);
	wiphy_delayed_work_cancel(hw->wiphy, &radio->dm_work);
	wiphy_delayed_work_cancel(hw->wiphy, &radio->mesh_sync_work);
	wiphy_work_cancel(hw->wiphy, &radio->ra_update_work);
	/* Wiphy serialization keeps the AP alive; PKT_DROP takes command_mutex. */
	ret = rtl_ax_tx_ni_route(radio->tx, NULL, 0);
	if (ret) dev_err(&radio->pdev->dev, "NI withdrawal on radio stop failed: %d\n", ret);
	mutex_lock(&radio->command_mutex);
	spin_lock_irqsave(&radio->command_lock, flags);
	radio->dfs.enabled = false;
	spin_unlock_irqrestore(&radio->command_lock, flags);
	WRITE_ONCE(radio->monitor_started, false);
	rtl_ax_update32(&radio->io, 0xce40, BIT(0), 0);
	tasklet_kill(&radio->tx_tasklet);
	rtl_ax_update32(&radio->io, 0xc400, BIT(12), 0);
	radio->beacon_enabled = false;
	ret = rtl_ax_tx_pause(radio->tx);
	if (ret) dev_err(&radio->pdev->dev, "TX pause on radio stop failed: %d\n", ret);
	rtl_ax_rx_close(radio->rx);
	rtl_ax_scan_complete_frame(radio, NULL);
	radio->dfs.error = rtl_ax_dfs_stop_locked(radio);
	if (radio->dfs.error)
		dev_err(&radio->pdev->dev, "radar stop failed: %d\n", radio->dfs.error);
	if (ret) radio->dfs.error = ret;
	radio->dfs.in_cac = false;
	radio->radar_required = false;
	mutex_unlock(&radio->command_mutex);
}

static int rtl_ax_monitor_config(struct ieee80211_hw *hw, int radio_idx, u32 changed)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret = 0;

	mutex_lock(&radio->command_mutex);
	if (changed & IEEE80211_CONF_CHANGE_POWER)
		radio->requested_tx_dbm = hw->conf.power_level;
	if (changed & IEEE80211_CONF_CHANGE_CHANNEL) {
		radio->radar_required = hw->conf.radar_enabled;
		ret = rtl_ax_chandef_locked(radio, &hw->conf.chandef);
	}
	/* Also apply when a channel notification keeps the physical channel
	 * unchanged, or the user repeats a previously failed power request. */
	if (!ret && (changed & IEEE80211_CONF_CHANGE_POWER) && radio->channel.configured)
		ret = rtl_ax_power_apply(&radio->io, radio->chip, &radio->power_inputs,
			radio->channel.band, radio->channel.primary, radio->channel.center,
			radio->channel.bandwidth, radio->power_country,
			radio->requested_tx_dbm, &radio->power_hardware);
	mutex_unlock(&radio->command_mutex);
	return ret;
}

static int rtl_ax_monitor_get_txpower(struct ieee80211_hw *hw,
	struct ieee80211_vif *vif, unsigned int link_id, int *dbm)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret = 0;

	mutex_lock(&radio->command_mutex);
	ret = rtl_ax_power_verify(&radio->io, &radio->power_hardware);
	if (!ret)
		/* This is the programmed upper limit, not per-packet RF power. */
		*dbm = radio->power_hardware.limit_dbm;
	mutex_unlock(&radio->command_mutex);
	return ret;
}

#include "station.h"
static void rtl_ax_client_release(struct rtl_ax_ap *radio);
static int rtl_ax_station_release(struct rtl_ax_ap *radio, unsigned int macid);

static void rtl_ax_update_vif_offload(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
	/* The client shortcut implements three-address routed operation. */
	if (vif->type == NL80211_IFTYPE_STATION && ieee80211_vif_to_wdev(vif)->use_4addr)
		vif->offload_flags &= ~IEEE80211_OFFLOAD_ENCAP_ENABLED;
}

static int rtl_ax_monitor_add_interface(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	if (vif->type == NL80211_IFTYPE_MONITOR)
		return 0;
	if (vif->type != NL80211_IFTYPE_AP && vif->type != NL80211_IFTYPE_STATION &&
	    vif->type != NL80211_IFTYPE_MESH_POINT)
		return -EOPNOTSUPP;
	if (radio->ap_vif || radio->sta_vif)
		return -EBUSY;
	/* .stop cancels deferred cleanup after a failed .add_interface. RX is
	 * running again here; finish the retired role before accepting a new vif. */
	if (radio->sta_role) {
		int ret = rtl_ax_station_release(radio, RTL_AX_CLIENT_MACID);

		if (ret) return ret;
	}
	if (vif->type == NL80211_IFTYPE_STATION) {
		int ret;

		if (ieee80211_vif_to_wdev(vif)->use_4addr) return -EOPNOTSUPP;
		radio->sta_4addr = false;
		radio->sta_vif = vif;
		ret = rtl_ax_client_prepare(radio, vif);
		if (ret) {
			rtl_ax_client_release(radio);
			radio->sta_vif = NULL;
			return ret;
		}
	} else radio->ap_vif = vif;
	rtl_ax_tx_ap_address(radio->tx, vif->addr);
	return 0;
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

static u64 rtl_ax_get_tsf(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	return rtl_ax_port0_tsf(&radio->io);
}

static void rtl_ax_offset_tsf(struct ieee80211_hw *hw, struct ieee80211_vif *vif, s64 offset)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	s64 total, units;
	int ret;

	if (offset < -0x3ffffLL * 32 || offset > 0x3ffffLL * 32) {
		ret = -ERANGE;
		goto failed;
	}
	total = offset + radio->mesh_tsf_remainder;
	units = div_s64(total, 32);
	if (!units) {
		radio->mesh_tsf_remainder = total;
		return;
	}
	mutex_lock(&radio->command_mutex);
	ret = rtl_ax_port0_offset_tsf(&radio->io, units);
	mutex_unlock(&radio->command_mutex);
failed:
	if (ret) {
		dev_err(&radio->pdev->dev, "mesh TSF adjustment failed: %d\n", ret);
		cfg80211_stop_iface(hw->wiphy, ieee80211_vif_to_wdev(vif), GFP_KERNEL);
	} else radio->mesh_tsf_remainder = total - units * 32;
}

static void rtl_ax_mesh_sync_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, mesh_sync_work.work);
	struct ieee80211_vif *vif = radio->ap_vif;
	struct sk_buff *skb;

	if (!radio->ap_started || !radio->beacon_enabled)
		return;
	/* Firmware repeats the uploaded template. Fetching the mesh beacon
	 * once per interval lets mac80211 schedule neighbor drift correction. */
	skb = ieee80211_beacon_get_template(radio->hw, vif, NULL, 0);
	dev_kfree_skb(skb);
	wiphy_delayed_work_queue(wiphy, &radio->mesh_sync_work,
		usecs_to_jiffies(vif->bss_conf.beacon_int * 1024));
}

static int rtl_ax_ap_start(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_bss_conf *conf)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned long flags;
	int ret;

	if (radio->radar_required &&
	    (radio->dfs.error || !radio->dfs.enabled || radio->dfs.detected))
		return radio->dfs.error ? radio->dfs.error : -EBUSY;
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
	/* RX radar can arrive while the firmware commands above are waiting. */
	spin_lock_irqsave(&radio->command_lock, flags);
	if (radio->radar_required && radio->dfs.detected) ret = -EBUSY;
	else {
		radio->dfs.in_cac = false;
		rtl_ax_tx_dfs_block(radio->tx, false);
		rtl_ax_update32(&radio->io, 0xc400, BIT(12), BIT(12));
		WRITE_ONCE(radio->mesh_started, vif->type == NL80211_IFTYPE_MESH_POINT);
		WRITE_ONCE(radio->ap_started, true);
		radio->beacon_enabled = true;
	}
	spin_unlock_irqrestore(&radio->command_lock, flags);
	radio->ap_result = ret;
	if (!ret && vif->type == NL80211_IFTYPE_MESH_POINT) {
		radio->mesh_tsf_remainder = 0;
		wiphy_delayed_work_queue(hw->wiphy, &radio->mesh_sync_work,
			usecs_to_jiffies(conf->beacon_int * 1024));
	}
	return ret;
}

static void rtl_ax_offload_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, offload_work);
	unsigned int macid, index;
	struct ieee80211_vif *vif = radio->sta_vif ? radio->sta_vif : radio->ap_vif;
	radio->offload_retry = false;
	if (radio->scanning || (!radio->ap_started && !radio->sta_associated)) return;
	if (vif->type == NL80211_IFTYPE_MESH_POINT) return;
	if (radio->sta_vif && (radio->sta_4addr || ieee80211_vif_to_wdev(vif)->use_4addr)) return;
	if (radio->sta_vif && radio->scan_offload_result) {
		radio->scan_offload_result = rtl_ax_tx_ni_retire_peer(radio->tx, RTL_AX_CLIENT_MACID);
		if (radio->scan_offload_result) {
			radio->offload_retry = true;
			wiphy_delayed_work_queue(wiphy, &radio->cleanup_work, HZ);
			return;
		}
	}
	for (macid = 1; macid < 128; macid++) {
		int ret;
		struct rtl_ax_security_role *role = &radio->security_roles[macid];
		/* NI bypasses wake_tx_queue; its active TIDs need the same BA
		 * request policy while data transmission continues normally. */
		if (radio->peer_authorized[macid]) {
			u8 tids = rtl_ax_tx_active_tids(radio->tx, macid);
			struct ieee80211_sta *sta;
			rcu_read_lock();
			sta = ieee80211_find_sta_by_ifaddr(radio->hw, radio->tx_ba[macid].address, vif->addr);
			if (sta && sta->wme && (sta->deflink.ht_cap.ht_supported ||
			    sta->deflink.vht_cap.vht_supported || sta->deflink.he_cap.has_he)) {
				struct rtl_ax_station *station = (void *)sta->drv_priv;
				for (unsigned int tid = 0; tid < 6; tid++)
					if ((tids & BIT(tid)) && !radio->tx_ba[macid].aggregation[tid] &&
					    !(radio->tx_ba[macid].stopping & BIT(tid)) &&
					    (!station->ba_retry[tid] || time_after_eq(jiffies, station->ba_retry[tid]))) {
						station->ba_retry[tid] = jiffies + HZ;
						ieee80211_start_tx_ba_session(sta, tid, 0);
					}
			}
			rcu_read_unlock();
		}
		if (!radio->peer_authorized[macid] || !radio->tx_ba[macid].aggregation[0] ||
		    radio->tx_ba[macid].stopping || !(role->valid & BIT(role->tx_keyid))) continue;
		for (index = 0; index < ARRAY_SIZE(radio->rx_ba); index++)
			if (radio->rx_ba[index].deleting && radio->rx_ba[index].macid == macid) break;
		if (index != ARRAY_SIZE(radio->rx_ba)) continue;
		for (index = 0; index < RTL_AX_SECURITY_ENTRIES; index++)
			if (radio->security_unlink[index] == macid + 1) break;
		if (index != RTL_AX_SECURITY_ENTRIES ||
		    !rtl_ax_tx_peer_security(radio->tx, macid, role->cam[role->tx_keyid], role->tx_keyid)) continue;
		ret = rtl_ax_tx_ni_peer(radio->tx, ieee80211_vif_to_wdev(vif)->netdev, macid);
		radio->offload_result = min(ret, 0);
		if (ret < 0) radio->offload_retry = true;
		if (ret > 0) radio->offload_started++;
	}
	if (radio->offload_retry)
		wiphy_delayed_work_queue(wiphy, &radio->cleanup_work, HZ);
}

static void rtl_ax_offload_ready(void *context)
{
	struct rtl_ax_ap *radio = context;
	wiphy_work_queue(radio->hw->wiphy, &radio->offload_work);
}

static void rtl_ax_sta_set_4addr(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta, bool enabled)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	if (vif->type != NL80211_IFTYPE_STATION) return;
	radio->sta_4addr = enabled;
	if (enabled) {
		radio->peer_authorized[RTL_AX_CLIENT_MACID] = false;
		rtl_ax_tx_peer_authorized(radio->tx, RTL_AX_CLIENT_MACID, false);
		radio->offload_result = rtl_ax_tx_ni_retire_peer(radio->tx, RTL_AX_CLIENT_MACID);
		ieee80211_connection_loss(vif);
	}
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
	unsigned long flags;

	spin_lock_irqsave(&radio->command_lock, flags);
	rtl_ax_update32(&radio->io, 0xc400, BIT(12), 0);
	radio->beacon_enabled = false;
	WRITE_ONCE(radio->mesh_started, false);
	WRITE_ONCE(radio->ap_started, false);
	if (radio->dfs.enabled) {
		radio->dfs.in_cac = true;
		rtl_ax_tx_dfs_block(radio->tx, true);
	}
	spin_unlock_irqrestore(&radio->command_lock, flags);
	wiphy_work_cancel(hw->wiphy, &radio->offload_work);
	wiphy_work_cancel(hw->wiphy, &radio->beacon_work);
	wiphy_delayed_work_cancel(hw->wiphy, &radio->mesh_sync_work);
	wiphy_work_cancel(hw->wiphy, &radio->ra_update_work);
	rtl_ax_receive_mode(&radio->io, 0);
}

static void rtl_ax_monitor_remove_interface(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	if (radio->sta_vif == vif) {
		rtl_ax_client_release(radio);
		radio->sta_vif = NULL;
	}

	if (radio->ap_vif == vif) {
		if (vif->type == NL80211_IFTYPE_MESH_POINT && radio->ap_started)
			rtl_ax_ap_stop(hw, vif, &vif->bss_conf);
		radio->ap_vif = NULL;
	}
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

	if (vif->type == NL80211_IFTYPE_STATION) {
		rtl_ax_client_cfg_changed(hw, vif, changed);
		if (radio->retired_stations[RTL_AX_CLIENT_MACID].pending) return;
		if (changed & BSS_CHANGED_HE_BSS_COLOR)
			rtl_ax_port0_color(&radio->io, conf->he_bss_color.enabled ? conf->he_bss_color.color : 0);
	} else if (vif->type != NL80211_IFTYPE_AP && vif->type != NL80211_IFTYPE_MESH_POINT) return;
	if (changed & BSS_CHANGED_ERP_SLOT) {
		mutex_lock(&radio->command_mutex);
		for (ac = 0; ac < IEEE80211_NUM_ACS; ac++)
			if (radio->edca_valid & BIT(ac))
				rtl_ax_edca_apply(radio, vif, ac);
		mutex_unlock(&radio->command_mutex);
	}
	/* A scan/ROC only pauses beaconing. Leaving mesh releases its channel. */
	if (vif->type == NL80211_IFTYPE_MESH_POINT &&
	    (changed & BSS_CHANGED_IDLE) && vif->cfg.idle) {
		rtl_ax_ap_stop(hw, vif, conf);
		return;
	}
	/* mac80211 mesh starts through BSS changes, not start_ap. */
	if (vif->type == NL80211_IFTYPE_MESH_POINT &&
	    (changed & BSS_CHANGED_BEACON_ENABLED)) {
		if (conf->enable_beacon &&
		    (!radio->ap_started || (changed & BSS_CHANGED_BEACON_INT))) {
			radio->ap_result = rtl_ax_ap_start(hw, vif, conf);
			if (radio->ap_result) {
				dev_err(&radio->pdev->dev, "mesh start failed: %d\n", radio->ap_result);
				cfg80211_stop_iface(hw->wiphy, ieee80211_vif_to_wdev(vif), GFP_KERNEL);
			}
			return;
		}
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
		unsigned long flags;
		bool enable;

		spin_lock_irqsave(&radio->command_lock, flags);
		enable = conf->enable_beacon && !radio->dfs.in_cac && !radio->dfs.detected && !radio->dfs.error;
		rtl_ax_update32(&radio->io, 0xc400, BIT(12), enable ? BIT(12) : 0);
		radio->beacon_enabled = enable;
		spin_unlock_irqrestore(&radio->command_lock, flags);
		if (vif->type == NL80211_IFTYPE_MESH_POINT) {
			if (enable)
				wiphy_delayed_work_queue(hw->wiphy, &radio->mesh_sync_work,
					usecs_to_jiffies(conf->beacon_int * 1024));
			else wiphy_delayed_work_cancel(hw->wiphy, &radio->mesh_sync_work);
		}
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
	/* PKT_DROP has no ACK. Keep ownership until RPQ, but let the delayed
	 * cleanup retry instead of sleeping for every STA under the wiphy lock. */
	return rtl_ax_tx_station_pending(radio->tx, macid, tid) ? -EAGAIN : 0;
}

static int rtl_ax_rx_ba_remove(struct rtl_ax_ap *radio, unsigned int index)
{
	u8 ba[8];
	int ret;

	radio->rx_ba[index].deleting = true;
	ret = rtl_ax_tx_rx_retire(radio->tx, radio->rx_ba[index].macid);
	if (ret) {
		radio->rx_ba_result = ret;
		return ret;
	}
	if (radio->rx_ba[index].cam < 8) {
		rtl_ax_store32(ba, 2 | radio->rx_ba[index].macid << 8);
		rtl_ax_store32(ba + 4, (radio->rx_ba[index].cam + 8U) << 28);
		ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 12, 0, ba, sizeof(ba), true);
	}
	radio->rx_ba_result = ret;
	if (!ret) {
		rtl_ax_rx_ba_window(radio->rx, radio->rx_ba[index].macid,
			radio->rx_ba[index].tid, 0);
		radio->rx_ba[index].valid = false;
		radio->rx_ba[index].deleting = false;
		radio->rx_ba_stopped++;
		rtl_ax_offload_ready(radio);
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
	bool unlink = false;
	int ret;

	ret = rtl_ax_tx_ni_retire_peer(radio->tx, macid);
	if (ret) return ret;
	if (radio->retired_stations[macid].client) rtl_ax_rx_key(radio->rx, macid, NULL);
	ret = rtl_ax_drop_station(radio, macid, IEEE80211_NUM_TIDS);
	if (ret)
		return ret;
	/* Keep the reservation until all traffic is dropped and CCTL acknowledges
	 * disable. A failed command can be retried without reusing the slot. */
	if (test_bit(macid, radio->preload_peers)) {
		rtl_ax_station_preload(dctl, macid, false);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 2, dctl, sizeof(dctl), true);
		if (ret) return ret;
		rtl_ax_tx_peer_preload(radio->tx, macid, false);
		__clear_bit(macid, radio->preload_peers);
	}
	for (i = 0; i < RTL_AX_SECURITY_ENTRIES; i++)
		if (radio->security_unlink[i] == macid + 1)
			unlink = true;
	if (keys->valid || unlink) {
		rtl_ax_security_dctl(dctl, macid, &empty);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 9, dctl, sizeof(dctl), true);
		if (ret)
			return ret;
		/* A later cleanup must not rewrite a released or reused MACID. */
		for (i = 0; i < RTL_AX_SECURITY_ENTRIES; i++)
			if (radio->security_unlink[i] == macid + 1)
				radio->security_unlink[i] = 0;
		for (i = 0; i < ARRAY_SIZE(keys->cam); i++)
			if (keys->valid & BIT(i)) {
				rtl_ax_security_cam(security, keys->cam[i], NULL);
				ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 10, 1, security, sizeof(security), true);
				if (ret)
					return ret;
				__clear_bit(keys->cam[i], radio->security_ids);
				radio->security_keys[keys->cam[i]] = NULL;
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
	rtl_ax_store32(command, macid | (radio->retired_stations[macid].client ? 0x2400 :
		radio->retired_stations[macid].mesh ? 0xc600 : 0x4600));
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 4, command, sizeof(command), true);
	if (ret)
		return ret;
	if (radio->retired_stations[macid].client)
		rtl_ax_client_cam(cam, macid, radio->retired_stations[macid].self,
			radio->retired_stations[macid].peer, radio->retired_stations[macid].aid, false, false);
	else rtl_ax_peer_cam(cam, macid, radio->retired_stations[macid].self,
		radio->retired_stations[macid].peer, radio->retired_stations[macid].aid, false);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (!ret) {
		radio->ra_registered[macid] = false;
		if (radio->traffic_peer == macid) radio->traffic_peer = 0;
		clear_bit(macid, radio->ra_pending);
		rtl_ax_tx_peer_qos(radio->tx, macid, false, 0);
		rtl_ax_tx_peer_client(radio->tx, macid, false, NULL);
		__clear_bit(macid, radio->station_ids);
		radio->retired_stations[macid].pending = false;
		if (radio->retired_stations[macid].client) {
			radio->sta_role = radio->sta_associated = false;
			rtl_ax_port0_field(&radio->io, 0xc400, 0x13c1c, 0);
		}
		radio->retired_stations[macid].client = false;
		radio->station_removed++;
	}
	return ret;
}

static void rtl_ax_client_release(struct rtl_ax_ap *radio)
{
	unsigned int id = RTL_AX_CLIENT_MACID;

	if (!radio->sta_role) return;
	radio->sta_associated = false;
	radio->peer_authorized[id] = false;
	rtl_ax_tx_peer_authorized(radio->tx, id, false);
	radio->tx_ba[id].stopping = radio->tx_ba[id].flushing = 0;
	radio->retired_stations[id].pending = true;
	if (rtl_ax_station_release(radio, id))
		wiphy_delayed_work_queue(radio->hw->wiphy, &radio->cleanup_work, HZ);
}

static int rtl_ax_tx_ba_finish(struct rtl_ax_ap *radio, unsigned int macid, unsigned int tid)
{
	u8 cmac[68];
	unsigned int i, aggregate = 0;
	bool flush = radio->tx_ba[macid].flushing & BIT(tid);
	int ret;

	ret = rtl_ax_tx_ni_retire_ba(radio->tx, macid);
	if (ret) return ret;
	ret = flush ? rtl_ax_drop_station(radio, macid, tid) :
		(rtl_ax_tx_station_pending(radio->tx, macid, tid) ? -EAGAIN : 0);
	if (ret)
		return ret;
	for (i = 0; i < IEEE80211_NUM_TIDS; i++)
		aggregate = max_t(unsigned int, aggregate, radio->tx_ba[macid].aggregation[i]);
	rtl_ax_station_cmac_connected(cmac, macid, radio->chip == RTL_AX_8852C, aggregate);
	if (radio->sta_vif) rtl_ax_station_cmac_client(cmac);
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
		ieee80211_stop_tx_ba_cb_irqsafe(radio->sta_vif ? radio->sta_vif : radio->ap_vif, radio->tx_ba[macid].address, tid);
	rtl_ax_offload_ready(radio);
	return 0;
}

static bool rtl_ax_security_cleanup(struct rtl_ax_ap *radio)
{
	unsigned int index;
	bool retry = false;
	u8 cam[24], dctl[68];

	for_each_set_bit(index, radio->security_deleting, RTL_AX_SECURITY_ENTRIES) {
		if (radio->security_unlink[index]) {
			unsigned int macid = radio->security_unlink[index] - 1;

			if ((macid && rtl_ax_tx_ni_retire_peer(radio->tx, macid)) ||
			    rtl_ax_tx_key_pending(radio->tx, index)) {
				retry = true;
				continue;
			}
			/* Use the current desired role, including keys installed since the failure. */
			rtl_ax_security_dctl(dctl, macid, &radio->security_roles[macid]);
			if (rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 9, dctl, sizeof(dctl), true)) {
				retry = true;
				continue;
			}
			radio->security_unlink[index] = 0;
			if (macid && (radio->security_roles[macid].valid & 3))
				rtl_ax_offload_ready(radio);
		}
		rtl_ax_security_cam(cam, index, NULL);
		if (rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 10, 1, cam, sizeof(cam), true)) {
			retry = true;
			continue;
		}
		__clear_bit(index, radio->security_deleting);
		__clear_bit(index, radio->security_ids);
		if (test_bit(index, radio->security_aborting)) {
			__clear_bit(index, radio->security_aborting);
			radio->keys_aborted++;
		} else {
			radio->keys_removed++;
		}
	}
	return retry;
}

static void rtl_ax_cleanup_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, cleanup_work.work);
	unsigned int i, tid;
	bool retry = rtl_ax_tx_ni_cleanup(radio->tx) != 0;

	retry |= rtl_ax_security_cleanup(radio);

	if (radio->offload_retry) {
		radio->offload_retry = false;
		wiphy_work_queue(wiphy, &radio->offload_work);
	}
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
	radio->retired_stations[macid].aid = vif->type == NL80211_IFTYPE_STATION ? vif->cfg.aid : sta->aid;
	radio->retired_stations[macid].client = vif->type == NL80211_IFTYPE_STATION;
	radio->peer_authorized[macid] = false;
	rtl_ax_tx_peer_authorized(radio->tx, macid, false);
	radio->retired_stations[macid].pending = true;
	radio->retired_stations[macid].mesh = vif->type == NL80211_IFTYPE_MESH_POINT;
	radio->tx_ba[macid].stopping = radio->tx_ba[macid].flushing = 0;
	station->macid = 0;
	ret = rtl_ax_station_release(radio, macid);
	if (ret) {
		dev_err(&radio->pdev->dev, "MACID%u release pending: %d\n", macid, ret);
		wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
	}
	return 0;
}

static bool rtl_ax_ra_peer(struct rtl_ax_ap *radio, struct ieee80211_sta *sta,
	unsigned int id, u8 command[16], u32 *rate, u32 *caps, bool update)
{
	struct ieee80211_link_sta *link = &sta->deflink;
	bool band5g = radio->chip == RTL_AX_8852C;
	u8 bandwidth = min_t(unsigned int, radio->channel.bandwidth, link->bandwidth);
	u8 nss = min_t(unsigned int, link->rx_nss, 2);
	u16 ht = link->ht_cap.mcs.rx_mask[0] | link->ht_cap.mcs.rx_mask[1] << 8;
	bool sgi = bandwidth == 3 ? link->vht_cap.cap & IEEE80211_VHT_CAP_SHORT_GI_160 :
		bandwidth == 2 ? link->vht_cap.cap & IEEE80211_VHT_CAP_SHORT_GI_80 :
		link->ht_cap.cap & (bandwidth ? IEEE80211_HT_CAP_SGI_40 : IEEE80211_HT_CAP_SGI_20);

	if (link->smps_mode == IEEE80211_SMPS_STATIC) nss = 1;
	if (nss == 1) ht &= 255;
	/* Stock txsc_amsdu_sta_init permits 10896 bytes for an HE 11K peer. */
	*caps = BIT(17) | min_t(unsigned int, sta->cur->max_amsdu_len,
		link->he_cap.has_he && sta->cur->max_amsdu_len >= 11454 ? 10896 :
		sta->cur->max_amsdu_len >= 7935 ? 7433 : 3793) << 18;
	if (link->he_cap.has_he) {
		const struct ieee80211_sta_he_cap *he = &link->he_cap;
		rtl_ax_ra_he(command, id, link->supp_rates[radio->band.band] << (band5g ? 4 : 0),
			le16_to_cpu(bandwidth == 3 ? he->he_mcs_nss_supp.rx_mcs_160 :
			he->he_mcs_nss_supp.rx_mcs_80), he->he_cap_elem.phy_cap_info[1],
			he->he_cap_elem.phy_cap_info[7], band5g, bandwidth);
		*rate = 0x180 | (he->he_cap_elem.phy_cap_info[1] &
			IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD ? BIT(21) : 0);
	} else if (band5g && link->vht_cap.vht_supported) {
		rtl_ax_ra_vht80(command, id, link->supp_rates[NL80211_BAND_5GHZ] << 4,
			le16_to_cpu(link->vht_cap.vht_mcs.rx_mcs_map), sgi);
		*rate = 0x100;
		*caps |= ht | (sgi ? BIT(16) : 0);
	} else if (link->ht_cap.ht_supported) {
		rtl_ax_ra_ht20(command, id, link->supp_rates[radio->band.band] << (band5g ? 4 : 0),
			ht, sgi, band5g);
		*rate = 0x80;
		*caps |= ht | (sgi ? BIT(16) : 0);
	} else {
		u16 legacy = link->supp_rates[radio->band.band] << (band5g ? 4 : 0);

		if (!legacy) return false;
		rtl_ax_ra_legacy(command, id, legacy, band5g);
		*rate = __ffs(legacy);
		*caps = BIT(17);
		bandwidth = 0;
		nss = 1;
	}
	rtl_ax_ra_limit(command, bandwidth, nss, update);
	rtl_ax_ra_environment(command, radio->environment.nhm_noise_ratio);
	*rate |= bandwidth << 16;
	/* A dynamic-SMPS receiver wakes its other chains after RTS/CTS. */
	if (link->smps_mode == IEEE80211_SMPS_DYNAMIC) *rate |= BIT(22);
	return true;
}

static int rtl_ax_station_add(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct rtl_ax_station *station = (void *)sta->drv_priv;
	u8 unpause[32], command[4], cam[60];
	bool client = vif->type == NL80211_IFTYPE_STATION;
	unsigned int id = client ? RTL_AX_CLIENT_MACID : find_next_zero_bit(radio->station_ids, 128, 1);
	int ret;

	if (id == 128)
		return -ENOSPC;
	if (client) {
		ret = rtl_ax_client_prepare(radio, vif);
		if (ret) return ret;
		goto connected;
	}
	rtl_ax_role_unpause(unpause, id);
	ret = rtl_ax_command_flags(radio, RTL_AX_H2C_CMD, 1, 9, 8, unpause, sizeof(unpause), true, false);
	if (ret)
		return ret;
	rtl_ax_store32(command, id | (vif->type == NL80211_IFTYPE_MESH_POINT ? 0xc200 : 0x4200));
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 4, command, sizeof(command), true);
	if (ret)
		return ret;
connected:
	station->macid = id;
	station->mesh = vif->type == NL80211_IFTYPE_MESH_POINT;
	__set_bit(id, radio->station_ids);
	memset(&radio->tx_ba[id], 0, sizeof(radio->tx_ba[id]));
	ether_addr_copy(radio->tx_ba[id].address, sta->addr);
	if (client) rtl_ax_client_cam(cam, id, vif->addr, sta->addr, vif->cfg.aid, true, true);
	else rtl_ax_peer_cam(cam, id, vif->addr, sta->addr, 0, true);
	if (sta->deflink.he_cap.has_he) rtl_ax_store32(cam + 32, id | BIT(14));
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (ret)
		goto remove;
	ret = rtl_ax_station_tables(radio, id);
	if (ret)
		goto remove;
	ret = rtl_ax_station_connected_table(radio, id, sta);
	if (ret)
		goto remove;
	rtl_ax_station_join(command, id, min_t(unsigned int, radio->channel.bandwidth,
		sta->deflink.bandwidth), &sta->deflink.he_cap,
		min_t(unsigned int, sta->deflink.smps_mode == IEEE80211_SMPS_STATIC ? 1 : sta->deflink.rx_nss, 2),
		vif->type == NL80211_IFTYPE_MESH_POINT, client);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 0, command, sizeof(command), true);
	if (ret)
		goto remove;
	if (client) radio->sta_join = rtl_ax_ap_read32(command);
	if (client) rtl_ax_client_cam(cam, id, vif->addr, sta->addr, vif->cfg.aid, true, true);
	else rtl_ax_peer_cam(cam, id, vif->addr, sta->addr, sta->aid, true);
	if (sta->deflink.he_cap.has_he) rtl_ax_store32(cam + 32, id | BIT(14));
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (ret)
		goto remove;
	rtl_ax_tx_peer_qos(radio->tx, id, sta->wme,
		DIV_ROUND_UP(ieee80211_sta_ps_buffer_timeout(sta), HZ));
	rtl_ax_tx_peer_client(radio->tx, id, client, sta->addr);
	rtl_ax_rx_uapsd(radio->rx, id, client ? 0 : sta->uapsd_queues);
	{
		u32 rate, caps;
		if (rtl_ax_ra_peer(radio, sta, id, radio->ra_config[id], &rate, &caps, false)) {
			ret = rtl_ax_command_flags(radio, RTL_AX_H2C_LONG_DATA, 2, 1, 0,
				radio->ra_config[id], 16, false, false);
			if (ret) goto remove;
			rtl_ax_ra_rssi_init(&radio->ra_rssi_state[id]);
			memset(radio->ra_rssi[id], 0, sizeof(radio->ra_rssi[id]));
			radio->ra_registered[id] = true;
			radio->ra_rssi_samples[id] = radio->ra_reports[id] = radio->ra_updates[id] = 0;
			radio->ra_rssi_level[id] = 0;
			radio->ra_update_error[id] = 0;
			clear_bit(id, radio->ra_pending);
			struct rtl_ax_traffic_peer *traffic = &radio->traffic[id];
			memset(traffic, 0, sizeof(*traffic));
			traffic->band5g = radio->chip == RTL_AX_8852C;
			traffic->he = sta->deflink.he_cap.has_he;
			traffic->vht = sta->deflink.vht_cap.vht_supported;
			traffic->vendor = station->vendor;
			traffic->bandwidth = min_t(u8, radio->channel.bandwidth, sta->deflink.bandwidth);
			traffic->nss = sta->deflink.rx_nss;
			traffic->max_bytes = caps >> 18;
			traffic->max_num = traffic->max_bytes > 7433 ? 7 : traffic->max_bytes > 3793 ? 4 : 2;
			traffic->amsdu_num = traffic->max_num;
			traffic->amsdu_bytes = traffic->max_bytes;
			rtl_ax_tx_peer_counters(radio->tx, id, &traffic->tx_seen, &traffic->packets_seen);
			traffic->rx_seen = rtl_ax_rx_peer_bytes(radio->rx, id);
			rtl_ax_tx_peer_amsdu(radio->tx, id, rtl_ax_amsdu_queue_policy(
				traffic->amsdu_bytes, traffic->amsdu_num, traffic->bandwidth, traffic->band5g,
				traffic->tx_kbits, traffic->rx_kbits));
			memset(radio->ra_feedback[id], 0, 8);
			rtl_ax_tx_peer_rate(radio->tx, id, rate, caps);
			/* Firmware is the only rate controller; do not retain Minstrel's
			 * initial HT-BA ceiling for a negotiated VHT/HE peer. */
			sta->deflink.agg.max_rc_amsdu_len = !sta->deflink.he_cap.has_he &&
				!sta->deflink.vht_cap.vht_supported ? min(caps >> 18, 4065U) : caps >> 18;
		} else { ret = -EOPNOTSUPP; goto remove; }
	}
	/* Reserve before issuing a fallible command: completion can time out
	 * after the hardware accepted it. Removal owns the reservation then. */
	if (vif->type == NL80211_IFTYPE_AP && radio->chip == RTL_AX_8852C &&
	    bitmap_weight(radio->preload_peers, 128) < 4) {
		u8 preload[68];

		__set_bit(id, radio->preload_peers);
		rtl_ax_station_preload(preload, id, true);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 2, preload, sizeof(preload), true);
		if (ret) goto remove;
		rtl_ax_tx_peer_preload(radio->tx, id, true);
	}
	radio->station_added++;
	dev_info(&radio->pdev->dev, "native %s peer MACID%u AID%u connected\n",
		client ? "STA" : "AP", id, client ? vif->cfg.aid : sta->aid);
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
	if (vif->type == NL80211_IFTYPE_STATION &&
	    ((old == IEEE80211_STA_NOTEXIST && new == IEEE80211_STA_NONE) ||
	     (old == IEEE80211_STA_NONE && new == IEEE80211_STA_AUTH))) {
		int ret;

		if (radio->sta_4addr) return -EOPNOTSUPP;
		ret = rtl_ax_client_prepare(radio, vif);
		if (!ret) {
			((struct rtl_ax_station *)sta->drv_priv)->macid = RTL_AX_CLIENT_MACID;
			ether_addr_copy(radio->sta_bssid, sta->addr);
			ether_addr_copy(radio->retired_stations[RTL_AX_CLIENT_MACID].peer, sta->addr);
			ret = rtl_ax_client_update(radio, vif, false);
		}
		return ret;
	}
	if (old == IEEE80211_STA_AUTH && new == IEEE80211_STA_ASSOC)
		return rtl_ax_station_add(hw, vif, sta);
	if (old == IEEE80211_STA_ASSOC && new == IEEE80211_STA_AUTH)
		return rtl_ax_station_remove(hw, vif, sta);
	if (vif->type == NL80211_IFTYPE_STATION && new == IEEE80211_STA_NOTEXIST) {
		rtl_ax_client_release(radio);
		return 0;
	}
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
		aggregate = min_t(unsigned int, params->buf_size, hw->max_tx_aggregation_subframes);
		/* Original C/XB hal_trx_init sets global count128 and time160;
		 * each peer's negotiated window belongs in CMAC/WD, not here. */
		rtl_ax_update8(&radio->io, 0xc604, 7, 0);
		rtl_ax_update32(&radio->io, 0xc610, 0xff0000ff,
			0xa0000000 | (hw->max_tx_aggregation_subframes - 1));
		rtl_ax_station_cmac_connected(cmac, station->macid, radio->chip == RTL_AX_8852C, aggregate);
		if (radio->sta_vif) rtl_ax_station_cmac_client(cmac);
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
		WRITE_ONCE(radio->tx_ba[station->macid].aggregation[params->tid], 0);
		rtl_ax_tx_peer_ba(radio->tx, station->macid, params->tid, 0);
		radio->tx_ba[station->macid].stopping |= BIT(params->tid);
		if (params->action != IEEE80211_AMPDU_TX_STOP_CONT)
			radio->tx_ba[station->macid].flushing |= BIT(params->tid);
		ret = rtl_ax_tx_ba_finish(radio, station->macid, params->tid);
		if (ret)
			wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
		return 0;
	case IEEE80211_AMPDU_RX_START: {
		unsigned int j, cam, used;

		ret = rtl_ax_tx_rx_retire(radio->tx, station->macid);
		if (ret) return ret;
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
		if (radio->rx_ba[i].valid) {
			cam = radio->rx_ba[i].cam;
		} else {
			used = 0;
			for (j = 0; j < ARRAY_SIZE(radio->rx_ba); j++)
				if (radio->rx_ba[j].valid && radio->rx_ba[j].cam < 8)
					used |= BIT(radio->rx_ba[j].cam);
			for (cam = 0; cam < 8; cam++)
				if (!(used & BIT(cam))) break;
			/* Hardware learns nonzero TIDs dynamically. Keep TID0 in a
			 * static entry when the shared CAM is full, as rtw89 does. */
			if (cam == 8 && !params->tid) {
				for (j = 0; j < ARRAY_SIZE(radio->rx_ba); j++)
					if (radio->rx_ba[j].valid && !radio->rx_ba[j].deleting &&
					    radio->rx_ba[j].tid && radio->rx_ba[j].cam < 8) break;
				if (j == ARRAY_SIZE(radio->rx_ba)) return -ENOSPC;
				ret = rtl_ax_tx_rx_retire(radio->tx, radio->rx_ba[j].macid);
				if (ret) return ret;
				cam = radio->rx_ba[j].cam;
				/* Keep the old software window; only its static CAM moves.
				 * A failed replacement is deleted by the existing retry. */
				radio->rx_ba[j].cam = 8;
			}
		}
		/* Actual C/XB MAC ops+496 reports extended standard entries8..15.
		 * ops+504 selects mac_bacam_info@23ca2c: index belongs to word1,
		 * standard-enable bit8, band0; own Linux allocation retains that ABI. */
		radio->rx_ba[i].macid = station->macid;
		radio->rx_ba[i].tid = params->tid;
		radio->rx_ba[i].cam = cam;
		radio->rx_ba[i].valid = true;
		radio->rx_ba[i].deleting = true;
		ret = 0;
		if (cam < 8) {
			rtl_ax_store32(ba, 3 | (params->tid & 15) << 4 | station->macid << 8 |
				(params->buf_size > 64 ? 4U << 16 : 0) | (params->ssn & 0xfff) << 20);
			rtl_ax_store32(ba + 4, (cam + 8) << 28 | BIT(8));
			ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 12, 0, ba, sizeof(ba), true);
		}
		radio->rx_ba_result = ret;
		if (ret) {
			rtl_ax_offload_ready(radio);
			wiphy_delayed_work_queue(hw->wiphy, &radio->cleanup_work, HZ);
			return ret;
		}
		radio->rx_ba[i].deleting = false;
		radio->rx_ba_started++;
		rtl_ax_rx_ba_window(radio->rx, station->macid, params->tid, params->buf_size);
		rtl_ax_offload_ready(radio);
		dev_info(&radio->pdev->dev, "native RX BA MACID%u TID%u CAM%d SSN%u size%u ready\n",
			station->macid, params->tid, cam < 8 ? (int)cam + 8 : -1,
			params->ssn, params->buf_size);
		return 0;
	}
	case IEEE80211_AMPDU_RX_STOP:
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
		dev_info(&radio->pdev->dev, "native RX BA MACID%u TID%u CAM%d removed\n",
			station->macid, params->tid,
			radio->rx_ba[i].cam < 8 ? (int)radio->rx_ba[i].cam + 8 : -1);
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
	rtl_ax_receive_mode(&radio->io, radio->scanning ? 0 : radio->ap_started ? 4 : radio->sta_vif ? 2 : 0);
	mutex_unlock(&radio->command_mutex);
}

static void rtl_ax_sw_scan_start(struct ieee80211_hw *hw,
	struct ieee80211_vif *vif, const u8 *mac_addr)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	/* NI bypasses mac80211's off-channel queue stop. Drain it before RF moves. */
	radio->scanning = true;
	radio->scan_offload_result = radio->sta_associated ?
		rtl_ax_tx_ni_retire_peer(radio->tx, RTL_AX_CLIENT_MACID) : 0;
	mutex_lock(&radio->command_mutex);
	/* Factory CRC-OK reports already include foreign beacons. */
	mutex_unlock(&radio->command_mutex);
}

static void rtl_ax_sw_scan_complete(struct ieee80211_hw *hw,
	struct ieee80211_vif *vif)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret = 0;

	mutex_lock(&radio->command_mutex);
	radio->scanning = false;
	/* mac80211 restores hw.conf before this callback but ignores a failed
	 * channel transaction. Retry that restore before admitting NI traffic. */
	if (radio->sta_associated && !radio->scan_offload_result && radio->dfs.error)
		ret = rtl_ax_chandef_locked(radio, &hw->conf.chandef);
	rtl_ax_receive_mode(&radio->io, radio->ap_started ? 4 : radio->sta_vif ? 2 : 0);
	mutex_unlock(&radio->command_mutex);
	if (ret) {
		radio->sta_associated = false;
		ieee80211_connection_loss(radio->sta_vif);
		return;
	}
	if (radio->sta_associated) rtl_ax_offload_ready(radio);
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
	struct ieee80211_tx_info *info = IEEE80211_SKB_CB(skb);
	bool injected_rate = info->control.flags & IEEE80211_TX_CTRL_RATE_INJECT;

	/* mac80211 leaves rates unset with HAS_RATE_CONTROL. Management, group
	 * and pre-authorization traffic still need a valid basic fallback. */
	if (!injected_rate)
		rate = rtl_ax_tx_basic_rate(radio, control->sta, info);

	if (!macid && radio->sta_role) macid = RTL_AX_CLIENT_MACID;
	if (skb->len >= (encap ? ETH_HLEN : ieee80211_is_back_req(header->frame_control) ? sizeof(struct ieee80211_bar) : 24) && skb->len <= 0x3fff &&
	    (encap || ieee80211_is_mgmt(header->frame_control) || ieee80211_is_data(header->frame_control) ||
	     ieee80211_is_back_req(header->frame_control))) {
		/* Original RTW_DATA_RATE_MCS0=0x80, GI0=long/GI1=short.
		 * mac80211's MCS index must not index the legacy bitrate array. */
		if (injected_rate && selected->flags & IEEE80211_TX_RC_VHT_MCS) {
			rate = 0x100 + (ieee80211_rate_get_vht_nss(selected) - 1) * 16 +
				ieee80211_rate_get_vht_mcs(selected);
			gi = !!(selected->flags & IEEE80211_TX_RC_SHORT_GI);
			bandwidth = selected->flags & IEEE80211_TX_RC_160_MHZ_WIDTH ? 3 :
				selected->flags & IEEE80211_TX_RC_80_MHZ_WIDTH ? 2 :
				!!(selected->flags & IEEE80211_TX_RC_40_MHZ_WIDTH);
		} else if (injected_rate && selected->flags & IEEE80211_TX_RC_MCS) {
			rate = 0x80 + selected->idx;
			gi = !!(selected->flags & IEEE80211_TX_RC_SHORT_GI);
			bandwidth = !!(selected->flags & IEEE80211_TX_RC_40_MHZ_WIDTH);
		} else if (injected_rate && selected->idx >= 0 && selected->idx < radio->band.n_bitrates)
			rate = radio->band.bitrates[selected->idx].hw_value;
		if (control->sta && (encap || ieee80211_is_data_qos(header->frame_control)) &&
		    (IEEE80211_SKB_CB(skb)->flags & IEEE80211_TX_CTL_AMPDU)) {
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
			struct rtl_ax_station *station = queue->sta ? (void *)queue->sta->drv_priv : NULL;

			/* Hardware RC replaces Minstrel's BA trigger, not mac80211's
			 * negotiated session lifecycle. Retry rejection at most once/sec. */
			if (station && station->macid && queue->tid < 8 && ac != IEEE80211_AC_VO &&
			    queue->sta->wme && READ_ONCE(radio->peer_authorized[station->macid]) &&
			    (queue->sta->deflink.ht_cap.ht_supported || queue->sta->deflink.vht_cap.vht_supported ||
			     queue->sta->deflink.he_cap.has_he) &&
			    !READ_ONCE(radio->tx_ba[station->macid].aggregation[queue->tid]) &&
			    !(READ_ONCE(radio->tx_ba[station->macid].stopping) & BIT(queue->tid)) &&
			    (!station->ba_retry[queue->tid] || time_after_eq(jiffies, station->ba_retry[queue->tid]))) {
				station->ba_retry[queue->tid] = jiffies + HZ;
				ieee80211_start_tx_ba_session(queue->sta, queue->tid, 0);
			}

			while ((skb = ieee80211_tx_dequeue(hw, queue)))
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
	if (vif->type != NL80211_IFTYPE_AP && vif->type != NL80211_IFTYPE_STATION) return -EOPNOTSUPP;
	return rtl9617c_wifi_setup_tc(netdev, type, data);
}

static int rtl_ax_set_key(struct ieee80211_hw *hw, enum set_key_cmd cmd,
	struct ieee80211_vif *vif, struct ieee80211_sta *sta,
	struct ieee80211_key_conf *key)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned int macid = sta ? ((struct rtl_ax_station *)sta->drv_priv)->macid :
		vif->type == NL80211_IFTYPE_STATION ? RTL_AX_CLIENT_MACID : 0;
	unsigned int slot = key->flags & IEEE80211_KEY_FLAG_PAIRWISE ? key->keyidx : 2 + key->keyidx;
	struct rtl_ax_security_role role = radio->security_roles[macid];
	u8 cam[24], dctl[68];
	unsigned int index;
	int ret;

	/* Mesh GTKs are per transmitter; mac80211 owns mesh encryption and PN. */
	if (vif->type == NL80211_IFTYPE_MESH_POINT)
		return -EOPNOTSUPP;
	if (key->cipher != WLAN_CIPHER_SUITE_CCMP)
		return -EOPNOTSUPP;
	if (cmd == DISABLE_KEY) {
		index = key->hw_key_idx;
		/* STA teardown may precede the interface GTK callback. An old key
		 * must not unlink a CAM that a subsequent association has reused. */
		if (index >= RTL_AX_SECURITY_ENTRIES || radio->security_keys[index] != key)
			return 0;
		radio->security_keys[index] = NULL;
		role.valid &= ~BIT(slot);
		role.keyid[slot] = 0;
		role.cam[slot] = 0;
		radio->security_roles[macid] = role;
		__set_bit(index, radio->security_deleting);
		radio->security_unlink[index] = macid + 1;
	}
	if (key->flags & IEEE80211_KEY_FLAG_PAIRWISE) {
		ret = rtl_ax_tx_ni_retire_peer(radio->tx, macid);
		if (cmd == DISABLE_KEY) rtl_ax_rx_key(radio->rx, macid, NULL);
		if (ret) {
			if (cmd == SET_KEY) return ret;
			goto done;
		}
	}
	if (cmd == SET_KEY) {
		index = find_first_zero_bit(radio->security_ids, RTL_AX_SECURITY_ENTRIES);
		if (index == RTL_AX_SECURITY_ENTRIES)
			return -ENOSPC;
		/* A timeout can follow a completed CAM write. Hold ownership until cleanup. */
		__set_bit(index, radio->security_ids);
		__set_bit(index, radio->security_deleting);
		__set_bit(index, radio->security_aborting);
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
		ret = rtl_ax_tx_flush_key(radio->tx, index);
		if (ret)
			goto done;
	}
	rtl_ax_security_dctl(dctl, macid, &role);
	if (cmd == SET_KEY && (key->flags & IEEE80211_KEY_FLAG_PAIRWISE)) {
		u64 pn = atomic64_read(&key->tx_pn);
		rtl_ax_store32(dctl + 4, (u32)(pn & 0xffff) << 16);
		rtl_ax_store32(dctl + 8, pn >> 16);
		rtl_ax_store32(dctl + 36, 0xffff0000);
		rtl_ax_store32(dctl + 40, 0xffffffff);
	}
	if (cmd == SET_KEY)
		radio->security_unlink[index] = macid + 1;
	ret = rtl_ax_command_flags(radio, RTL_AX_H2C_DATA, 1, 5, 9,
		dctl, sizeof(dctl), false, true);
	if (ret)
		goto done;
	radio->security_roles[macid] = role;
	if (cmd == SET_KEY) {
		__clear_bit(index, radio->security_deleting);
		__clear_bit(index, radio->security_aborting);
		radio->security_unlink[index] = 0;
		key->hw_key_idx = index;
		radio->security_keys[index] = key;
		key->flags |= IEEE80211_KEY_FLAG_SW_MGMT_TX;
		if (key->flags & IEEE80211_KEY_FLAG_PAIRWISE) rtl_ax_rx_key(radio->rx, macid, key);
		radio->keys_installed++;
	} else {
		radio->security_unlink[index] = 0;
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
	if (vif->type != NL80211_IFTYPE_AP && vif->type != NL80211_IFTYPE_STATION) return -EOPNOTSUPP;
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
	u16 tids, int num_frames, enum ieee80211_frame_release_type reason, bool more_data, u32 generation)
{
	struct rtl_ax_station *station = (void *)sta->drv_priv;

	rtl_ax_tx_ps_release(rtl_ax_monitor_radio(hw)->tx, sta, station->macid, tids,
		reason == IEEE80211_FRAME_RELEASE_UAPSD, more_data, generation);
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

static void rtl_ax_ra_update_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, ra_update_work);
	unsigned int id;
	if (!radio->ap_started && !radio->sta_associated) return;
	for (id = 1; id < 128; id++) {
		struct ieee80211_sta *sta;
		u8 command[16], cmac[68] = {}, join[4];
		u32 rate, caps;
		bool configured, he;
		int ret = 0;
		unsigned long flags;

		if (!test_and_clear_bit(id, radio->ra_pending) || !radio->ra_registered[id]) continue;
		rcu_read_lock();
		sta = ieee80211_find_sta_by_ifaddr(radio->hw, radio->tx_ba[id].address, radio->sta_vif ? radio->sta_vif->addr : radio->ap_vif->addr);
		configured = sta && rtl_ax_ra_peer(radio, sta, id, command, &rate, &caps, true);
		he = configured && sta->deflink.he_cap.has_he;
		if (configured)
			rtl_ax_station_join(join, id, min_t(unsigned int, radio->channel.bandwidth,
				sta->deflink.bandwidth), &sta->deflink.he_cap, (command[3] & 7) + 1,
				radio->ap_vif && radio->ap_vif->type == NL80211_IFTYPE_MESH_POINT, radio->sta_vif != NULL);
		if (he) {
			rtl_ax_store32(cmac, id | 0x80);
			rtl_ax_store32(cmac + 28, rtl_ax_he_padding(sta->deflink.he_cap.he_cap_elem.phy_cap_info,
				sta->deflink.he_cap.ppe_thres, (command[3] & 7) + 1));
			rtl_ax_store32(cmac + 60, 255);
		}
		rcu_read_unlock();
		if (!configured) continue;
		if (he) ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 2, cmac, sizeof(cmac), true);
		if (!ret) ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 0, join, sizeof(join), true);
		if (!ret) ret = rtl_ax_command_flags(radio, RTL_AX_H2C_LONG_DATA, 2, 1, 0,
			command, sizeof(command), false, false);
		spin_lock_irqsave(&radio->command_lock, flags);
		radio->ra_update_error[id] = ret;
		if (!ret) {
			if (radio->sta_vif) radio->sta_join = rtl_ax_ap_read32(join);
			memcpy(radio->ra_config[id], command, sizeof(command));
			radio->ra_updates[id]++;
		}
		spin_unlock_irqrestore(&radio->command_lock, flags);
		if (!ret) rtl_ax_tx_peer_rate(radio->tx, id, rate, caps);
		else dev_err(&radio->pdev->dev, "MACID%u capability update failed: %d\n", id, ret);
	}
}

static void rtl_ax_link_sta_rc_update(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_link_sta *link, u32 changed)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	unsigned int id = ((struct rtl_ax_station *)link->sta->drv_priv)->macid;
	if (!(changed & (IEEE80211_RC_BW_CHANGED | IEEE80211_RC_NSS_CHANGED |
	    IEEE80211_RC_SMPS_CHANGED | IEEE80211_RC_SUPP_RATES_CHANGED))) return;
	if (!id || !radio->ra_registered[id]) return;
	set_bit(id, radio->ra_pending);
	wiphy_work_queue(hw->wiphy, &radio->ra_update_work);
}

static void rtl_ax_traffic_update(struct rtl_ax_ap *radio)
{
	unsigned int id;
	u32 total_tx = 0, total_rx = 0, ac[4];
	bool was_wmm = radio->traffic_wmm.enabled;
	for (id = 1; id < 128; id++) {
		struct rtl_ax_traffic_peer *p = &radio->traffic[id];
		struct ieee80211_sta *sta;
		u32 tx, packets;
		u8 feedback[8];
		unsigned long flags;
		if (!radio->ra_registered[id]) continue;
		rtl_ax_tx_peer_counters(radio->tx, id, &tx, &packets);
		rtl_ax_traffic_sample(p, tx, rtl_ax_rx_peer_bytes(radio->rx, id), packets);
		spin_lock_irqsave(&radio->command_lock, flags);
		memcpy(feedback, radio->ra_feedback[id], sizeof(feedback));
		spin_unlock_irqrestore(&radio->command_lock, flags);
		total_tx += p->tx_kbits; total_rx += p->rx_kbits;
		p->rate = (feedback[5] & 3) << 7 | (feedback[4] & 127);
		rcu_read_lock();
		sta = ieee80211_find_sta_by_ifaddr(radio->hw, radio->tx_ba[id].address, radio->ap_vif->addr);
		if (sta) {
			p->bandwidth = min_t(u8, radio->channel.bandwidth, sta->deflink.bandwidth);
			p->nss = sta->deflink.smps_mode == IEEE80211_SMPS_STATIC ? 1 : sta->deflink.rx_nss;
			/* Actual mib_table has amsdu_pps default0; no forced policy. */
			rtl_ax_traffic_amsdu(p, 0);
			sta->max_amsdu_subframes = p->amsdu_num ? 9 : 1;
			sta->deflink.agg.max_rc_amsdu_len = p->amsdu_bytes;
			rtl_ax_tx_peer_amsdu(radio->tx, id, rtl_ax_amsdu_queue_policy(
				p->amsdu_bytes, p->amsdu_num, p->bandwidth, p->band5g,
				p->tx_kbits, p->rx_kbits));
		}
		rcu_read_unlock();
	}
	rtl_ax_tx_ac_counters(radio->tx, ac);
	rtl_ax_traffic_wmm_update(&radio->traffic_wmm, ac);
	rtl_ax_tx_wmm(radio->tx, rtl_ax_traffic_wmm_quota(&radio->traffic_wmm,
		radio->chip == RTL_AX_8852C ? 2048 : 1024));
	if (radio->traffic_wmm.enabled && !was_wmm) {
		radio->traffic_edca_saved[0] = radio->io.read32(radio->io.ctx, 0xc300);
		radio->traffic_edca_saved[1] = radio->io.read32(radio->io.ctx, 0xc304);
		radio->io.write32(radio->io.ctx, 0xc300, 0x642b);
	} else if (!radio->traffic_wmm.enabled && was_wmm) {
		radio->io.write32(radio->io.ctx, 0xc300, radio->traffic_edca_saved[0]);
		radio->io.write32(radio->io.ctx, 0xc304, radio->traffic_edca_saved[1]);
	}
	for (id = 1; id < 128; id++) {
		struct rtl_ax_traffic_peer *p = &radio->traffic[id];
		if (!radio->ra_registered[id]) continue;
		if (((p->tx_kbits + p->rx_kbits) >> 10) * 100 /
		    ((total_tx >> 10) + (total_rx >> 10) + 1) >= 25) radio->traffic_peer = id;
	}
	id = radio->traffic_peer;
	if (id && radio->ra_registered[id] && !radio->traffic_wmm.enabled) {
		u16 window = rtl_ax_rx_ba_size(radio->rx, id, 0);
		u32 edca = rtl_ax_traffic_edca(&radio->traffic[id], !!window, window,
			rtl_ax_tx_peer_flows(radio->tx, id), radio->environment.nhm_cca_ratio, &radio->traffic_busy);
		radio->io.write32(radio->io.ctx, 0xc300, edca);
	}
}

static void rtl_ax_dm_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, dm_work.work);
	struct rtl_ax_environment *env = &radio->environment;
	unsigned long flags;

	if (!radio->monitor_started) return;
	/* Wiphy work shares start/stop/STA serialization; command_mutex
	 * also excludes sysfs/channel RFK while the meter and LCK use RF IO. */
	mutex_lock(&radio->command_mutex);
	radio->rf_runtime.result = rtl_ax_rf_runtime_update(&radio->io,
		radio->chip, &radio->rf_runtime);
	if (!radio->rf_runtime.result)
		radio->rf_runtime.result = rtl_ax_rxdck_tracking_locked(radio);
	if (!radio->rf_runtime.result && radio->chip == RTL_AX_8192XB)
		radio->rf_runtime.result = rtl_ax_xb_op5k_track(&radio->io, &radio->xb_op5k);
	if (radio->rf_runtime.result)
		dev_err(&radio->pdev->dev, "RF watchdog failed: %d\n", radio->rf_runtime.result);
	if (radio->channel.configured) {
		u8 rssi_min = 255;
		unsigned int i;
		bool linked = false;

		spin_lock_irqsave(&radio->command_lock, flags);
		for (i = 1; i < 128; i++) {
			u8 rssi;

			if (!radio->ra_registered[i]) continue;
			linked = true;
			rssi = rtl_ax_bb_rssi(&radio->ra_rssi_state[i]);
			if (rssi < rssi_min) rssi_min = rssi;
		}
		spin_unlock_irqrestore(&radio->command_lock, flags);
		if (!linked) rssi_min = 0;
		rtl_ax_dig_update(&radio->io, &radio->dig, radio->chip, linked, rssi_min,
			radio->chip == RTL_AX_8852C ? rtl_ax_environment_loss(
				radio->channel.external_loss[0], radio->channel.external_loss[1]) : 0,
			ktime_get_boottime_seconds());
		rtl_ax_bb_dm_edcca_update(&radio->io, &radio->bb_dm, linked, rssi_min);
	}
	if (radio->channel.configured) {
		u32 traffic = rtl_ax_tx_traffic_bytes(radio->tx) + rtl_ax_rx_traffic_bytes(radio->rx);
		u64 now = ktime_get_boottime_ns() / 1000000;

		if (env->background_started) {
			rtl_ax_environment_sample(&radio->io, env, radio->channel.bandwidth);
			env->throughput = div_u64((u64)(u32)(traffic - env->traffic_previous) * 8,
				(now - env->traffic_time_ms) * 1000);
		}
		if (radio->chip == RTL_AX_8852C) rtl_ax_environment_false_alarms(&radio->io, env);
		spin_lock_irqsave(&radio->command_lock, flags);
		if (radio->dfs.enabled)
			rtl_ax_dfs_dynamic(&radio->dfs, env->throughput,
				env->nhm_idle_ratio, env->nhm_noise_ratio, env->false_alarms);
		spin_unlock_irqrestore(&radio->command_lock, flags);
		env->traffic_previous = traffic;
		env->traffic_time_ms = now;
		rtl_ax_environment_trigger(&radio->io, env,
			radio->chip == RTL_AX_8852C ? rtl_ax_environment_loss(
				radio->channel.external_loss[0], radio->channel.external_loss[1]) : 0);
	}
	if (radio->ap_started && !radio->mesh_started) rtl_ax_traffic_update(radio);
	mutex_unlock(&radio->command_mutex);
	wiphy_delayed_work_queue(wiphy, &radio->dm_work, 2 * HZ);
}

static void rtl_ax_ra_work(struct wiphy *wiphy, struct wiphy_work *work)
{
	struct rtl_ax_ap *radio = container_of(work, struct rtl_ax_ap, ra_work.work);
	u8 command[1024] = {};
	unsigned int i, entries = 0;
	unsigned long flags;

	if (!radio->monitor_started) return;
	/* Original PHY collection runs continuously; only H2C RSSI/mask
	 * publication and moving-average factors have the two-second cadence. */
	spin_lock_irqsave(&radio->command_lock, flags);
	for (i = 1; i < 128; i++) {
		u8 *setting = command + entries * 8;
		if (!radio->ra_registered[i]) continue;
		rtl_ax_ra_rssi_watchdog(&radio->ra_rssi_state[i]);
		if (!radio->ra_rssi_samples[i]) continue;
		setting[0] = i;
		setting[1] = BIT(7) | (radio->ra_rssi[i][1] >> 1);
		setting[2] = BIT(7) | (radio->ra_rssi_state[i].ma[1][1] >> 5);
		setting[3] = radio->ra_rssi_state[i].ma[1][2] >> 5;
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
	/* The original watchdog follows RSSI reporting with a mask-only update.
	 * ra_config retains the full capability mask so lower rates can return. */
	for (i = 1; i < 128; i++) {
		u8 mask_command[16], rssi, previous, level;
		int ret;

		spin_lock_irqsave(&radio->command_lock, flags);
		if (!radio->ra_registered[i] || !radio->ra_rssi_samples[i]) {
			spin_unlock_irqrestore(&radio->command_lock, flags);
			continue;
		}
		memcpy(mask_command, radio->ra_config[i], sizeof(mask_command));
		rssi = radio->ra_rssi[i][0] >> 1;
		previous = radio->ra_rssi_level[i];
		spin_unlock_irqrestore(&radio->command_lock, flags);
		level = rtl_ax_ra_rssi_mask(mask_command, rssi, previous);
		rtl_ax_ra_environment(mask_command, radio->environment.nhm_noise_ratio);
		ret = rtl_ax_command_flags(radio, RTL_AX_H2C_LONG_DATA, 2, 1, 0,
			mask_command, sizeof(mask_command), false, false);
		spin_lock_irqsave(&radio->command_lock, flags);
		radio->ra_update_error[i] = ret;
		if (!ret) {
			radio->ra_rssi_level[i] = level;
			radio->ra_updates[i]++;
		}
		spin_unlock_irqrestore(&radio->command_lock, flags);
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
	if (reports && rtl_ax_rate_report(feedback, &info->txrate))
		info->filled |= BIT_ULL(NL80211_STA_INFO_TX_BITRATE);
}

static u32 rtl_ax_get_expected_throughput(struct ieee80211_hw *hw, struct ieee80211_sta *sta)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct rtl_ax_station *station = (void *)sta->drv_priv;
	struct rate_info rate;
	u8 feedback[8];
	u64 reports;
	unsigned long flags;
	u32 bitrate, success;

	if (!station->mesh || !station->macid) return 0;
	spin_lock_irqsave(&radio->command_lock, flags);
	memcpy(feedback, radio->ra_feedback[station->macid], 8);
	reports = radio->ra_reports[station->macid];
	spin_unlock_irqrestore(&radio->command_lock, flags);
	if (reports && rtl_ax_rate_report(feedback, &rate))
		bitrate = cfg80211_calculate_bitrate(&rate);
	else {
		u32 supported = sta->deflink.supp_rates[radio->band.band];

		if (!supported) return 0;
		bitrate = radio->band.bitrates[__ffs(supported)].bitrate;
	}
	/* HWMP treats expected throughput as already including losses. Use
	 * firmware's current rate and the actual unicast RPQ success EWMA;
	 * leave per-packet PHY status unknown when RPQ supplies no rate. */
	success = rtl_ax_tx_success(radio->tx, station->macid);
	return max(bitrate * success, 1U); /* 100 kbit/s * percent = kbit/s */
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
	.set_rts_threshold = rtl_ax_set_rts_threshold,
	.tx = rtl_ax_monitor_tx,
	.wake_tx_queue = rtl_ax_wake_tx_queue,
	.net_setup_tc = rtl_ax_net_setup_tc,
	.net_fill_forward_path = rtl_ax_net_fill_forward_path,
	.start = rtl_ax_monitor_start,
	.stop = rtl_ax_monitor_stop,
	.config = rtl_ax_monitor_config,
	.get_txpower = rtl_ax_monitor_get_txpower,
	.get_tsf = rtl_ax_get_tsf,
	.offset_tsf = rtl_ax_offset_tsf,
	.add_interface = rtl_ax_monitor_add_interface,
	.remove_interface = rtl_ax_monitor_remove_interface,
	.start_ap = rtl_ax_ap_start,
	.stop_ap = rtl_ax_ap_stop,
	.bss_info_changed = rtl_ax_ap_changed,
	.update_vif_offload = rtl_ax_update_vif_offload,
	.sta_set_4addr = rtl_ax_sta_set_4addr,
	.conf_tx = rtl_ax_ap_conf_tx,
	.ampdu_action = rtl_ax_ampdu_action,
	.sta_state = rtl_ax_sta_state,
	.sta_notify = rtl_ax_sta_notify,
	.release_buffered_frames_generation = rtl_ax_release_buffered,
	.sta_rate_tbl_update = rtl_ax_sta_rate_update,
	.link_sta_rc_update = rtl_ax_link_sta_rc_update,
	.sta_statistics = rtl_ax_sta_statistics,
	.get_expected_throughput = rtl_ax_get_expected_throughput,
	.set_key = rtl_ax_set_key,
	.get_key_seq = rtl_ax_get_key_seq,
	.set_tim = rtl_ax_set_tim,
	.configure_filter = rtl_ax_monitor_filter,
	.sw_scan_start = rtl_ax_sw_scan_start,
	.sw_scan_complete = rtl_ax_sw_scan_complete,
	.add_chanctx = ieee80211_emulate_add_chanctx,
	.remove_chanctx = ieee80211_emulate_remove_chanctx,
	.change_chanctx = ieee80211_emulate_change_chanctx,
};

static void rtl_ax_ps_poll(struct rtl_ax_ap *radio, const struct rtl_ax_rx_packet *packet)
{
	struct ieee80211_sta *sta;
	unsigned int macid = 0;

	if (packet->crc_error || packet->bytes < 20 ||
	    !ieee80211_is_pspoll(get_unaligned((const __le16 *)packet->payload)))
		return;
	rcu_read_lock();
	sta = ieee80211_find_sta_by_ifaddr(radio->hw, packet->payload + 10,
					packet->payload + 4);
	/* Actual rtw_proccess_pspoll validates AID and rejects a UAPSD AC
	 * before releasing the firmware queue, including already submitted WPs. */
	if (sta && sta->aid == (get_unaligned_le16(packet->payload + 2) & 0x3fff) &&
	    !(sta->uapsd_queues & BIT(IEEE80211_AC_BE)))
		macid = ((struct rtl_ax_station *)sta->drv_priv)->macid;
	rcu_read_unlock();
	if (macid) {
		radio->ps_poll_requests++;
		radio->ps_poll_errors += !!rtl_ax_host_getpkt(&radio->io, macid);
	}
}

static void rtl_ax_scan_complete_frame(struct rtl_ax_ap *radio,
	const struct rtl_ax_rx_packet *report)
{
	struct sk_buff *skb = radio->scan_pending;
	struct ieee80211_rx_status *status;
	int rpl = -EINVAL;

	if (!skb) return;
	radio->scan_pending = NULL;
	/* Do not match a wrapped PPDU ID after a report-sampling gap. */
	if (report && ktime_get_ns() - radio->scan_received_ns < 20000000 &&
	    !report->crc_error &&
	    (rtl_ax_ap_read32(report->descriptor) & BIT(23)) &&
	    rtl_ax_scan_ppdu_match(radio->scan_word0, radio->scan_word1,
		rtl_ax_ap_read32(report->descriptor),
		rtl_ax_ap_read32(report->descriptor + 4)))
		rpl = rtl_ax_scan_rpl(report->payload, report->bytes);
	status = IEEE80211_SKB_RXCB(skb);
	if (rpl >= 0) {
		status->signal = (rpl >> 1) - 110;
		status->flag &= ~RX_FLAG_NO_SIGNAL_VAL;
		radio->scan_last_rpl = rpl;
		memcpy(radio->scan_last_bssid, skb->data + 16, 6);
		radio->scan_matched++;
	} else {
		/* RX outlives remove_interface; use published state, not ap_vif. */
		if (!READ_ONCE(radio->mesh_started) &&
		    (!READ_ONCE(radio->sta_associated) ||
		     !ether_addr_equal(skb->data + 16, radio->sta_bssid)))
			status->flag |= RX_FLAG_ONLY_MONITOR;
		radio->scan_unmatched++;
	}
	ieee80211_rx_ni(radio->hw, skb);
}

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
	__le16 frame_control;
	u8 bandwidth = word >> 29, gi_ltf = (word >> 25) & 7;
	bool profile = READ_ONCE(rtl_ax_datapath_profile);
	u64 begin = profile ? ktime_get_ns() : 0, delivered = 0;

	/* mac80211 RX_INCLUDES_FCS requires a MAC header before the FCS.
	 * Hardware can report FCS-only records during receive transitions. */
	if (packet->bytes < 2 + FCS_LEN)
		return -EINVAL;
	frame_control = get_unaligned((const __le16 *)packet->payload);
	/* Actual hal_parsing_rx_wd_C/XB word1 carries rate16:24,
	 * GI/LTF25:27 and bandwidth29:31; no PPDU-derived RSSI is invented. */
	if (bandwidth >= ARRAY_SIZE(bandwidths))
		return -EOPNOTSUPP;
	if ((rate >> 7) == 0 && (rate > 11 || (radio->chip == RTL_AX_8852C && rate < 4)))
		return -EOPNOTSUPP;
	if ((rate >> 7) == 3 && gi_ltf >= ARRAY_SIZE(he_gi))
		return -EOPNOTSUPP;
	/* RXQ passes its DMA-backed skb. RPQ storage remains a bounded copy. */
	skb = packet->skb;
	if (!skb) {
		skb = dev_alloc_skb(packet->bytes);
		if (!skb) return -ENOMEM;
		skb_put_data(skb, packet->payload, packet->bytes);
	}
	status = IEEE80211_SKB_RXCB(skb);
	memset(status, 0, sizeof(*status));
	status->flag = RX_FLAG_NO_SIGNAL_VAL;
	/* Startup neighbour scans need discovery frames before start_ap. */
	if (!READ_ONCE(radio->ap_started) && !READ_ONCE(radio->sta_vif) &&
	    !ieee80211_is_beacon(frame_control) &&
	    !ieee80211_is_probe_resp(frame_control))
		status->flag |= RX_FLAG_ONLY_MONITOR;
	/* The chip retains IV/MIC, so mac80211 still checks the receive PN. */
	word = rtl_ax_ap_read32(packet->descriptor + 12);
	if ((word & BIT(2)) && !(word & BIT(1))) {
		if (packet->icv_error) {
			if (!packet->skb) dev_kfree_skb(skb);
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
	if (ieee80211_is_beacon(frame_control))
		status->boottime_ns = ktime_get_boottime_ns();
	rtl_ax_ps_poll(radio, packet);
	radio->mac80211_packets++;
	if (profile) {
		delivered = ktime_get_ns();
		if (!packet->skb) radio->rx_copy_bytes += packet->bytes;
		radio->rx_prepare_ns += delivered - begin;
	}
	if (!packet->crc_error && (ieee80211_is_assoc_req(frame_control) || ieee80211_is_reassoc_req(frame_control))) {
		unsigned int fixed = ieee80211_is_reassoc_req(frame_control) ? 34 : 28;
		if (packet->bytes >= fixed + FCS_LEN) {
			struct ieee80211_sta *sta;
			rcu_read_lock();
			sta = ieee80211_find_sta_by_ifaddr(radio->hw, packet->payload + 10, packet->payload + 4);
			if (sta) ((struct rtl_ax_station *)sta->drv_priv)->vendor =
				rtl_ax_vendor(packet->payload + 10, packet->payload + fixed, packet->bytes - fixed - FCS_LEN);
			rcu_read_unlock();
		}
	}
	/* RX dispatch runs in rtl_ax_rx_work, not the hardware IRQ. Process
	 * the frame here so rx_close's synchronous work cancellation also
	 * completes mac80211 delivery before .stop returns. */
	if (packet->bytes >= 36 + FCS_LEN && !packet->crc_error && (ieee80211_is_beacon(frame_control) ||
	    ieee80211_is_probe_resp(frame_control))) {
		radio->scan_pending = skb;
		radio->scan_word0 = rtl_ax_ap_read32(packet->descriptor);
		radio->scan_word1 = rtl_ax_ap_read32(packet->descriptor + 4);
		radio->scan_received_ns = ktime_get_ns();
	} else {
		ieee80211_rx_ni(radio->hw, skb);
	}
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
	switch (request->dfs_region) {
	case NL80211_DFS_FCC: radio->dfs_domain = 1; break;
	case NL80211_DFS_JP: radio->dfs_domain = 2; break;
	case NL80211_DFS_ETSI: radio->dfs_domain = 3; break;
	default: radio->dfs_domain = 0; break;
	}
	/* A country hint also arrives while stopped or after a failed .start.
	 * Cache its domain, but only an active interface owns detector setup. */
	if (radio->monitor_started && radio->radar_required) {
		ret = rtl_ax_receive_configure(radio, radio->channel.primary,
			radio->channel.center, radio->channel.bandwidth);
		if (ret)
			dev_err(&radio->pdev->dev,
				"country radar update failed: %d country=%c%c dfs_region=%u primary=%u center=%u width=%uMHz\n",
				ret, request->alpha2[0], request->alpha2[1],
				request->dfs_region, radio->channel.primary,
				radio->channel.center, 20U << radio->channel.bandwidth);
	}
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
	static const struct ieee80211_iface_limit ap_limit = {
		.max = 1, .types = BIT(NL80211_IFTYPE_AP) | BIT(NL80211_IFTYPE_STATION) | BIT(NL80211_IFTYPE_MESH_POINT),
	};
	static const struct ieee80211_iface_combination dfs_combination = {
		.limits = &ap_limit, .n_limits = 1, .max_interfaces = 1,
		.num_different_channels = 1,
		.radar_detect_widths = BIT(NL80211_CHAN_WIDTH_20_NOHT) | BIT(NL80211_CHAN_WIDTH_20) |
			BIT(NL80211_CHAN_WIDTH_40) | BIT(NL80211_CHAN_WIDTH_80) | BIT(NL80211_CHAN_WIDTH_160),
		.radar_detect_regions = BIT(NL80211_DFS_FCC) | BIT(NL80211_DFS_ETSI) | BIT(NL80211_DFS_JP),
	};
	static const u16 rates[] = { 10, 20, 55, 110, 60, 90, 120, 180, 240, 360, 480, 540 };
	/* Exact rtw_5ghz_a_channels entries; cfg80211 applies the country rules. */
	static const u8 channels_5g[] = {
		36, 40, 44, 48, 52, 56, 60, 64,
		100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
		149, 153, 157, 161, 165, 169, 173, 177,
	};
	struct ieee80211_hw *hw;
	unsigned int i, first_rate = radio->chip == RTL_AX_8852C ? 4 : 0;
	unsigned int channels = radio->chip == RTL_AX_8852C ? ARRAY_SIZE(channels_5g) : 13;
	enum nl80211_band band = radio->chip == RTL_AX_8852C ? NL80211_BAND_5GHZ : NL80211_BAND_2GHZ;
	int ret;

	hw = ieee80211_alloc_hw(sizeof(radio), &rtl_ax_monitor_ops);
	if (!hw)
		return -ENOMEM;
	*(struct rtl_ax_ap **)hw->priv = radio;
	SET_IEEE80211_DEV(hw, &radio->pdev->dev);
	SET_IEEE80211_PERM_ADDR(hw, radio->flash_calibration.wlan_address);
	hw->queues = 4;
	hw->max_rates = 1;
	hw->max_report_rates = 1;
	/* TX maps page fragments; keep software GSO from copying whole payloads. */
	hw->netdev_features = NETIF_F_SG;
	radio->requested_tx_dbm = 20;
	memcpy(radio->power_country, "00", 3);
	hw->wiphy->reg_notifier = rtl_ax_regulatory_notify;
	hw->sta_data_size = sizeof(struct rtl_ax_station);
	ieee80211_hw_set(hw, RX_INCLUDES_FCS);
	ieee80211_hw_set(hw, SIGNAL_DBM);
	ieee80211_hw_set(hw, RX_INLINE);
	/* mac80211 handles BIP and SW_MGMT_TX; RX retains the CCMP IV/MIC. */
	ieee80211_hw_set(hw, MFP_CAPABLE);
	ieee80211_hw_set(hw, AMPDU_AGGREGATION);
	ieee80211_hw_set(hw, HAS_RATE_CONTROL);
	ieee80211_hw_set(hw, SUPPORTS_AMSDU_IN_AMPDU);
	/* RPQ releases individual MPDUs without the transmitted PPDU length. */
	ieee80211_hw_set(hw, TX_STATUS_NO_AMPDU_LEN);
	ieee80211_hw_set(hw, SUPPORT_FAST_XMIT);
	/* mac80211 assembles host A-MSDUs; NI retains its PE encapsulation. */
	ieee80211_hw_set(hw, TX_AMSDU);
	ieee80211_hw_set(hw, TX_FRAG_LIST);
	hw->max_tx_fragments = RTL_AX_TX_WP_ADDRESS_MAX - 1;
	hw->max_tx_aggregation_subframes = 128;
	hw->max_rx_aggregation_subframes = 256;
	hw->wiphy->interface_modes = BIT(NL80211_IFTYPE_MONITOR) | BIT(NL80211_IFTYPE_AP) |
		BIT(NL80211_IFTYPE_MESH_POINT) | BIT(NL80211_IFTYPE_STATION);
	if (radio->chip == RTL_AX_8852C) {
		hw->wiphy->iface_combinations = &dfs_combination;
		hw->wiphy->n_iface_combinations = 1;
	}
	hw->wiphy->available_antennas_rx = 3;
	for (i = 0; i < channels; i++) {
		radio->channels[i].band = band;
		radio->channels[i].hw_value = band == NL80211_BAND_5GHZ ? channels_5g[i] : i + 1;
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
	/* Both board radios implement the original 2T2R HT20/40 channel path. */
	radio->band.ht_cap.ht_supported = true;
	radio->band.ht_cap.cap = IEEE80211_HT_CAP_LDPC_CODING |
		IEEE80211_HT_CAP_SGI_20 | IEEE80211_HT_CAP_MAX_AMSDU |
		IEEE80211_HT_CAP_SUP_WIDTH_20_40 | IEEE80211_HT_CAP_SGI_40;
	radio->band.ht_cap.ampdu_factor = IEEE80211_HT_MAX_AMPDU_64K;
	radio->band.ht_cap.mcs.rx_mask[0] = 0xff;
	radio->band.ht_cap.mcs.rx_mask[1] = 0xff;
	radio->band.ht_cap.mcs.rx_highest = cpu_to_le16(300);
	radio->band.ht_cap.mcs.tx_params = IEEE80211_HT_MCS_TX_DEFINED;
	if (radio->chip == RTL_AX_8852C) {
		radio->band.vht_cap.vht_supported = true;
		/* RMAC accepts 11966-byte MPDUs; native RXQ slots hold 12288 bytes. */
		radio->band.vht_cap.cap = IEEE80211_VHT_CAP_RXLDPC |
			IEEE80211_VHT_CAP_SUPP_CHAN_WIDTH_160MHZ |
			IEEE80211_VHT_CAP_SHORT_GI_160 | IEEE80211_VHT_CAP_SHORT_GI_80 | IEEE80211_VHT_CAP_MAX_MPDU_LENGTH_11454 |
			(IEEE80211_VHT_MAX_AMPDU_1024K << IEEE80211_VHT_CAP_MAX_A_MPDU_LENGTH_EXPONENT_SHIFT);
		/* Two streams MCS0..9; all higher NSS are unsupported. */
		radio->band.vht_cap.vht_mcs.rx_mcs_map = cpu_to_le16(0xfffa);
		radio->band.vht_cap.vht_mcs.tx_mcs_map = cpu_to_le16(0xfffa);
		radio->band.vht_cap.vht_mcs.rx_highest = cpu_to_le16(1734);
		radio->band.vht_cap.vht_mcs.tx_highest = cpu_to_le16(1734);
	}
	radio->he_band.types_mask = BIT(NL80211_IFTYPE_AP) | BIT(NL80211_IFTYPE_MESH_POINT) | BIT(NL80211_IFTYPE_STATION);
	radio->he_band.he_cap.has_he = true;
	radio->he_band.he_cap.he_cap_elem.mac_cap_info[0] = IEEE80211_HE_MAC_CAP0_HTC_HE;
	/* Original rtw_build_he_mac_caps extends the A-MPDU exponent by2. */
	radio->he_band.he_cap.he_cap_elem.mac_cap_info[3] = IEEE80211_HE_MAC_CAP3_MAX_AMPDU_LEN_EXP_EXT_2;
	radio->he_band.he_cap.he_cap_elem.mac_cap_info[4] = IEEE80211_HE_MAC_CAP4_AMSDU_IN_AMPDU;
	radio->he_band.he_cap.he_cap_elem.phy_cap_info[0] = band == NL80211_BAND_5GHZ ?
		(IEEE80211_HE_PHY_CAP0_CHANNEL_WIDTH_SET_40MHZ_80MHZ_IN_5G |
		 IEEE80211_HE_PHY_CAP0_CHANNEL_WIDTH_SET_160MHZ_IN_5G) :
		IEEE80211_HE_PHY_CAP0_CHANNEL_WIDTH_SET_40MHZ_IN_2G;
	radio->he_band.he_cap.he_cap_elem.phy_cap_info[1] = IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD |
		IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US;
	radio->he_band.he_cap.he_cap_elem.phy_cap_info[7] = IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI;
	/* Original AP protocol capability requires 16us receive processing. */
	radio->he_band.he_cap.he_cap_elem.phy_cap_info[9] =
		IEEE80211_HE_PHY_CAP9_NOMINAL_PKT_PADDING_16US << IEEE80211_HE_PHY_CAP9_NOMINAL_PKT_PADDING_POS;
	radio->he_band.he_cap.he_mcs_nss_supp.rx_mcs_80 = cpu_to_le16(0xfffa);
	radio->he_band.he_cap.he_mcs_nss_supp.tx_mcs_80 = cpu_to_le16(0xfffa);
	radio->he_band.he_cap.he_mcs_nss_supp.rx_mcs_160 = cpu_to_le16(band == NL80211_BAND_5GHZ ? 0xfffa : 0xffff);
	radio->he_band.he_cap.he_mcs_nss_supp.tx_mcs_160 = cpu_to_le16(band == NL80211_BAND_5GHZ ? 0xfffa : 0xffff);
	radio->he_band.he_cap.he_mcs_nss_supp.rx_mcs_80p80 = cpu_to_le16(0xffff);
	radio->he_band.he_cap.he_mcs_nss_supp.tx_mcs_80p80 = cpu_to_le16(0xffff);
	_ieee80211_set_sband_iftype_data(&radio->band, &radio->he_band, 1);
	hw->wiphy->bands[band] = &radio->band;
	radio->hw = hw;
	rtl_ax_tx_bind_hw(radio->tx, hw);
	spin_lock_init(&radio->pn_lock);
	wiphy_work_init(&radio->beacon_work, rtl_ax_tim_work);
	wiphy_delayed_work_init(&radio->mesh_sync_work, rtl_ax_mesh_sync_work);
	wiphy_work_init(&radio->offload_work, rtl_ax_offload_work);
	wiphy_work_init(&radio->ra_update_work, rtl_ax_ra_update_work);
	rtl_ax_tx_bind_ready(radio->tx, rtl_ax_offload_ready, radio);
	tasklet_setup(&radio->tx_tasklet, rtl_ax_tx_queues);
	wiphy_delayed_work_init(&radio->cleanup_work, rtl_ax_cleanup_work);
	wiphy_delayed_work_init(&radio->ra_work, rtl_ax_ra_work);
	wiphy_delayed_work_init(&radio->dm_work, rtl_ax_dm_work);
	ret = ieee80211_register_hw(hw);
	if (ret) {
		rtl_ax_tx_bind_hw(radio->tx, NULL);
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
	rtl_ax_tx_bind_hw(radio->tx, NULL);
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
		"keys_installed=%llu keys_removed=%llu keys_aborted=%llu keys_active=%u key_result=%d RX_decrypted=%llu "
		"TIM_requests=%llu TIM_updates=%llu PS_Poll_requests=%llu PS_Poll_errors=%llu "
		"profile=%u RX_copy_bytes=%llu RX_prepare_ns=%llu RX_mac80211_ns=%llu "
		"retired_MACIDs=%u RX_BA_active=%u RX_BA_deleting=%u TX_BA_stopping=%u "
		"auto_offload_starts=%llu auto_offload_result=%d STA_role=%u STA_associated=%u STA_result=%d\n",
		!!radio->hw, radio->monitor_started, radio->mac80211_packets, radio->tx_unimplemented,
		radio->ap_started, radio->ap_result, radio->station_added, radio->station_removed,
		radio->rx_ba_started, radio->rx_ba_stopped, radio->rx_ba_result,
		radio->keys_installed, radio->keys_removed, radio->keys_aborted,
		bitmap_weight(radio->security_ids, RTL_AX_SECURITY_ENTRIES), radio->key_result, radio->rx_decrypted,
		radio->tim_requests, radio->tim_updates,
		radio->ps_poll_requests, radio->ps_poll_errors,
		rtl_ax_datapath_profile, radio->rx_copy_bytes, radio->rx_prepare_ns, radio->rx_mac80211_ns,
		retired, ba_active, ba_deleting, tx_stopping, radio->offload_started, radio->offload_result,
		radio->sta_role, radio->sta_associated, radio->role_result);
}
static DEVICE_ATTR_RO(radio_mac80211);
#endif
