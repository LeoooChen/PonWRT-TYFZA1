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
	struct device *ni_dev;
	void __iomem *control;
	void __iomem *mac;
	void __iomem *epon_control;
	void __iomem *serdes_init;
	void __iomem *optical_control;
	void __iomem *optical_status;
	void __iomem *top_irq;
	struct mutex lock;
	struct delayed_work serdes_work;
	struct delayed_work mpcp_work;
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
	u8 onu_mac[6];
	unsigned int llid_count;
	bool mpcp_configured;
	bool mpcp_enabled;
	bool pdc_configured;
	int pdc_error;
	bool puc_configured;
	int puc_error;
	u32 registered;
	u32 waiting;
	u32 registration_attempts;
	u32 registration_events;
	u32 register_frames;
	u32 mpcp_irq_count;
	u32 last_mpcp_irq;
	unsigned long silence_until[32];
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
int rtl_pon_mpcp_init(struct rtl9617c_pon *pon, int irq);
void rtl_pon_mpcp_stop(struct rtl9617c_pon *pon);
int rtl_pon_mpcp_configure(struct rtl9617c_pon *pon);
ssize_t rtl_pon_mpcp_state(struct rtl9617c_pon *pon, char *buf);
ssize_t rtl_pon_llid_table(struct rtl9617c_pon *pon, char *buf);
int rtl_pon_llid_update(struct rtl9617c_pon *pon, bool add, unsigned int index,
			 unsigned int mac, u16 llid, bool multicast);
int rtl_pon_pdc_configure(struct rtl9617c_pon *pon);
ssize_t rtl_pon_pdc_state(struct rtl9617c_pon *pon, char *buf);
int rtl_pon_puc_configure(struct rtl9617c_pon *pon);
ssize_t rtl_pon_puc_state(struct rtl9617c_pon *pon, char *buf);
#endif
