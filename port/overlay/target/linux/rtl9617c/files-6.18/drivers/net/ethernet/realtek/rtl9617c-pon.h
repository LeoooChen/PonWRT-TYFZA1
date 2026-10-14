/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_H
#define RTL9617C_PON_H

#include <linux/device.h>
#include <linux/bitmap.h>
#include <linux/gpio/consumer.h>
#include <linux/mutex.h>
#include <linux/workqueue.h>
#include "rtl9617c-ni.h"
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-mpcp-backoff.h"
#include "rtl9617c-pon-path.h"

struct rtl_itu_path_state {
	struct rcu_head rcu;
	struct rtl_itu_paths paths;
};

struct rtl9617c_pon {
	struct device *dev;
	struct device *ni_dev;
	struct device *bosa_dev;
	struct gpio_desc *tx_disable;
	void __iomem *control;
	void __iomem *mac;
	void __iomem *epon_control;
	void __iomem *gpon_control;
	void __iomem *serdes_init;
	void __iomem *optical_control;
	void __iomem *optical_status;
	void __iomem *optical_irq;
	void __iomem *top_irq;
	struct mutex lock;
	/* Control transactions precede state lock. Work/IRQ threads use only lock;
	 * cancel_delayed_work_sync must run without holding that state lock. */
	struct mutex control_lock;
	struct delayed_work serdes_work;
	struct delayed_work mpcp_work;
	struct delayed_work xgpon_recovery_work;
	bool xgpon_recover;
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
	struct rtl9617c_ni_endpoint oam_endpoint;
	struct net_device __rcu *oam_devices[32];
	struct net_device __rcu *data_devices[32];
	u8 pdc_source[64];
	bool traffic_enabled;
	unsigned int last_oam_llid;
	unsigned int last_oam_source;
	u32 registered;
	u32 waiting;
	u32 registration_attempts;
	u32 registration_events;
	u32 register_frames;
	u32 mpcp_irq_count;
	u32 last_mpcp_irq;
	u32 optical_irq_count;
	u32 last_optical_irq;
	int mpcp_irq;
	struct rtl_mpcp_backoff backoff;
	bool fe_enabled;
	u64 ploam_rx, ploam_tx, ploam_mic_errors;
	u8 gpon_serial[8], gpon_password[10];
	bool gpon_identity_configured;
	bool gpon_enabled, gpon_omcc_ready, gpon_ds_fec;
	u8 gpon_last_state, gpon_omcc_onu_id;
	u16 gpon_omcc_gem;
	u64 gpon_irq_count, gpon_key_switches;
	u32 gpon_last_irq;
	int gpon_activation_error;
	struct net_device *omci_device;
	u8 xgpon_pending_ploam[48];
	u8 xgpon_serial[8], xgpon_registration[36];
	bool xgpon_identity_configured;
	u32 xgpon_pending_status;
	bool xgpon_pending_valid;
	bool xgpon_enabled, xgpon_omcc_ready, xgpon_key_ready;
	u8 xgpon_last_state, xgpon_tag[8];
	u16 xgpon_omcc_id;
	u32 xgpon_last_irq;
	u32 xgpon_last_transitions;
	u64 xgpon_deactivations;
	u64 xgpon_irq_count;
	int xgpon_activation_error;
	struct crypto_shash *omci_cmac, *omci_kdf;
	spinlock_t omci_key_lock;
	u8 xgpon_kek[16], xgpon_ploam_ik[16], xgpon_encrypted_key[2][16], xgpon_key_name[2][16];
	u8 xgpon_key_valid, xgpon_key_state, xgpon_active_key;
	u8 omci_master_key[16];
	bool omci_master_key_valid;
	u64 xgpon_key_reports, xgpon_key_tk4, xgpon_key_tk5;
	struct rtl_itu_path_state __rcu *itu_paths;
	bool itu_paths_ready;
	int itu_paths_error;
	u64 itu_path_epoch;
	u64 itu_line_epoch;
	u16 itu_alloc_ids[32];
	u32 itu_alloc_valid;
	DECLARE_BITMAP(itu_gpon_gems, 4096);
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
ssize_t rtl_pon_itu_state(struct rtl9617c_pon *pon, char *buf);
ssize_t rtl_pon_gpon_identity(struct rtl9617c_pon *pon, const char *buf, size_t count);
int rtl_pon_gpon_prepare_locked(struct rtl9617c_pon *pon);
ssize_t rtl_pon_xgpon_identity(struct rtl9617c_pon *pon, const char *buf, size_t count);
int rtl_pon_xgpon_prepare_locked(struct rtl9617c_pon *pon);
int rtl_pon_xgpon_activation_locked(struct rtl9617c_pon *pon, bool enable);
void rtl_pon_xgpon_stop_locked(struct rtl9617c_pon *pon);
void rtl_pon_xgpon_irq(struct rtl9617c_pon *pon);
void rtl_pon_xgpon_recovery(struct work_struct *work);
int rtl_pon_xgpon_omci_key(struct rtl9617c_pon *pon, const u8 tag[8]);
int rtl_pon_xgpon_ack_update(struct rtl9617c_pon *pon);
void rtl_pon_xgpon_key_irq(struct rtl9617c_pon *pon, u32 interrupts, u32 control);
ssize_t rtl_pon_omci_master_key(struct rtl9617c_pon *pon, const char *buf, size_t count);
ssize_t rtl_pon_omci_broadcast_key(struct rtl9617c_pon *pon, const char *buf, size_t count);
ssize_t rtl_pon_itu_paths_write(struct rtl9617c_pon *pon, const char *buf, size_t count);
ssize_t rtl_pon_itu_paths_state(struct rtl9617c_pon *pon, char *buf);
void rtl_pon_itu_carrier(struct rtl9617c_pon *pon);
void rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *pon);
void rtl_pon_itu_paths_remove(struct rtl9617c_pon *pon);
netdev_tx_t rtl_pon_itu_data_transmit(struct rtl9617c_pon *pon, struct sk_buff *skb, struct net_device *dev);
void rtl_pon_itu_data_receive(struct rtl9617c_pon *pon, struct napi_struct *napi,
	struct sk_buff *skb, unsigned int source);
