/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_P6_H
#define RTL9617C_FTTR_P6_H

#include "rtl9617c-fttr-frame.h"

struct platform_device;
struct reset_control;

struct rtl_fttr_queue_state {
	u32 status;
	u8 completed, failed;
	int error, cleanup_error;
};

struct rtl_fttr_p6 {
	struct device *dev;
	void __iomem *ni;
	void __iomem *qx;
	struct reset_control *reset;
	unsigned int mode;
	bool nway;
	bool configured;
	int error;
	/* DEEPQ port14 first, then physical P6. */
	struct rtl_fttr_queue_state l3_queue, l2_queue;
};

int rtl_fttr_p6_init(struct platform_device *pdev, struct rtl_fttr_p6 *p6, void __iomem *ni);
int rtl_fttr_p6_configure(struct rtl_fttr_p6 *p6, unsigned int mode, bool nway);
void rtl_fttr_p6_stop(struct rtl_fttr_p6 *p6);
bool rtl_fttr_p6_link(struct rtl_fttr_p6 *p6);
int rtl_fttr_p6_flush_queues(struct rtl_fttr_p6 *p6);

#endif
