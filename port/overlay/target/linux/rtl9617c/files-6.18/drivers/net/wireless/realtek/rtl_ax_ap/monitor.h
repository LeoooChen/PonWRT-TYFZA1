/* SPDX-License-Identifier: GPL-2.0-only */
/* Linux receive integration for the native C 5GHz and XB2.4GHz paths.
 * Hardware channel and filters reuse the original-backed native controls. */
#ifndef RTL_AX_AP_MONITOR_H
#define RTL_AX_AP_MONITOR_H

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
	rtl_ax_rx_open(radio->rx);
	rtl_ax_tx_open(radio->tx);
	mutex_unlock(&radio->command_mutex);
	return 0;
}

static void rtl_ax_monitor_stop(struct ieee80211_hw *hw, bool suspend)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	mutex_lock(&radio->command_mutex);
	WRITE_ONCE(radio->monitor_started, false);
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
	if (!ret)
		ret = rtl_ax_ap_template(hw, vif, conf);
	radio->ap_result = ret;
	if (ret) {
		rtl_ax_update32(&radio->io, 0xc400, BIT(12), 0);
		return ret;
	}
	rtl_ax_receive_mode(&radio->io, 4);
	WRITE_ONCE(radio->ap_started, true);
	radio->beacon_enabled = true;
	return 0;
}

static void rtl_ax_ap_stop(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_bss_conf *conf)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	rtl_ax_update32(&radio->io, 0xc400, BIT(12), 0);
	radio->beacon_enabled = false;
	WRITE_ONCE(radio->ap_started, false);
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

static int rtl_ax_station_remove(struct ieee80211_hw *hw, struct ieee80211_vif *vif,
	struct ieee80211_sta *sta)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct rtl_ax_station *station = (void *)sta->drv_priv;
	u8 command[4], cam[60];
	int ret;

	if (!station->macid)
		return 0;
	/* Actual _remove_role: FW role REMOVE followed by address CAM valid0;
	 * BSSID CAM remains valid while the AP's self-role uses it. */
	rtl_ax_store32(command, station->macid | 0x4600);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 4, command, sizeof(command), true);
	if (ret)
		return ret;
	rtl_ax_peer_cam(cam, station->macid, vif->addr, sta->addr, sta->aid, false);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (!ret) {
		__clear_bit(station->macid, radio->station_ids);
		station->macid = 0;
		radio->station_removed++;
	}
	return ret;
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
	rtl_ax_peer_cam(cam, id, vif->addr, sta->addr, 0, true);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (ret)
		goto remove;
	ret = rtl_ax_station_tables(radio, id);
	if (ret)
		goto remove;
	ret = rtl_ax_station_connected_table(radio, id);
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
	radio->station_added++;
	dev_info(&radio->pdev->dev, "native AP station MACID%u AID%u connected\n", id, sta->aid);
	return 0;
