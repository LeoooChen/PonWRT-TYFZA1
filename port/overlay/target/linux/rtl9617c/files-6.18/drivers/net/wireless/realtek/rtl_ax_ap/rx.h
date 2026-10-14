/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RX_H
#define RTL_AX_AP_RX_H
#include <linux/types.h>
struct rtl_ax_rx;
struct rtl_ax_io;
struct rtl_ax_rx_packet;
struct pci_dev;
struct device;
struct ieee80211_hw;
struct ieee80211_key_conf;

typedef int (*rtl_ax_rx_handler)(void *ctx, const struct rtl_ax_rx_packet *packet);
struct rtl_ax_rx *rtl_ax_rx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io,
				  rtl_ax_rx_handler handler, void *ctx, struct device *rpq_device);
void rtl_ax_rx_pe_rpq_start(struct rtl_ax_rx *rx, void *memory);
int rtl_ax_rx_pe_rpq_stop(struct rtl_ax_rx *rx);
void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx);
void rtl_ax_rx_uapsd(struct rtl_ax_rx *rx, unsigned int macid, u8 queues);
void rtl_ax_rx_key(struct rtl_ax_rx *rx, unsigned int macid, struct ieee80211_key_conf *key);
void rtl_ax_rx_uplink_peer(struct rtl_ax_rx *rx, unsigned int wifi_id, struct ieee80211_hw *hw);
bool rtl_ax_rx_uplink_active(struct rtl_ax_rx *rx, unsigned int macid);
void rtl_ax_rx_uplink_enable(struct rtl_ax_rx *rx, unsigned int macid);
void rtl_ax_rx_uplink_disable(struct rtl_ax_rx *rx, unsigned int macid);
void rtl_ax_rx_uplink_retire(struct rtl_ax_rx *rx, unsigned int macid);
void rtl_ax_rx_open(struct rtl_ax_rx *rx);
void rtl_ax_rx_close(struct rtl_ax_rx *rx);
void rtl_ax_rx_log_stall(struct rtl_ax_rx *rx);
void rtl_ax_rx_release_interrupts(struct rtl_ax_rx *rx);
void rtl_ax_rx_free(struct rtl_ax_rx *rx);
int rtl_ax_rx_status(struct rtl_ax_rx *rx, char *out, size_t capacity);
int rtl_ax_rx_data_observation(struct rtl_ax_rx *rx, char *out, size_t capacity);
#endif
