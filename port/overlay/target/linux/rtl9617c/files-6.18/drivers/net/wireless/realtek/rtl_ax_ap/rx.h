/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RX_H
#define RTL_AX_AP_RX_H
#include <linux/types.h>
struct rtl_ax_rx;
struct rtl_ax_io;
struct rtl_ax_rx_packet;
struct pci_dev;

typedef int (*rtl_ax_rx_handler)(void *ctx, const struct rtl_ax_rx_packet *packet);
struct rtl_ax_rx *rtl_ax_rx_prepare(struct pci_dev *pdev, const struct rtl_ax_io *io,
				  rtl_ax_rx_handler handler, void *ctx);
void rtl_ax_rx_open(struct rtl_ax_rx *rx);
void rtl_ax_rx_close(struct rtl_ax_rx *rx);
void rtl_ax_rx_free(struct rtl_ax_rx *rx);
int rtl_ax_rx_status(struct rtl_ax_rx *rx, char *out, size_t capacity);
int rtl_ax_rx_data_observation(struct rtl_ax_rx *rx, char *out, size_t capacity);
#endif
