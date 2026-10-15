/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_TX_H
#define RTL_AX_AP_TX_H
#include <linux/types.h>
struct rtl_ax_tx;
struct rtl_ax_rx;
struct device;
struct rtl_ax_io;
struct pci_dev;
struct ieee80211_hw;
struct sk_buff;
struct rtl_ax_rx_packet;
struct ieee80211_sta;
struct net_device;
extern bool rtl_ax_datapath_profile;

struct rtl_ax_tx *rtl_ax_tx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io);
struct device *rtl_ax_tx_dma_device(struct rtl_ax_tx *tx);
void rtl_ax_tx_bind_rx(struct rtl_ax_tx *tx, struct rtl_ax_rx *rx);
void rtl_ax_tx_bind_ready(struct rtl_ax_tx *tx, void (*ready)(void *), void *context);
int rtl_ax_tx_ni_peer(struct rtl_ax_tx *tx, struct net_device *netdev, unsigned int macid);
int rtl_ax_tx_uplink(struct rtl_ax_tx *tx, struct net_device *netdev, unsigned int macid, unsigned int wifi_id);
int rtl_ax_tx_free(struct rtl_ax_tx *tx, struct ieee80211_hw *hw);
int rtl_ax_tx_pause(struct rtl_ax_tx *tx);
void rtl_ax_tx_open(struct rtl_ax_tx *tx);
int rtl_ax_tx_flush_key(struct rtl_ax_tx *tx, unsigned int index);
bool rtl_ax_tx_station_pending(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid);
int rtl_ax_tx_wait_station(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid);
int rtl_ax_tx_submit(struct rtl_ax_tx *tx, struct ieee80211_hw *hw, struct sk_buff *skb, unsigned int rate, unsigned int gi, unsigned int bandwidth, unsigned int macid, unsigned int aggregate, unsigned int density);
int rtl_ax_tx_report(struct rtl_ax_tx *tx, struct ieee80211_hw *hw, const struct rtl_ax_rx_packet *packet);
int rtl_ax_tx_status(struct rtl_ax_tx *tx, char *out, size_t capacity);
void rtl_ax_tx_ap_address(struct rtl_ax_tx *tx, const u8 *address);
void rtl_ax_tx_peer_qos(struct rtl_ax_tx *tx, unsigned int macid, bool qos);
int rtl_ax_tx_peer_ssn(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid);
int rtl_ax_tx_ni_test(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid);
int rtl_ax_tx_ni_route(struct rtl_ax_tx *tx, struct net_device *netdev, unsigned int macid);
int rtl_ax_tx_ni_retire_peer(struct rtl_ax_tx *tx, unsigned int macid);
int rtl_ax_mac_drop_peer(struct pci_dev *pdev, unsigned int macid);
void rtl_ax_tx_peer_sleep(struct rtl_ax_tx *tx, unsigned int macid, bool asleep);
void rtl_ax_tx_peer_authorized(struct rtl_ax_tx *tx, unsigned int macid, bool authorized);
void rtl_ax_tx_peer_ba(struct rtl_ax_tx *tx, unsigned int macid, unsigned int tid, unsigned int size);
void rtl_ax_tx_peer_rate(struct rtl_ax_tx *tx, unsigned int macid, u32 rate, u32 caps);
bool rtl_ax_tx_ni_buffered(struct rtl_ax_tx *tx, unsigned int macid);
void rtl_ax_tx_ps_release(struct rtl_ax_tx *tx, struct ieee80211_sta *sta,
	unsigned int macid, u16 tids, bool uapsd, bool more_data);
#endif