remove:
	rtl_ax_station_remove(hw, vif, sta);
	return ret;
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
		return IEEE80211_AMPDU_TX_START_IMMEDIATE;
	case IEEE80211_AMPDU_TX_OPERATIONAL:
		aggregate = min_t(unsigned int, params->buf_size, 64);
		/* rtw_hal_cfg_tx_ampdu(count64) -> set_hw_ampdu_cfg, band0,
		 * normal/non-single mode, maximum time165 and number64. */
		rtl_ax_update8(&radio->io, 0xc604, 7, 0);
		rtl_ax_update32(&radio->io, 0xc610, 0xff0000ff, 0xa500003f);
		rtl_ax_station_cmac_connected(cmac, station->macid, radio->chip == RTL_AX_8852C, aggregate);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 2, cmac, sizeof(cmac), true);
		if (ret)
			return ret;
		WRITE_ONCE(station->tx_aggregation[params->tid], aggregate);
		dev_info(&radio->pdev->dev, "native TX BA MACID%u TID%u size%u operational\n",
			station->macid, params->tid, aggregate);
		return 0;
	case IEEE80211_AMPDU_TX_STOP_CONT:
	case IEEE80211_AMPDU_TX_STOP_FLUSH:
	case IEEE80211_AMPDU_TX_STOP_FLUSH_CONT:
		WRITE_ONCE(station->tx_aggregation[params->tid], 0);
		for (i = 0; i < IEEE80211_NUM_TIDS; i++)
			aggregate = max_t(unsigned int, aggregate, station->tx_aggregation[i]);
		rtl_ax_station_cmac_connected(cmac, station->macid, radio->chip == RTL_AX_8852C, aggregate);
		if (!aggregate)
			rtl_ax_store32(cmac + 48, 0x3ff);
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 5, 2, cmac, sizeof(cmac), true);
		ieee80211_stop_tx_ba_cb_irqsafe(vif, params->sta->addr, params->tid);
		return ret;
	case IEEE80211_AMPDU_RX_START:
		for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++)
			if (!radio->rx_ba[i].valid)
				break;
		if (i == ARRAY_SIZE(radio->rx_ba))
			return -ENOSPC;
		/* Actual C/XB MAC ops+496 reports extended standard entries8..15.
		 * ops+504 selects mac_bacam_info@23ca2c: index belongs to word1,
		 * standard-enable bit8, band0; own Linux allocation retains that ABI. */
		rtl_ax_store32(ba, 3 | (params->tid & 15) << 4 | station->macid << 8 |
			(params->ssn & 0xfff) << 20);
		rtl_ax_store32(ba + 4, (i + 8) << 28 | BIT(8));
		ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 12, 0, ba, sizeof(ba), true);
		radio->rx_ba_result = ret;
		if (ret)
			return ret;
		radio->rx_ba[i].macid = station->macid;
		radio->rx_ba[i].tid = params->tid;
		radio->rx_ba[i].valid = true;
		radio->rx_ba_started++;
		dev_info(&radio->pdev->dev, "native RX BA MACID%u TID%u entry%u SSN%u FW completed\n",
			station->macid, params->tid, i + 8, params->ssn);
		return 0;
	case IEEE80211_AMPDU_RX_STOP:
		for (i = 0; i < ARRAY_SIZE(radio->rx_ba); i++)
			if (radio->rx_ba[i].valid && radio->rx_ba[i].macid == station->macid &&
			    radio->rx_ba[i].tid == params->tid)
				break;
		if (i == ARRAY_SIZE(radio->rx_ba))
			return 0;
		rtl_ax_store32(ba, 2 | station->macid << 8);
		rtl_ax_store32(ba + 4, (i + 8) << 28);
		ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 12, 0, ba, sizeof(ba), true);
		radio->rx_ba_result = ret;
		if (ret)
			return ret;
		radio->rx_ba[i].valid = false;
		radio->rx_ba_stopped++;
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
	unsigned int aggregate = 0, density = 0;

	if (skb->len >= 24 && skb->len <= 0x3fff &&
	    (ieee80211_is_mgmt(header->frame_control) || ieee80211_is_data(header->frame_control))) {
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
			unsigned int tid = *ieee80211_get_qos_ctl(header) & IEEE80211_QOS_CTL_TID_MASK;
			struct rtl_ax_station *station = (void *)control->sta->drv_priv;

			aggregate = READ_ONCE(station->tx_aggregation[tid]);
			density = control->sta->deflink.ht_cap.ampdu_density;
		}
		if (!rtl_ax_tx_submit(radio->tx, hw, skb, rate, gi, bandwidth, macid, aggregate, density))
			return;
	} else
		radio->tx_unimplemented++;
	ieee80211_free_txskb(hw, skb);
}

