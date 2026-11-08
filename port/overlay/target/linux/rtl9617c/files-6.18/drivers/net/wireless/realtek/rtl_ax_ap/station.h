/* SPDX-License-Identifier: GPL-2.0-only */
/* Factory-backed single-client role. MACID0 remains the AP/group sentinel. */
#ifndef RTL_AX_CLIENT_H
#define RTL_AX_CLIENT_H
#define RTL_AX_CLIENT_MACID 1

static int rtl_ax_client_update(struct rtl_ax_ap *radio, struct ieee80211_vif *vif,
	bool associated)
{
	u8 command[4], cam[60];
	int ret;

	if (!radio->sta_role) return -ENODEV;
	rtl_ax_store32(command, associated ? radio->sta_join : RTL_AX_CLIENT_MACID | 0x04040100);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 0, command, 4, true);
	if (ret) return ret;
	rtl_ax_client_cam(cam, RTL_AX_CLIENT_MACID, vif->addr, radio->sta_bssid,
		vif->cfg.aid, associated, true);
	if (associated && (radio->sta_join & BIT(12)))
		rtl_ax_store32(cam + 32, RTL_AX_CLIENT_MACID | BIT(14));
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (ret) return ret;
	memcpy(radio->role_cam, cam, sizeof(cam));
	mutex_lock(&radio->command_mutex);
	/* Factory STA: BSSID match and TSF sync, no beacon transmission. */
	rtl_ax_port0_field(&radio->io, 0xc400, 0x13c1f, associated ? 0x1281c : 4);
	rtl_ax_port0_field(&radio->io, 0xc414, 0xffffff,
		vif->bss_conf.beacon_int ? vif->bss_conf.beacon_int : 100);
	rtl_ax_update16(&radio->io, 0xc412, 0xff00, 1U << 8);
	radio->io.write8(radio, 0xc590, 50);
	rtl_ax_update8(&radio->io, 0xca08, 0, 3);
	radio->io.write8(radio, 0xc427, vif->bss_conf.dtim_period ? vif->bss_conf.dtim_period : 1);
	rtl_ax_port0_field(&radio->io, 0xc404, 0xfff0000, 400U << 16);
	rtl_ax_port0_field(&radio->io, 0xc408, 0xfff0000, 0);
	rtl_ax_port0_field(&radio->io, 0xc40c, 0xfff, 160);
	rtl_ax_port0_field(&radio->io, 0xc404, 0xff, 4);
	rtl_ax_update16(&radio->io, 0xc40e, 0xfff, 5);
	rtl_ax_receive_mode(&radio->io, radio->scanning ? 0 : 2);
	mutex_unlock(&radio->command_mutex);
	radio->sta_associated = associated;
	return 0;
}

static int rtl_ax_client_prepare(struct rtl_ax_ap *radio, struct ieee80211_vif *vif)
{
	u8 command[4], cam[60], unpause[32];
	int ret;

	if (radio->retired_stations[RTL_AX_CLIENT_MACID].pending) return -EBUSY;
	if (radio->sta_role) return 0;
	if (!bitmap_empty(radio->station_ids, 128)) return -EBUSY;
	/* Probe installed an AP self role. Release its CAM before STA takes CAM0. */
	if (radio->role_stage) {
		rtl_ax_store32(command, 0x500 | (u32)radio->role_type << 13);
		ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 4, command, 4, true);
		if (ret) return ret;
		/* Firmware no longer owns AP MACID0, even if CAM cleanup fails. */
		radio->role_stage = 0;
		memcpy(cam, radio->role_cam, sizeof(cam));
		cam[8] &= ~1;
		cam[52] &= ~1;
		ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
		if (ret) return ret;
	}
	rtl_ax_role_unpause(unpause, RTL_AX_CLIENT_MACID);
	ret = rtl_ax_command_flags(radio, RTL_AX_H2C_CMD, 1, 9, 8, unpause, 32, true, false);
	if (ret) return ret;
	/* Retain ownership on a lost ACK; cleanup must precede MACID reuse. */
	__set_bit(RTL_AX_CLIENT_MACID, radio->station_ids);
	radio->sta_role = true;
	radio->sta_join = RTL_AX_CLIENT_MACID | 0x06040000;
	radio->retired_stations[RTL_AX_CLIENT_MACID].pending = true;
	radio->retired_stations[RTL_AX_CLIENT_MACID].client = true;
	ether_addr_copy(radio->retired_stations[RTL_AX_CLIENT_MACID].self, vif->addr);
	rtl_ax_store32(command, RTL_AX_CLIENT_MACID | 0x2000);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 8, 4, command, 4, true);
	if (ret) goto failed;
	eth_zero_addr(radio->sta_bssid);
	rtl_ax_client_cam(cam, RTL_AX_CLIENT_MACID, vif->addr, radio->sta_bssid, 0, false, true);
	ret = rtl_ax_command(radio, RTL_AX_H2C_DATA, 1, 6, 0, cam, sizeof(cam), true);
	if (!ret) ret = rtl_ax_station_tables(radio, RTL_AX_CLIENT_MACID);
	if (!ret) ret = rtl_ax_client_update(radio, vif, false);
	if (!ret) {
		mutex_lock(&radio->command_mutex);
		rtl_ax_port0_color(&radio->io, 0);
		mutex_unlock(&radio->command_mutex);
		radio->retired_stations[RTL_AX_CLIENT_MACID].pending = false;
	}
failed:
	if (ret) wiphy_delayed_work_queue(radio->hw->wiphy, &radio->cleanup_work, HZ);
	return ret;
}

static void rtl_ax_client_cfg_changed(struct ieee80211_hw *hw,
	struct ieee80211_vif *vif, u64 changed)
{
	struct rtl_ax_ap *radio = rtl_ax_monitor_radio(hw);

	if (vif->type != NL80211_IFTYPE_STATION ||
	    !(changed & (BSS_CHANGED_ASSOC | BSS_CHANGED_BSSID | BSS_CHANGED_BEACON_INT | BSS_CHANGED_BEACON_INFO))) return;
	if (changed & BSS_CHANGED_BSSID) {
		ether_addr_copy(radio->sta_bssid, vif->bss_conf.bssid);
		ether_addr_copy(radio->retired_stations[RTL_AX_CLIENT_MACID].peer, vif->bss_conf.bssid);
	}
	if ((changed & BSS_CHANGED_ASSOC) && !vif->cfg.assoc) {
		radio->sta_associated = false;
		rtl_ax_tx_peer_authorized(radio->tx, RTL_AX_CLIENT_MACID, false);
		radio->peer_authorized[RTL_AX_CLIENT_MACID] = false;
		radio->offload_result = rtl_ax_tx_ni_retire_peer(radio->tx, RTL_AX_CLIENT_MACID);
	}
	if (!radio->sta_role || radio->retired_stations[RTL_AX_CLIENT_MACID].pending) return;
	radio->role_result = rtl_ax_client_update(radio, vif, vif->cfg.assoc);
	if (radio->role_result && vif->cfg.assoc) {
		radio->sta_associated = false;
		radio->peer_authorized[RTL_AX_CLIENT_MACID] = false;
		rtl_ax_tx_peer_authorized(radio->tx, RTL_AX_CLIENT_MACID, false);
		radio->offload_result = rtl_ax_tx_ni_retire_peer(radio->tx, RTL_AX_CLIENT_MACID);
		ieee80211_connection_loss(vif);
	}
}
#endif
