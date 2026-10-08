/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_QM_H
#define RTL9617C_QM_H
#include <linux/dma-mapping.h>
#include <linux/netdevice.h>
#include "rtl9617c-ni.h"
#include "rtl9617c-qm-refill.h"
#include "rtl9617c-qm-deepq.h"

#define RTL_QM_RING_BYTES 1024
#define RTL_QM_COMMAND_BYTES 8
#define RTL_QM_VOQS 8
#define RTL_QM_BUFFERS_PER_EQ 256
#define RTL_QM_BUFFERS (2 * RTL_QM_BUFFERS_PER_EQ)
#define RTL_QM_BUFFER_BYTES 2048
#define RTL_QM_MEMORY_BYTES (RTL_QM_RING_BYTES * RTL_QM_VOQS + RTL_QM_BUFFERS * RTL_QM_BUFFER_BYTES)

struct rtl_qm_rx {
	void __iomem *ni;
	void __iomem *axi_reorder;
	void __iomem *block_reset;
	void *memory;
	dma_addr_t memory_dma;
	void *deepq_memory;
	dma_addr_t deepq_dma;
	bool deepq_configured;
	unsigned int read[RTL_QM_VOQS];
	u32 last_address;
	u32 last_size;
	u32 last_header[2];
	u32 trace[32][7];
	unsigned int trace_count;
	unsigned int lan_source;
	struct rtl_qm_refill refill;
	struct rtl9617c_ni_endpoint __rcu *pon;
};

int rtl_qm_rx_alloc(struct device *dev, struct rtl_qm_rx *rx);
int rtl_qm_rx_start(struct rtl_qm_rx *rx, unsigned int port);
void rtl_qm_rx_stop(struct rtl_qm_rx *rx);
void rtl_qm_rx_free(struct device *dev, struct rtl_qm_rx *rx);
int rtl_qm_rx_poll(struct rtl_qm_rx *rx, struct napi_struct *napi, int budget);
ssize_t rtl_qm_rx_state(struct rtl_qm_rx *rx, char *buf);
ssize_t rtl_qm_rx_trace(struct rtl_qm_rx *rx, char *buf);
ssize_t rtl_qm_rx_pool(struct rtl_qm_rx *rx, char *buf);
ssize_t rtl_qm_deepq_state(struct rtl_qm_rx *rx, char *buf);
#endif
