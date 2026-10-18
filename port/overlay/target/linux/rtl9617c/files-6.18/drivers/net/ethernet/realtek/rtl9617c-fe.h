/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_H
#define RTL9617C_FE_H
#include <linux/device.h>
#include <linux/netdevice.h>

struct rtl_fe;
struct rtl_itu_paths;
int rtl_fe_set_itu_paths(struct rtl_fe *fe, struct net_device *dev, const struct rtl_itu_paths *paths);
struct rtl_fe *rtl_fe_allocate(struct device *device, void __iomem *registers);
void rtl_fe_free(struct rtl_fe *fe);
int rtl_fe_add_port(struct rtl_fe *fe, struct net_device *netdev,
                    unsigned int source, unsigned int destination, unsigned int llid);
int rtl_fe_add_fttr_port(struct rtl_fe *fe, struct net_device *netdev);
int rtl_fe_fttr_open(struct rtl_fe *fe);
int rtl_fe_fttr_close(struct rtl_fe *fe);
int rtl_fe_fttr_set(struct rtl_fe *fe, bool allowed);
ssize_t rtl_fe_fttr_state(struct rtl_fe *fe, char *buffer);
void rtl_fe_remove_port(struct rtl_fe *fe, struct net_device *netdev);
int rtl_fe_add_wifi_port(struct rtl_fe *fe, struct net_device *netdev, unsigned int id);
int rtl_fe_wifi_peer_route(struct rtl_fe *fe, struct net_device *netdev, const u8 *address, unsigned int destination);
bool rtl_fe_enabled(struct rtl_fe *fe);
int rtl_fe_flush_port(struct rtl_fe *fe, struct net_device *netdev);
int rtl_fe_setup_tc(struct rtl_fe *fe, struct net_device *netdev,
                    enum tc_setup_type type, void *data);
int rtl_fe_enable(struct rtl_fe *fe);
int rtl_fe_disable(struct rtl_fe *fe);
ssize_t rtl_fe_state(struct rtl_fe *fe, char *buffer);
#endif
