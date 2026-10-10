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
	ret = rtl_ax_receive_set_channel(radio, hw->conf.chandef.chan->hw_value);
	if (ret) {
		mutex_unlock(&radio->command_mutex);
		return ret;
	}
	rtl_ax_update32(&radio->io, 0xce20, 0x47ff, 0x440f);
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
	rtl_ax_tx_pause(radio->tx);
	rtl_ax_rx_close(radio->rx);
	mutex_unlock(&radio->command_mutex);
}

static int rtl_ax_monitor_config(struct ieee80211_hw *hw, int radio_idx, u32 changed)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	int ret;

	if (!(changed & IEEE80211_CONF_CHANGE_CHANNEL))
		return 0;
	if (hw->conf.chandef.width != NL80211_CHAN_WIDTH_20 &&
	    hw->conf.chandef.width != NL80211_CHAN_WIDTH_20_NOHT)
		return -EOPNOTSUPP;
	mutex_lock(&radio->command_mutex);
	ret = rtl_ax_receive_set_channel(radio, hw->conf.chandef.chan->hw_value);
	mutex_unlock(&radio->command_mutex);
	return ret;
}

static int rtl_ax_monitor_add_interface(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
	/* Passive monitors need no original MAC role or address-CAM entry. */
	return vif->type == NL80211_IFTYPE_MONITOR ? 0 : -EOPNOTSUPP;
}

static void rtl_ax_monitor_remove_interface(struct ieee80211_hw *hw, struct ieee80211_vif *vif)
{
}

static void rtl_ax_monitor_filter(struct ieee80211_hw *hw,
	unsigned int changed, unsigned int *flags, u64 multicast)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	*flags &= FIF_ALLMULTI | FIF_BCN_PRBRESP_PROMISC | FIF_CONTROL |
		FIF_OTHER_BSS | FIF_PSPOLL | FIF_PROBE_REQ | FIF_MCAST_ACTION;
	mutex_lock(&radio->command_mutex);
	rtl_ax_update32(&radio->io, 0xce20, 0x47ff, 0x440f);
	mutex_unlock(&radio->command_mutex);
}

static void rtl_ax_monitor_tx(struct ieee80211_hw *hw,
	struct ieee80211_tx_control *control, struct sk_buff *skb)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);
	struct ieee80211_hdr *header = (void *)skb->data;
	int rate_index = IEEE80211_SKB_CB(skb)->control.rates[0].idx;
	unsigned int rate = radio->chip == RTL_AX_8852C ? 4 : 0;

	/* Raw management first: same original builder used by AP beacon,
	 * probe and association traffic. DATA WD and roles follow this path. */
	if (skb->len >= 24 && skb->len <= 0x3fff &&
	    (ieee80211_is_mgmt(header->frame_control) || ieee80211_is_data(header->frame_control))) {
		if (rate_index >= 0 && rate_index < radio->band.n_bitrates)
			rate = radio->band.bitrates[rate_index].hw_value;
		if (!rtl_ax_tx_submit(radio->tx, hw, skb, rate))
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
	status->flag = RX_FLAG_ONLY_MONITOR | RX_FLAG_NO_SIGNAL_VAL;
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
	ieee80211_rx_irqsafe(radio->hw, skb);
	return 0;
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
	ieee80211_hw_set(hw, RX_INCLUDES_FCS);
	hw->wiphy->interface_modes = BIT(NL80211_IFTYPE_MONITOR);
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
		"TX_unimplemented=%llu interface=monitor AP=pending\n",
		!!radio->hw, radio->monitor_started, radio->mac80211_packets, radio->tx_unimplemented);
}
static DEVICE_ATTR_RO(radio_mac80211);
#endif
