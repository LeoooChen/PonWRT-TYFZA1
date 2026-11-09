/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_WIFI_H
#define RTL9617C_WIFI_H
#include <linux/device.h>
#include <linux/netdevice.h>

/* TYFZA1 PE pool0 has2496 2KiB BIDs. Native radio pools follow the WAN pools. */
#define RTL9617C_WIFI_BUFFERS 2496
#define RTL9617C_WIFI_BID_START 1536
#define RTL9617C_WIFI_EPP_SHIFT 15
#define RTL9617C_WIFI_EPP_BYTES (1U << RTL9617C_WIFI_EPP_SHIFT)

int rtl_wifi_start(struct device *ni);
void rtl_wifi_stop(void);
int rtl9617c_wifi_setup_tc(struct net_device *netdev, enum tc_setup_type type, void *data);
int rtl9617c_wifi_peer_route(struct net_device *netdev, const u8 *address, unsigned int destination);
struct rtl_mc_pe_table;
int rtl9617c_wifi_mcast_table(struct net_device *netdev,unsigned int destination,
 struct rtl_mc_pe_table *table,bool bind);
int rtl9617c_wifi_id(struct net_device *netdev);
/* Forced teardown only: FE withdrawn, ES closed, PE/PCI DMA joined. */
int rtl9617c_wifi_retire(unsigned int pool);
#endif
