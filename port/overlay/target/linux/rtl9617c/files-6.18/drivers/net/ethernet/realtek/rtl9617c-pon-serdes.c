// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native PON SerDes initialization for Taurus EPON and ITU modes.
 * Sequence: Cortina-Access 2015 GPL-2.0+ aal_psds.c, checked against
 * TYFZA1 V1.0.5 ca-ne.ko aal_psds_{init,out_of_reset} machine code.
 * Register parameter firmware is generated from this device's stock tables.
 */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/unaligned.h>

#include "rtl9617c-pon.h"

#define PSDS_MODE 0xa02c
#define PSDS_STATUS 0xa060
#define PSDS_GBOX 0xa064
#define PSDS_ACCESS 0xa0cc
#define PSDS_DATAIN 0xa0d0
#define PSDS_DATAOUT 0xa0d4
#define PSDS_POWER BIT(5)
#define PSDS_CSR_RESET_N BIT(2)
#define PSDS_GBOX_RESET_N BIT(1)
#define PSDS_SYNC_MASK (BIT(0) | BIT(10) | BIT(11) | BIT(12) | BIT(15))
#define PSDS_SYNC_GOAL (BIT(10) | BIT(11) | BIT(12) | BIT(15))

const char *rtl_pon_mode_name(enum rtl_pon_mode mode)
{
	return rtl_pon_modes[mode].name;
}

void rtl_pon_update(void __iomem *reg, u32 mask, u32 value)
{
	writel((readl(reg) & ~mask) | value, reg);
}

static int rtl_pon_profile_validate(const struct firmware *fw)
{
	u32 rows;
	unsigned int i;

	if (fw->size < 12 || memcmp(fw->data, "TPON", 4) ||
	    get_unaligned_le32(fw->data + 4) != 1)
		return -EINVAL;
	rows = get_unaligned_le32(fw->data + 8);
	if (!rows || rows > 512 || fw->size != 12 + rows * 12)
		return -EINVAL;
	for (i = 0; i < rows; i++) {
		u32 offset = get_unaligned_le32(fw->data + 12 + i * 12);
		u32 delay = get_unaligned_le32(fw->data + 20 + i * 12);

		if (offset >= 0xc000 || (offset & 3) || delay > 1000000)
			return -EINVAL;
	}
	return 0;
}

static void rtl_pon_profile_load(struct rtl9617c_pon *pon, const struct firmware *fw)
{
	unsigned int i;

	pon->profile_rows = get_unaligned_le32(fw->data + 8);
	for (i = 0; i < pon->profile_rows; i++) {
		const u8 *record = fw->data + 12 + i * 12;
		u32 delay = get_unaligned_le32(record + 8);

		writel(get_unaligned_le32(record + 4), pon->mac + get_unaligned_le32(record));
		if (delay >= 1000)
			usleep_range(delay, delay + 100);
		else
			udelay(delay);
	}
}

static int rtl_pon_burst_polarity(struct rtl9617c_pon *pon, unsigned int page)
{
	u32 value;
	int ret;

	writel(BIT(31) | (page << 5) | 22, pon->mac + PSDS_ACCESS);
	ret = readl_poll_timeout(pon->mac + PSDS_ACCESS, value, !(value & BIT(31)), 1, 10000);
	if (ret)
		return ret;
	value = readl(pon->mac + PSDS_DATAOUT);
	value = (value & ~BIT(9)) | (pon->burst_polarity << 9);
	writel(value, pon->mac + PSDS_DATAIN);
	writel(BIT(31) | BIT(30) | (page << 5) | 22, pon->mac + PSDS_ACCESS);
	return readl_poll_timeout(pon->mac + PSDS_ACCESS, value, !(value & BIT(31)), 1, 10000);
}

static void rtl_pon_gearbox_release(struct rtl9617c_pon *pon)
{
	rtl_pon_update(pon->control, PSDS_GBOX_RESET_N, 0);
	msleep(1);
	rtl_pon_update(pon->control, PSDS_GBOX_RESET_N, PSDS_GBOX_RESET_N);
	msleep(100);
	pon->gearbox_ready = true;
}

static void rtl_pon_serdes_work(struct work_struct *work)
{
	struct rtl9617c_pon *pon = container_of(to_delayed_work(work), struct rtl9617c_pon, serdes_work);

	mutex_lock(&pon->lock);
	pon->serdes_status = readl(pon->mac + PSDS_STATUS);
	if ((pon->serdes_status & PSDS_SYNC_MASK) == PSDS_SYNC_GOAL) {
		if (!pon->rx_synced)
			dev_info(pon->dev, "%s SerDes RX clocks synchronized\n", rtl_pon_mode_name(pon->mode));
		pon->sync_result = 0;
		pon->rx_synced = true;
	} else {
		pon->rx_synced = false;
		pon->sync_result = -ENOLINK;
	}
	if (pon->serdes_configured)
		schedule_delayed_work(&pon->serdes_work, HZ);
	mutex_unlock(&pon->lock);
}

void rtl_pon_serdes_init_work(struct rtl9617c_pon *pon)
{
	INIT_DELAYED_WORK(&pon->serdes_work, rtl_pon_serdes_work);
	INIT_DELAYED_WORK(&pon->xgpon_recovery_work, rtl_pon_xgpon_recovery);
}

void rtl_pon_serdes_stop_work(struct rtl9617c_pon *pon)
{
	cancel_delayed_work_sync(&pon->serdes_work);
	cancel_delayed_work_sync(&pon->xgpon_recovery_work);
}