int rtl_pon_gpon_activation_locked(struct rtl9617c_pon *pon, bool enable);
void rtl_pon_gpon_stop_locked(struct rtl9617c_pon *pon);
void rtl_pon_gpon_irq(struct rtl9617c_pon *pon);
int rtl_pon_omci_init(struct rtl9617c_pon *pon);
void rtl_pon_omci_remove(struct rtl9617c_pon *pon);
void rtl_pon_omci_receive(struct rtl9617c_pon *pon, struct napi_struct *napi,
			  struct sk_buff *skb, unsigned int source);
ssize_t rtl_pon_ploam_receive(struct rtl9617c_pon *pon, char *buf, size_t count);
ssize_t rtl_pon_ploam_transmit(struct rtl9617c_pon *pon, const char *buf, size_t count,
			     bool default_key);
int rtl_pon_epon_traffic(struct rtl9617c_pon *pon, bool enable);
int rtl_pon_mpcp_init(struct rtl9617c_pon *pon, int irq);
void rtl_pon_mpcp_stop(struct rtl9617c_pon *pon);
int rtl_pon_mpcp_configure(struct rtl9617c_pon *pon);
ssize_t rtl_pon_mpcp_state(struct rtl9617c_pon *pon, char *buf);
ssize_t rtl_pon_llid_table(struct rtl9617c_pon *pon, char *buf);
int rtl_pon_llid_update(struct rtl9617c_pon *pon, bool add, unsigned int index,
			 unsigned int mac, u16 llid, bool multicast);
int rtl_pon_pdc_configure(struct rtl9617c_pon *pon);
int rtl_pon_pdc_refresh(struct rtl9617c_pon *pon);
ssize_t rtl_pon_pdc_state(struct rtl9617c_pon *pon, char *buf);
int rtl_pon_fe_configure_locked(struct rtl9617c_pon *pon,bool enable);
int rtl_pon_puc_configure(struct rtl9617c_pon *pon);
ssize_t rtl_pon_puc_state(struct rtl9617c_pon *pon, char *buf);
int rtl_pon_oam_init(struct rtl9617c_pon *pon);
void rtl_pon_oam_remove(struct rtl9617c_pon *pon);
void rtl_pon_oam_carrier(struct rtl9617c_pon *pon);
int rtl_pon_oam_resize(struct rtl9617c_pon *pon, unsigned int count);
void rtl_pon_oam_address(struct rtl9617c_pon *pon);

/* Internal control steps: caller owns control_lock, never acquire it again. */
int rtl_pon_serdes_configure_locked(struct rtl9617c_pon *pon, enum rtl_pon_mode mode);
int rtl_pon_epon_prepare_locked(struct rtl9617c_pon *pon);
int rtl_pon_epon_traffic_locked(struct rtl9617c_pon *pon, bool enable);
void rtl_pon_mpcp_stop_locked(struct rtl9617c_pon *pon);
int rtl_pon_mpcp_configure_locked(struct rtl9617c_pon *pon);
int rtl_pon_pdc_configure_locked(struct rtl9617c_pon *pon);
int rtl_pon_puc_configure_locked(struct rtl9617c_pon *pon);
#endif
