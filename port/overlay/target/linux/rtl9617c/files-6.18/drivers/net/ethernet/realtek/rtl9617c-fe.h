/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_H
#define RTL9617C_FE_H
#include <linux/device.h>
#include <linux/netdevice.h>

struct rtl_fe;
struct rtl_fe *rtl_fe_allocate(struct device *device, void __iomem *registers);
void rtl_fe_free(struct rtl_fe *fe);
int rtl_fe_add_port(struct rtl_fe *fe, struct net_device *netdev,
                    unsigned int source, unsigned int destination, unsigned int llid);
void rtl_fe_remove_port(struct rtl_fe *fe, struct net_device *netdev);
void rtl_fe_flush_port(struct rtl_fe *fe, struct net_device *netdev);
int rtl_fe_setup_tc(struct rtl_fe *fe, struct net_device *netdev,
                    enum tc_setup_type type, void *data);
int rtl_fe_enable(struct rtl_fe *fe);
int rtl_fe_disable(struct rtl_fe *fe);
ssize_t rtl_fe_state(struct rtl_fe *fe, char *buffer);
#endif
