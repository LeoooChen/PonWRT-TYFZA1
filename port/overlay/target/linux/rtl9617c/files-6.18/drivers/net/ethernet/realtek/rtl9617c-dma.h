/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_DMA_H
#define RTL9617C_DMA_H

#include <linux/dma-mapping.h>
#include <linux/netdevice.h>

#define RTL_DMA_DEPTH 6
#define RTL_DMA_COUNT (1U << RTL_DMA_DEPTH)
#define RTL_DMA_BUFFER_SIZE 2048

struct rtl_dma_descriptor {
	__le32 address;
	__le32 control;
};

struct rtl_dma_tx {
	void __iomem *registers;
	struct rtl_dma_descriptor *descriptors;
	dma_addr_t memory_dma;
	void *memory;
	unsigned int read;
	unsigned int write;
	unsigned int lengths[RTL_DMA_COUNT];
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
			   struct net_device *dev);
ssize_t rtl_dma_tx_state(struct rtl_dma_tx *tx, char *buf);

#endif