int rtl_pon_serdes_configure_locked(struct rtl9617c_pon *pon, enum rtl_pon_mode mode)
{
	const struct firmware *fw;
	const struct rtl_pon_mode_configuration *configuration = &rtl_pon_modes[mode];
	bool epon = rtl_pon_mode_is_epon(mode);
	int ret;

	lockdep_assert_held(&pon->control_lock);
	ret = request_firmware_direct(&fw, configuration->profile, pon->dev);
	if (ret)
		return ret;
	ret = rtl_pon_profile_validate(fw);
	if (ret)
		goto release;
	ret = rtl_pon_stop_locked(pon);
	if (!ret) {
		mutex_lock(&pon->lock);
		ret = rtl_pon_itu_paths_invalidate(pon, mode);
		mutex_unlock(&pon->lock);
	}
	if (ret) {
		mutex_lock(&pon->lock);
		pon->serdes_error = ret;
		mutex_unlock(&pon->lock);
		goto release;
	}
	cancel_delayed_work_sync(&pon->serdes_work);
	mutex_lock(&pon->lock);
	pon->serdes_configured = false;
	pon->gearbox_ready = false;
	pon->rx_synced = false;
	pon->mac_prepared = false;
	pon->puc_configured = false;
	pon->pdc_configured = false;
	pon->xgpon_pending_valid = false;
	pon->mode = mode;
	rtl_pon_update(pon->optical_control, BIT(30), pon->los_invert << 30);
	rtl_pon_update(pon->control, BIT(0), epon ? BIT(0) : 0);
	if (epon)
		rtl_pon_update(pon->epon_control, GENMASK(17, 16), configuration->epon_onu_mode << 16);
	else
		rtl_pon_update(pon->gpon_control, BIT(8) | BIT(9),
			mode == RTL_PON_GPON ? 0 : BIT(9) | (mode == RTL_PON_XGSPON ? BIT(8) : 0));
	rtl_pon_update(pon->serdes_init, PSDS_POWER, 0);
	msleep(1);
	rtl_pon_update(pon->control, PSDS_CSR_RESET_N, 0);
	msleep(1);
	rtl_pon_update(pon->control, PSDS_CSR_RESET_N, PSDS_CSR_RESET_N);
	msleep(100);
	rtl_pon_update(pon->mac + PSDS_MODE, GENMASK(10, 0), configuration->sds_mode);
	udelay(10);
	rtl_pon_profile_load(pon, fw);
	ret = rtl_pon_burst_polarity(pon, configuration->burst_page);
	if (ret)
		goto unlock;
	if (!epon) {
		/* The GPL ITU out-of-reset sequence disables automatic LOS resets. */
		rtl_pon_update(pon->epon_control, BIT(0), BIT(0));
		rtl_pon_update(pon->mac + 0x1c8, BIT(31), BIT(31));
	}
	rtl_pon_update(pon->serdes_init, PSDS_POWER, PSDS_POWER);
	msleep(100);
	pon->sync_result = readl_poll_timeout(pon->mac + PSDS_STATUS, pon->serdes_status,
		(pon->serdes_status & PSDS_SYNC_MASK) == PSDS_SYNC_GOAL, 1000, 1000000);
	if (!epon && !pon->sync_result)
		rtl_pon_update(pon->epon_control, BIT(0), 0);
	/* The original out_of_reset sequence resets the gearbox even on LOS. */
	rtl_pon_gearbox_release(pon);
	pon->serdes_configured = true;
	pon->rx_synced = !pon->sync_result;
	schedule_delayed_work(&pon->serdes_work, HZ);
	rtl_pon_update(pon->mac + PSDS_GBOX, GENMASK(7, 4), BIT(4) | BIT(6));
	msleep(1);
	dev_info(pon->dev, "%s SerDes: %u profile writes, status=%08x\n",
		 rtl_pon_mode_name(mode), pon->profile_rows, pon->serdes_status);
unlock:
	pon->serdes_error = ret;
	mutex_unlock(&pon->lock);
release:
	release_firmware(fw);
	return ret;
}

ssize_t rtl_pon_serdes_state(struct rtl9617c_pon *pon, char *buf)
{
	u32 status = readl(pon->mac + PSDS_STATUS);

	return sysfs_emit(buf,
		"mode=%s configured=%u profile_rows=%u error=%d sync_result=%d status=%08x rx_los=%u rx_clock=%u tx_clock=%u mpcp_clock=%u ber_notify=%u gearbox_ready=%u mode_reg=%08x gearbox_reg=%08x init_reg=%08x\n",
		rtl_pon_mode_name(pon->mode), pon->serdes_configured, pon->profile_rows,
		pon->serdes_error, pon->sync_result, status, !!(status & BIT(0)), !!(status & BIT(10)),
		!!(status & BIT(11)), !!(status & BIT(12)), !!(status & BIT(15)), pon->gearbox_ready,
		readl(pon->mac + PSDS_MODE), readl(pon->mac + PSDS_GBOX), readl(pon->serdes_init));
}

ssize_t rtl_pon_serdes_registers(struct rtl9617c_pon *pon, char *buf)
{
	static const unsigned int addresses[] = {0x400, 0x420, 0x424, 0x429, 0x780, 0x796, 0x79f};
	ssize_t length = 0;
	unsigned int i;
	u32 status;
	int ret;

	for (i = 0; i < ARRAY_SIZE(addresses); i++) {
		writel(BIT(31) | addresses[i], pon->mac + PSDS_ACCESS);
		ret = readl_poll_timeout(pon->mac + PSDS_ACCESS, status, !(status & BIT(31)), 1, 10000);
		if (ret)
			return ret;
		length += scnprintf(buf + length, PAGE_SIZE - length, "%04x=%04x\n",
			addresses[i], readl(pon->mac + PSDS_DATAOUT));
	}
	return length;
}
