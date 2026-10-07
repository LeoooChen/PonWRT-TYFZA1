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

#define PON_EPON_MODE BIT(0)
#define EPON_STATUS 0x0010
#define MPCP_REGISTERED 0x2014

struct rtl9617c_pon {
	void __iomem *control;
	void __iomem *mac;
};

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

static struct attribute *rtl9617c_pon_attrs[] = {
	&dev_attr_state.attr,
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
	pon->control = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(pon->control))
		return PTR_ERR(pon->control);
	pon->mac = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(pon->mac))
		return PTR_ERR(pon->mac);
	platform_set_drvdata(pdev, pon);
	dev_info(dev, "native PON MAC binding: global control=%08x\n", readl(pon->control));
	return 0;
}

static const struct of_device_id rtl9617c_pon_of_match[] = {
	{ .compatible = "realtek,rtl9617c-pon" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl9617c_pon_of_match);

static struct platform_driver rtl9617c_pon_driver = {
	.probe = rtl9617c_pon_probe,
	.driver = {
		.name = "rtl9617c-pon",
		.of_match_table = rtl9617c_pon_of_match,
		.dev_groups = rtl9617c_pon_groups,
	},
};
module_platform_driver(rtl9617c_pon_driver);
MODULE_DESCRIPTION("Native RTL9617C PON MAC platform binding");
MODULE_LICENSE("GPL");
