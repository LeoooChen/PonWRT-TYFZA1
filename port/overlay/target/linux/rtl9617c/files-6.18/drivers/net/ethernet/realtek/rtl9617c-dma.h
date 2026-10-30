/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_DMA_H
#define RTL9617C_DMA_H

#include <linux/dma-mapping.h>
#include <linux/netdevice.h>
#include "rtl9617c-ni.h"

#define RTL_DMA_DEPTH 6
#define RTL_DMA_COUNT (1U << RTL_DMA_DEPTH)
#define RTL_DMA_BUFFER_SIZE 2048

struct rtl_dma_descriptor {
	__le32 address;
	__le32 control;
};

struct rtl_dma_tx {
	bool profile;
	u64 wifi_packets, wifi_copy_bytes, wifi_prepare_ns;
	void __iomem *registers;
	void __iomem *axi_reorder;
	struct rtl_dma_descriptor *descriptors;
	dma_addr_t memory_dma;
	void *memory;
	unsigned int read;
	unsigned int write;
	unsigned int lengths[RTL_DMA_COUNT];
	struct net_device *owners[RTL_DMA_COUNT];
	unsigned int port;
	unsigned int vp;
};

/* HP=11, direct LAN, one linear frame, software checksums. */
static inline u32 rtl_dma_direct_lan(unsigned int port, unsigned int length)
{
	return 0x81400000U | (length << 8) | (port << 1);
}

int rtl_dma_tx_alloc(struct device *dev, struct rtl_dma_tx *tx);
int rtl_dma_tx_start(struct rtl_dma_tx *tx);
void rtl_dma_tx_stop(struct rtl_dma_tx *tx);
void rtl_dma_tx_free(struct device *dev, struct rtl_dma_tx *tx);
void rtl_dma_tx_complete(struct rtl_dma_tx *tx, struct net_device *dev);
netdev_tx_t rtl_dma_tx_frame(struct rtl_dma_tx *tx, struct sk_buff *skb,
			   struct net_device *dev, const struct rtl9617c_ni_frame *frame);
int rtl_dma_tx_wifi(struct rtl_dma_tx *tx, struct net_device *dev,
		    struct sk_buff *skb, unsigned int id);
void rtl_dma_tx_forget(struct rtl_dma_tx *tx, struct net_device *dev);
static inline bool rtl_dma_tx_available(struct rtl_dma_tx *tx)
{
	return ((tx->read - tx->write - 1) & (RTL_DMA_COUNT - 1)) > 2;
}
ssize_t rtl_dma_tx_state(struct rtl_dma_tx *tx, char *buf);

#endif
