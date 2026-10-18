/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_P6_H
#define RTL9617C_FTTR_P6_H

/* 2 KiB QM buffer minus 32-byte headroom, full header and possible Ethernet FCS. */
#define RTL_FTTR_FRAME_BYTES 1996

struct platform_device;
struct reset_control;

struct rtl_fttr_p6 {
	struct device *dev;
	void __iomem *ni;
	void __iomem *qx;
	struct reset_control *reset;
	unsigned int mode;
	bool nway;
	bool configured;
	int error;
	u32 queue_status;
	u8 queue_completed, queue_failed;
	int queue_error, queue_cleanup_error;
};

int rtl_fttr_p6_init(struct platform_device *pdev, struct rtl_fttr_p6 *p6, void __iomem *ni);
int rtl_fttr_p6_configure(struct rtl_fttr_p6 *p6, unsigned int mode, bool nway);
void rtl_fttr_p6_stop(struct rtl_fttr_p6 *p6);
bool rtl_fttr_p6_link(struct rtl_fttr_p6 *p6);
int rtl_fttr_p6_flush_queues(struct rtl_fttr_p6 *p6);

#endif
