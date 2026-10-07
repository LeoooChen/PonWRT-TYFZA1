// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Taurus PON platform binding and MAC state observation.
 * Register definitions: GPL Cortina-Access 2015 aal_pon.c/aal_mpcp.c,
 * Realtek Taurus register header, and the TYFZA1 stock DTB NI resources.
 */
#include <linux/bitfield.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include "rtl9617c-pon.h"

#define PON_EPON_MODE BIT(0)
#define EPON_STATUS 0x0010
#define MPCP_REGISTERED 0x2014

static ssize_t state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	u32 control = readl(pon->control);
	u32 status = 0, registered = 0;

	if (!(control & PON_EPON_MODE))
		return sysfs_emit(buf, "family=gpon control=%08x serdes_reset_n=%u register_reset_n=%u\n",
			control, !!(control & BIT(1)), !!(control & BIT(2)));
	status = readl(pon->mac + EPON_STATUS);
	registered = readl(pon->mac + MPCP_REGISTERED);
	return sysfs_emit(buf,
		"family=%s control=%08x serdes_reset_n=%u register_reset_n=%u epon_status=%08x signal_loss=%u pcs10g_sync=%u pcs1g_sync=%u registered_llids=%08x\n",
		control & PON_EPON_MODE ? "epon" : "gpon",
		control, !!(control & BIT(1)), !!(control & BIT(2)), status,
		!!(status & BIT(0)), !!(status & BIT(1)), !!(status & BIT(2)), registered);
}
static DEVICE_ATTR_RO(state);

static ssize_t serdes_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;

	mutex_lock(&pon->lock);
	size = rtl_pon_serdes_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t serdes_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	unsigned int mode;
	int ret;

	for (mode = RTL_PON_EPON_1G; mode <= RTL_PON_EPON_BI10G; mode++)
		if (sysfs_streq(buf, rtl_pon_mode_name(mode)))
			break;
	if (mode > RTL_PON_EPON_BI10G)
		return -EINVAL;
	ret = rtl_pon_serdes_configure(pon, mode);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(serdes);

static ssize_t serdes_registers_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_serdes_registers(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}
static DEVICE_ATTR_RO(serdes_registers);

static ssize_t optical_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	u32 status = readl(pon->optical_status);
	return sysfs_emit(buf, "control=%08x status=%08x rx_los=%u module_absent=%u tx_signal_detect=%u tx_fault=%u\n",
		readl(pon->optical_control), status, !!(status & BIT(3)), !!(status & BIT(2)),
		!!(status & BIT(1)), !!(status & BIT(0)));
}
static DEVICE_ATTR_RO(optical);

static ssize_t mac_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_epon_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t mac_store(struct device *dev, struct device_attribute *attr,
			const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool prepare;
	int ret = kstrtobool(buf, &prepare);
	if (ret)
		return ret;
	if (!prepare)
		return -EINVAL;
	ret = rtl_pon_epon_prepare(pon);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(mac);

static struct attribute *rtl9617c_pon_attrs[] = {
	&dev_attr_state.attr,
	&dev_attr_serdes.attr,
	&dev_attr_serdes_registers.attr,
	&dev_attr_optical.attr,
	&dev_attr_mac.attr,
	NULL,
};
ATTRIBUTE_GROUPS(rtl9617c_pon);

static int rtl9617c_pon_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rtl9617c_pon *pon;

	pon = devm_kzalloc(dev, sizeof(*pon), GFP_KERNEL);
	if (!pon)
		return -ENOMEM;
	pon->dev = dev;
	pon->mode = RTL_PON_EPON_D10G;
	mutex_init(&pon->lock);
	rtl_pon_serdes_init_work(pon);
	pon->control = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(pon->control))
		return PTR_ERR(pon->control);
	pon->mac = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(pon->mac))
		return PTR_ERR(pon->mac);
	pon->epon_control = devm_platform_ioremap_resource(pdev, 2);
	if (IS_ERR(pon->epon_control))
		return PTR_ERR(pon->epon_control);
	pon->serdes_init = devm_platform_ioremap_resource(pdev, 3);
	if (IS_ERR(pon->serdes_init))
		return PTR_ERR(pon->serdes_init);
	of_property_read_u32(dev->of_node, "realtek,burst-polarity", &pon->burst_polarity);
	if (pon->burst_polarity > 1)
		return -EINVAL;
	pon->optical_control = devm_platform_ioremap_resource(pdev, 4);
	if (IS_ERR(pon->optical_control))
		return PTR_ERR(pon->optical_control);
	pon->optical_status = devm_platform_ioremap_resource(pdev, 5);
	if (IS_ERR(pon->optical_status))
		return PTR_ERR(pon->optical_status);
	of_property_read_u32(dev->of_node, "realtek,los-invert", &pon->los_invert);
	if (pon->los_invert > 1)
		return -EINVAL;
	platform_set_drvdata(pdev, pon);
	dev_info(dev, "native PON MAC binding: global control=%08x\n", readl(pon->control));
	return 0;
}

static void rtl9617c_pon_remove(struct platform_device *pdev)
{
	rtl_pon_serdes_stop_work(platform_get_drvdata(pdev));
}

static const struct of_device_id rtl9617c_pon_of_match[] = {
	{ .compatible = "realtek,rtl9617c-pon" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl9617c_pon_of_match);

static struct platform_driver rtl9617c_pon_driver = {
	.probe = rtl9617c_pon_probe,
	.remove = rtl9617c_pon_remove,
	.driver = {
		.name = "rtl9617c-pon",
		.of_match_table = rtl9617c_pon_of_match,
		.dev_groups = rtl9617c_pon_groups,
	},
};
module_platform_driver(rtl9617c_pon_driver);
MODULE_DESCRIPTION("Native RTL9617C PON MAC platform binding");
MODULE_LICENSE("GPL");
