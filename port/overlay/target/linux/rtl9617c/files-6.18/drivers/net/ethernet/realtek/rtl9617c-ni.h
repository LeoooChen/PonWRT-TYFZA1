/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_NI_H
#define RTL9617C_NI_H
#include <linux/device.h>
int rtl9617c_ni_report_configure(struct device *dev, unsigned int low, unsigned int high);
u32 rtl9617c_ni_report_state(struct device *dev);
#endif
