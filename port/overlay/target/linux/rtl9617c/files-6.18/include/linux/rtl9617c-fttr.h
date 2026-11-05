/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_RTL9617C_FTTR_H
#define _LINUX_RTL9617C_FTTR_H

struct device;

/* Hold an already configured, closed P6 while loading its downstream FPGA.
 * The caller owns a device link/reference to NI for the complete operation.
 */
int rtl9617c_ni_fttr_load_begin(struct device *dev);
void rtl9617c_ni_fttr_load_end(struct device *dev, int error);

#endif
