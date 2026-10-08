/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_BOSA_H
#define RTL9617C_BOSA_H
#include <linux/device.h>
int rtl9617c_bosa_prepare(struct device *device);
ssize_t rtl9617c_bosa_state(struct device *device, char *buf);
#endif
