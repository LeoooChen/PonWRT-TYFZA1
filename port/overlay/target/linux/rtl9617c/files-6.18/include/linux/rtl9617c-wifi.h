/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_WIFI_H
#define RTL9617C_WIFI_H
#include <linux/device.h>
#include <linux/netdevice.h>

/* TYFZA1 PE pool0 has2496 2KiB BIDs. Native radio pools follow the WAN pools. */
#define RTL9617C_WIFI_BUFFERS 2496
#define RTL9617C_WIFI_BID_START 1536

int rtl_wifi_start(struct device *ni);
void rtl_wifi_stop(void);
int rtl9617c_wifi_setup_tc(struct net_device *netdev, enum tc_setup_type type, void *data);
int rtl9617c_wifi_peer_route(struct net_device *netdev, const u8 *address, unsigned int destination);
int rtl9617c_wifi_id(struct net_device *netdev);
#endif
