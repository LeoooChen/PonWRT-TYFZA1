/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_QM_H
#define RTL9617C_QM_H
#include <linux/dma-mapping.h>
#include <linux/netdevice.h>

#define RTL_QM_RING_BYTES 1024
#define RTL_QM_VOQS 8
#define RTL_QM_BUFFERS 256
#define RTL_QM_BUFFER_BYTES 2048
#define RTL_QM_MEMORY_BYTES (RTL_QM_RING_BYTES * RTL_QM_VOQS + RTL_QM_BUFFERS * RTL_QM_BUFFER_BYTES)

struct rtl_qm_rx {
	void __iomem *ni;
	void *memory;
	dma_addr_t memory_dma;
	unsigned int read[RTL_QM_VOQS];
	u32 last_address;
	u32 last_size;
	u32 last_header[2];
};

int rtl_qm_rx_alloc(struct device *dev, struct rtl_qm_rx *rx);
int rtl_qm_rx_start(struct rtl_qm_rx *rx, unsigned int port);
void rtl_qm_rx_stop(struct rtl_qm_rx *rx);
void rtl_qm_rx_free(struct device *dev, struct rtl_qm_rx *rx);
int rtl_qm_rx_poll(struct rtl_qm_rx *rx, struct napi_struct *napi, int budget);
ssize_t rtl_qm_rx_state(struct rtl_qm_rx *rx, char *buf);
#endif