static const struct ieee80211_ops rtl_ax_monitor_ops = {
	.tx = rtl_ax_monitor_tx,
	.wake_tx_queue = ieee80211_handle_wake_tx_queue,
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
	.sta_add = rtl_ax_station_add,
	.sta_remove = rtl_ax_station_remove,
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
	/* Original WD reports FCS and decrypt ICV separately. This passive
	 * path supplies the raw encrypted frame, without RX_FLAG_DECRYPTED;
	 * a missing decryption key does not make its FCS invalid. */
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
	/* RX dispatch runs in rtl_ax_rx_work, not the hardware IRQ. Process
	 * the frame here so rx_close's synchronous work cancellation also
	 * completes mac80211 delivery before .stop returns. */
	ieee80211_rx_ni(radio->hw, skb);
	return 0;
}

static void rtl_ax_regulatory_notify(struct wiphy *wiphy, struct regulatory_request *request)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(wiphy_to_ieee80211_hw(wiphy));

	mutex_lock(&radio->command_mutex);
	radio->power_country[0] = request->alpha2[0];
	radio->power_country[1] = request->alpha2[1];
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
	hw->queues = 4;
	hw->max_rates = 1;
	hw->max_report_rates = 1;
	radio->requested_tx_dbm = 20;
	memcpy(radio->power_country, "00", 3);
	hw->wiphy->reg_notifier = rtl_ax_regulatory_notify;
	hw->sta_data_size = sizeof(struct rtl_ax_station);
	ieee80211_hw_set(hw, RX_INCLUDES_FCS);
	ieee80211_hw_set(hw, AMPDU_AGGREGATION);
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
	radio->band.ht_cap.cap = IEEE80211_HT_CAP_SGI_20;
	radio->band.ht_cap.ampdu_factor = IEEE80211_HT_MAX_AMPDU_64K;
	radio->band.ht_cap.mcs.rx_mask[0] = 0xff;
	radio->band.ht_cap.mcs.rx_mask[1] = 0xff;
	radio->band.ht_cap.mcs.rx_highest = cpu_to_le16(144);
	radio->band.ht_cap.mcs.tx_params = IEEE80211_HT_MCS_TX_DEFINED;
	if (radio->chip == RTL_AX_8852C) {
		radio->band.ht_cap.cap |= IEEE80211_HT_CAP_SUP_WIDTH_20_40 | IEEE80211_HT_CAP_SGI_40;
		radio->band.ht_cap.mcs.rx_highest = cpu_to_le16(300);
		radio->band.vht_cap.vht_supported = true;
		radio->band.vht_cap.cap = IEEE80211_VHT_CAP_SHORT_GI_80 |
			(IEEE80211_VHT_MAX_AMPDU_64K << IEEE80211_VHT_CAP_MAX_A_MPDU_LENGTH_EXPONENT_SHIFT);
		/* Two streams MCS0..9; all higher NSS are unsupported. */
		radio->band.vht_cap.vht_mcs.rx_mcs_map = cpu_to_le16(0xfffa);
		radio->band.vht_cap.vht_mcs.tx_mcs_map = cpu_to_le16(0xfffa);
		radio->band.vht_cap.vht_mcs.rx_highest = cpu_to_le16(867);
		radio->band.vht_cap.vht_mcs.tx_highest = cpu_to_le16(867);
	}
	hw->wiphy->bands[band] = &radio->band;
	radio->hw = hw;
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
	ieee80211_unregister_hw(radio->hw);
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

	return sysfs_emit(buffer, "registered=%u receive_active=%u handed_to_mac80211=%llu "
		"TX_unimplemented=%llu AP_started=%u AP_result=%d station_added=%llu station_removed=%llu "
		"RX_BA_started=%llu RX_BA_stopped=%llu RX_BA_result=%d\n",
		!!radio->hw, radio->monitor_started, radio->mac80211_packets, radio->tx_unimplemented,
		radio->ap_started, radio->ap_result, radio->station_added, radio->station_removed,
		radio->rx_ba_started, radio->rx_ba_stopped, radio->rx_ba_result);
}
static DEVICE_ATTR_RO(radio_mac80211);
#endif
