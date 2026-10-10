/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_TX_H
#define RTL_AX_AP_TX_H
#include <linux/types.h>
struct rtl_ax_tx;
struct rtl_ax_io;
struct pci_dev;
struct ieee80211_hw;
struct sk_buff;
struct rtl_ax_rx_packet;

struct rtl_ax_tx *rtl_ax_tx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io);
void rtl_ax_tx_free(struct rtl_ax_tx *tx, struct ieee80211_hw *hw);
int rtl_ax_tx_pause(struct rtl_ax_tx *tx);
void rtl_ax_tx_open(struct rtl_ax_tx *tx);
int rtl_ax_tx_submit(struct rtl_ax_tx *tx, struct ieee80211_hw *hw, struct sk_buff *skb, unsigned int rate, unsigned int macid);
int rtl_ax_tx_report(struct rtl_ax_tx *tx, struct ieee80211_hw *hw, const struct rtl_ax_rx_packet *packet);
int rtl_ax_tx_status(struct rtl_ax_tx *tx, char *out, size_t capacity);
#endif
