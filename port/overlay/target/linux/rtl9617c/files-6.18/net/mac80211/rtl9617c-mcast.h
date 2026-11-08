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

/* Caller holds wiphy through use of the returned key; no pointer is retained. */
int ieee80211_rtl9617c_bcmc_key(struct ieee80211_vif *vif,struct ieee80211_key_conf **result)
{
 struct ieee80211_sub_if_data *sdata=vif_to_sdata(vif);
 struct ieee80211_key *key;
 lockdep_assert_wiphy(sdata->local->hw.wiphy);
 if(vif->type!=NL80211_IFTYPE_AP || sdata->u.ap.multicast_to_unicast || ieee80211_vif_is_mld(vif) || sdata->wdev.use_4addr ||
    sdata->local->hw.wiphy->frag_threshold!=(u32)-1 ||
    sdata->control_port_protocol==htons(ETH_P_IP) || sdata->control_port_protocol==htons(ETH_P_IPV6) ||
    sdata->control_port_protocol==htons(ETH_P_8021Q) || sdata->control_port_protocol==htons(ETH_P_8021AD))return -EOPNOTSUPP;
 key=wiphy_dereference(sdata->local->hw.wiphy,sdata->deflink.default_multicast_key);
 if(key && (!(key->flags&KEY_FLAG_UPLOADED_TO_HARDWARE) || (key->flags&KEY_FLAG_TAINTED) ||
    (key->conf.cipher!=WLAN_CIPHER_SUITE_CCMP && key->conf.cipher!=WLAN_CIPHER_SUITE_GCMP) || (key->conf.flags&IEEE80211_KEY_FLAG_PAIRWISE) ||
    key->conf.keyidx<0 || key->conf.keyidx>3 || !rtl9617c_key_pn_token(&key->conf.rtl9617c_pn)))return -EOPNOTSUPP;
 *result=key?&key->conf:NULL;
 return 0;
}
EXPORT_SYMBOL_GPL(ieee80211_rtl9617c_bcmc_key);

/* Match the current mac80211 unicast key, not a cached driver CAM role.
 * The wiphy lock pins the returned key through the caller's validation. */
int ieee80211_rtl9617c_unicast_key(struct ieee80211_vif *vif,const u8 *address,
	struct ieee80211_key_conf **result)
{
	struct ieee80211_sub_if_data *sdata=vif_to_sdata(vif);
	struct sta_info *sta;
	struct ieee80211_key *key;
	int ret=-EOPNOTSUPP;
	lockdep_assert_wiphy(sdata->local->hw.wiphy);
	if ((vif->type!=NL80211_IFTYPE_AP && vif->type!=NL80211_IFTYPE_STATION) ||
	    ieee80211_vif_is_mld(vif) || sdata->wdev.use_4addr) return ret;
	rcu_read_lock();
	sta=sta_info_get(sdata,address);
	if (!sta || !sta->uploaded || !test_sta_flag(sta,WLAN_STA_AUTHORIZED)) goto out;
	key=rcu_dereference(sta->ptk[sta->ptk_idx]);
	if (!key) key=rcu_dereference(sdata->default_unicast_key);
	if (!key) {
		if (test_sta_flag(sta,WLAN_STA_USES_ENCRYPTION)) goto out;
	} else if (!(key->flags&KEY_FLAG_UPLOADED_TO_HARDWARE) ||
		   (key->flags&KEY_FLAG_TAINTED) || (key->conf.cipher!=WLAN_CIPHER_SUITE_CCMP && key->conf.cipher!=WLAN_CIPHER_SUITE_GCMP) ||
		   !(key->conf.flags&IEEE80211_KEY_FLAG_PAIRWISE)) goto out;
	*result=key?&key->conf:NULL;
	ret=0;
out:
	rcu_read_unlock();
	return ret;
}
EXPORT_SYMBOL_GPL(ieee80211_rtl9617c_unicast_key);

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
