/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_NI_H
#define RTL9617C_NI_H
#include <linux/device.h>
#include <linux/netdevice.h>

/* receive source: physical/logical LSPID in low eight bits, POL_ID flow
 * in the upper bits. Native PDC uses the flow field for its business LLID. */
struct rtl9617c_ni_endpoint {
	struct net_device *netdev;
	void (*receive)(struct rtl9617c_ni_endpoint *endpoint, struct napi_struct *napi,
			struct sk_buff *skb, unsigned int source);
	void (*wake)(struct rtl9617c_ni_endpoint *endpoint);
};

struct rtl9617c_ni_frame {
	u32 header_high;
	u32 header_low;
	const u8 *prefix;
	unsigned int prefix_length;
};

int rtl9617c_ni_report_configure(struct device *dev, unsigned int low, unsigned int high);
u32 rtl9617c_ni_report_state(struct device *dev);
int rtl9617c_ni_pon_attach(struct device *dev, struct rtl9617c_ni_endpoint *endpoint);
void rtl9617c_ni_pon_detach(struct device *dev);
int rtl9617c_ni_open(struct device *dev);
void rtl9617c_ni_close(struct device *dev, struct net_device *owner);
netdev_tx_t rtl9617c_ni_transmit(struct device *dev, struct net_device *owner,
			       struct sk_buff *skb, const struct rtl9617c_ni_frame *frame);
int rtl9617c_ni_oam_configure(struct device *dev);
#endif
