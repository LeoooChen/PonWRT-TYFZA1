/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_H
#define RTL9617C_PON_H

#include <linux/device.h>
#include <linux/mutex.h>
#include <linux/workqueue.h>

enum rtl_pon_mode {
	RTL_PON_EPON_1G,
	RTL_PON_EPON_D10G,
	RTL_PON_EPON_BI10G,
};

struct rtl9617c_pon {
	struct device *dev;
	void __iomem *control;
	void __iomem *mac;
	void __iomem *epon_control;
	void __iomem *serdes_init;
	void __iomem *optical_control;
	void __iomem *optical_status;
	struct mutex lock;
	struct delayed_work serdes_work;
	enum rtl_pon_mode mode;
	unsigned int burst_polarity;
	unsigned int los_invert;
	unsigned int profile_rows;
	bool serdes_configured;
	bool gearbox_ready;
	u32 serdes_status;
	int serdes_error;
	int sync_result;
	bool rx_synced;
	bool mac_prepared;
	int mac_error;
};

void rtl_pon_update(void __iomem *reg, u32 mask, u32 value);
int rtl_pon_serdes_configure(struct rtl9617c_pon *pon, enum rtl_pon_mode mode);
void rtl_pon_serdes_init_work(struct rtl9617c_pon *pon);
void rtl_pon_serdes_stop_work(struct rtl9617c_pon *pon);
const char *rtl_pon_mode_name(enum rtl_pon_mode mode);
ssize_t rtl_pon_serdes_state(struct rtl9617c_pon *pon, char *buf);
ssize_t rtl_pon_serdes_registers(struct rtl9617c_pon *pon, char *buf);
int rtl_pon_epon_prepare(struct rtl9617c_pon *pon);
ssize_t rtl_pon_epon_state(struct rtl9617c_pon *pon, char *buf);
#endif
