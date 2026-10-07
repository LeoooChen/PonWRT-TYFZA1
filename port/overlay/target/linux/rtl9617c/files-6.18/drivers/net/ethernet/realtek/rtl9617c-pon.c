// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Taurus PON platform binding and MAC state observation.
 * Register definitions: GPL Cortina-Access 2015 aal_pon.c/aal_mpcp.c,
 * Realtek Taurus register header, and the TYFZA1 stock DTB NI resources.
 */
#include <linux/bitfield.h>
#include <linux/io.h>
#include <linux/etherdevice.h>
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

static ssize_t onu_mac_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	return sysfs_emit(buf, "%pM\n", pon->onu_mac);
}

static ssize_t onu_mac_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	u8 mac[ETH_ALEN];
	if (!mac_pton(buf, mac) || !is_valid_ether_addr(mac))
		return -EINVAL;
	mutex_lock(&pon->lock);
	if (pon->mpcp_enabled) {
		mutex_unlock(&pon->lock);
		return -EBUSY;
	}
	ether_addr_copy(pon->onu_mac, mac);
	mutex_unlock(&pon->lock);
	return count;
}
static DEVICE_ATTR_RW(onu_mac);

static ssize_t llid_count_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	return sysfs_emit(buf, "%u\n", pon->llid_count);
}

static ssize_t llid_count_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	unsigned int value;
	int ret = kstrtouint(buf, 0, &value);
	if (ret)
		return ret;
	if (!value || value > 32)
		return -EINVAL;
	mutex_lock(&pon->lock);
	if (pon->mpcp_enabled) {
		mutex_unlock(&pon->lock);
		return -EBUSY;
	}
	pon->llid_count = value;
	mutex_unlock(&pon->lock);
	return count;
}
static DEVICE_ATTR_RW(llid_count);

static ssize_t mpcp_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_mpcp_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t mpcp_store(struct device *dev, struct device_attribute *attr,
			 const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool enabled;
	int ret = kstrtobool(buf, &enabled);
	if (ret)
		return ret;
	if (enabled)
		ret = rtl_pon_mpcp_configure(pon);
	else
		rtl_pon_mpcp_stop(pon);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(mpcp);

static ssize_t llid_table_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_llid_table(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t llid_table_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	char operation[7];
	unsigned int index, mac, llid, multicast;
	bool add;
	int ret;
	if (sscanf(buf, "%6s %u %u %x %u", operation, &index, &mac, &llid, &multicast) != 5 ||
	    index > 63 || mac > 31 || llid > 0xffff || multicast > 1)
		return -EINVAL;
	if (!strcmp(operation, "add"))
		add = true;
	else if (!strcmp(operation, "delete"))
		add = false;
	else
		return -EINVAL;
	mutex_lock(&pon->lock);
	ret = rtl_pon_llid_update(pon, add, index, mac, llid, multicast);
	mutex_unlock(&pon->lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(llid_table);

static ssize_t pdc_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_pdc_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t pdc_store(struct device *dev, struct device_attribute *attr,
			const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool enabled;
	int ret = kstrtobool(buf, &enabled);
	if (ret)
		return ret;
	if (!enabled)
		return -EINVAL;
	ret = rtl_pon_pdc_configure(pon);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(pdc);

static struct attribute *rtl9617c_pon_attrs[] = {
	&dev_attr_state.attr,
	&dev_attr_serdes.attr,
	&dev_attr_serdes_registers.attr,
	&dev_attr_optical.attr,
	&dev_attr_mac.attr,
	&dev_attr_onu_mac.attr,
	&dev_attr_llid_count.attr,
	&dev_attr_mpcp.attr,
	&dev_attr_llid_table.attr,
	&dev_attr_pdc.attr,
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
	pon->top_irq = devm_platform_ioremap_resource(pdev, 6);
	if (IS_ERR(pon->top_irq))
		return PTR_ERR(pon->top_irq);
	{
		int irq = platform_get_irq(pdev, 0);
		int ret;
		if (irq < 0)
			return irq;
		ret = rtl_pon_mpcp_init(pon, irq);
		if (ret)
			return ret;
	}
	platform_set_drvdata(pdev, pon);
	dev_info(dev, "native PON MAC binding: global control=%08x\n", readl(pon->control));
	return 0;
}

static void rtl9617c_pon_remove(struct platform_device *pdev)
{
	rtl_pon_mpcp_stop(platform_get_drvdata(pdev));
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
