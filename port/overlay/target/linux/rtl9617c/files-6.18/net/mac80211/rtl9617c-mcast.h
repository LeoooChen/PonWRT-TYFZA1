/* SPDX-License-Identifier: GPL-2.0-only */
/* Included by wme.c, so policy snapshots execute the same classifier as TX. */
int ieee80211_rtl9617c_seq_bind(struct ieee80211_vif *vif, struct rtl_seq_shared *shared,
	unsigned int generation)
{
	struct ieee80211_sub_if_data *sdata = vif_to_sdata(vif);
	lockdep_assert_wiphy(sdata->local->hw.wiphy);
	if ((vif->type != NL80211_IFTYPE_AP && vif->type != NL80211_IFTYPE_STATION) ||
	    ieee80211_vif_is_mld(vif)) return -EOPNOTSUPP;
	return rtl9617c_seq_bind(&sdata->rtl9617c_sequence, shared, generation, &sdata->sequence_number);
}
EXPORT_SYMBOL_GPL(ieee80211_rtl9617c_seq_bind);

int ieee80211_rtl9617c_seq_unbind(struct ieee80211_vif *vif)
{
	struct ieee80211_sub_if_data *sdata = vif_to_sdata(vif);
	lockdep_assert_wiphy(sdata->local->hw.wiphy);
	return rtl9617c_seq_unbind(&sdata->rtl9617c_sequence, &sdata->sequence_number);
}
EXPORT_SYMBOL_GPL(ieee80211_rtl9617c_seq_unbind);

int ieee80211_rtl9617c_next_sequence(struct ieee80211_vif *vif, u16 *sequence)
{
	struct ieee80211_sub_if_data *sdata;
	if (!vif) return -EINVAL;
	sdata = vif_to_sdata(vif);
	return rtl9617c_seq_next(&sdata->rtl9617c_sequence, &sdata->sequence_number, sequence);
}
EXPORT_SYMBOL_GPL(ieee80211_rtl9617c_next_sequence);

int ieee80211_rtl9617c_mcast_policy(struct ieee80211_vif *vif,const u8 *address,
	struct rtl9617c_wifi_policy *policy)
{
	struct ieee80211_sub_if_data *sdata = vif_to_sdata(vif);
	struct rtl9617c_wifi_policy result = {};
	struct sta_info *sta;
	struct sk_buff *skb;
	u8 *frame;
	unsigned int i;
	int ret = -EOPNOTSUPP;

	lockdep_assert_wiphy(sdata->local->hw.wiphy);
	if ((vif->type != NL80211_IFTYPE_AP && vif->type != NL80211_IFTYPE_STATION) ||
	    ieee80211_vif_is_mld(vif) || sdata->wdev.use_4addr ||
	    sdata->local->hw.queues < IEEE80211_NUM_ACS ||
	    sdata->local->hw.wiphy->frag_threshold != (u32)-1)
		return -EOPNOTSUPP;
	/* Authentication/control-port traffic must stay on mac80211's path;
	 * one override map cannot cover EtherType-dependent control policy. */
	if (sdata->control_port_protocol == htons(ETH_P_IP) ||
	    sdata->control_port_protocol == htons(ETH_P_IPV6) ||
	    sdata->control_port_protocol == htons(ETH_P_8021Q) ||
	    sdata->control_port_protocol == htons(ETH_P_8021AD))
		return -EOPNOTSUPP;
	/* Direct TX has no mac80211 per-packet TSPEC airtime accounting yet. */
	if (vif->type == NL80211_IFTYPE_STATION)
		for (i = 0; i < IEEE80211_NUM_ACS; i++)
			if (sdata->u.mgd.tx_tspec[i].admitted_time)
				return -EOPNOTSUPP;
	skb = alloc_skb(ETH_HLEN + sizeof(struct ipv6hdr), GFP_KERNEL);
	if (!skb) return -ENOMEM;
	frame = skb_put_zero(skb, ETH_HLEN + sizeof(struct ipv6hdr));
	ether_addr_copy(frame, address);
	ether_addr_copy(frame + ETH_ALEN, vif->addr);
	skb_reset_mac_header(skb);
	skb_set_network_header(skb, ETH_HLEN);
	rcu_read_lock();
	sta = sta_info_get(sdata, address);
	if (!sta || !sta->uploaded || !test_sta_flag(sta, WLAN_STA_AUTHORIZED))
		goto out;
	result.qos = sta->sta.wme;
	result.noack_map = sdata->noack_map & 255;
	for (i = 0; i < 64; i++) {
		skb->priority = 0;
		skb->protocol = htons(ETH_P_IP);
		frame[12] = 8; frame[13] = 0; frame[14] = 0x45; frame[15] = i << 2;
		ieee80211_select_queue(sdata, sta, skb);
		result.dscp_tid[i] = skb->priority;
		/* One published DSCP map must apply to both IP families. */
		skb->priority = 0;
		skb->protocol = htons(ETH_P_IPV6);
		frame[12] = 0x86; frame[13] = 0xdd;
		frame[14] = 0x60 | (i >> 2); frame[15] = (i & 3) << 6;
		ieee80211_select_queue(sdata, sta, skb);
		if (result.dscp_tid[i] != skb->priority) goto out;
	}
	skb->protocol = htons(ETH_P_8021Q);
	frame[12] = 0x81; frame[13] = 0;
	for (i = 0; i < 8; i++) {
		skb->priority = 256 + i;
		ieee80211_select_queue(sdata, sta, skb);
		result.priority_tid[i] = skb->priority;
	}
	skb->priority = 0;
	ieee80211_select_queue(sdata, sta, skb);
	result.tagged_tid = skb->priority;
	*policy = result;
	ret = 0;
out:
	rcu_read_unlock();
	kfree_skb(skb);
	return ret;
}
EXPORT_SYMBOL_GPL(ieee80211_rtl9617c_mcast_policy);
