// SPDX-License-Identifier: GPL-2.0-only
/* Native TYFZA1 USB PHY, from the board's taurus_usb{2,3}_phy machine code. */
#include <linux/arm-smccc.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/phy/phy.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/reset.h>
#include "rtl9617c-usb-sequence.h"

struct rtl_usb {
	struct device *dev;
	void __iomem *usb2, *usb3, *extension, *serdes, *atu;
	struct phy *lane;
	struct reset_control *core_reset, *port_reset;
	struct gpio_desc *vbus;
	u8 usb2_address[10], usb2_value[10], usb3_address[20];
	u16 usb3_value[20];
	u32 usb3_rows;
};

static void rtl_usb2_mmio(void *context, unsigned int reg, unsigned int value)
{
	struct rtl_usb *usb = context;

	writel(value, usb->usb2 + reg);
}

static int rtl_usb_init(struct phy *phy)
{
	struct rtl_usb *usb = phy_get_drvdata(phy);
	unsigned int port, row;
	u32 value;
	int ret;

	ret = reset_control_assert(usb->core_reset);
	if (ret)
		return ret;
	msleep(20);
	ret = reset_control_deassert(usb->core_reset);
	if (ret)
		return ret;
	msleep(20);
	ret = reset_control_deassert(usb->port_reset);
	if (ret)
		return ret;
	for (port = 0; port < 2; port++)
		writel(readl(usb->usb2 + port * 8) | 0x80064, usb->usb2 + port * 8);
	ret = phy_power_on(usb->lane);
	if (ret)
		return ret;
	msleep(20);
	writel(readl(usb->usb3) | 0x80000300, usb->usb3);
	for (row = 0; row < usb->usb3_rows; row++)
		writel(usb->usb3_value[row], usb->serdes + usb->usb3_address[row] * 4);
	writel(readl(usb->extension) & ~BIT(28), usb->extension);
	for (port = 0; port < 2; port++)
		for (row = 0; row < ARRAY_SIZE(usb->usb2_address); row++)
			rtl_usb2_write(usb, rtl_usb2_mmio, port,
				usb->usb2_address[row], usb->usb2_value[row]);
	writel((readl(usb->usb2 + 0x10) & ~BIT(9)) | BIT(7), usb->usb2 + 0x10);
	writel(readl(usb->usb2 + 0x14) & ~BIT(25), usb->usb2 + 0x14);
	/* USB uses CCI alias2; PCIe uses alias4. 512 MiB DDR, 64 KiB pages. */
	writel(0, usb->atu + 4);
	writel(0xffffe000, usb->atu + 8);
	writel(0x00020000, usb->atu + 0x0c);
	writel(0x07ff0022, usb->atu + 0x10);
	writel(0x80, usb->atu + 0x14);
	writel(0x80, usb->atu + 0x18);
	writel(0x80000001, usb->atu);
	ret = readl_poll_timeout(usb->atu, value, !(value & BIT(31)), 1, 10000);
	if (ret) {
		phy_power_off(usb->lane);
		return dev_err_probe(usb->dev, ret, "USB ATU initialization\n");
	}
	return 0;
}

static int rtl_usb_exit(struct phy *phy)
{
	struct rtl_usb *usb = phy_get_drvdata(phy);
	int ret = reset_control_assert(usb->core_reset);

	phy_power_off(usb->lane);
	return ret;
}

static int rtl_usb_power_on(struct phy *phy)
{
	struct rtl_usb *usb = phy_get_drvdata(phy);

	gpiod_set_value_cansleep(usb->vbus, 1);
	msleep(100);
	return 0;
}

static int rtl_usb_power_off(struct phy *phy)
{
	struct rtl_usb *usb = phy_get_drvdata(phy);

	gpiod_set_value_cansleep(usb->vbus, 0);
	msleep(100);
	return 0;
}

static const struct phy_ops rtl_usb_ops = {
	.init = rtl_usb_init,
	.exit = rtl_usb_exit,
	.power_on = rtl_usb_power_on,
	.power_off = rtl_usb_power_off,
	.owner = THIS_MODULE,
};

static int rtl_usb_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct phy_provider *provider;
	struct arm_smccc_res revision;
	struct rtl_usb *usb;
	struct phy *phy;
	const char *usb2_values, *usb3_addresses, *usb3_values;
	int ret;

	usb = devm_kzalloc(dev, sizeof(*usb), GFP_KERNEL);
	if (!usb)
		return -ENOMEM;
	usb->dev = dev;
	usb->usb2 = devm_platform_ioremap_resource_byname(pdev, "usb2");
	if (IS_ERR(usb->usb2))
		return PTR_ERR(usb->usb2);
	usb->usb3 = devm_platform_ioremap_resource_byname(pdev, "usb3");
	if (IS_ERR(usb->usb3))
		return PTR_ERR(usb->usb3);
	usb->extension = devm_platform_ioremap_resource_byname(pdev, "extension");
	if (IS_ERR(usb->extension))
		return PTR_ERR(usb->extension);
	usb->serdes = devm_platform_ioremap_resource_byname(pdev, "serdes");
	if (IS_ERR(usb->serdes))
		return PTR_ERR(usb->serdes);
	usb->atu = devm_platform_ioremap_resource_byname(pdev, "atu");
	if (IS_ERR(usb->atu))
		return PTR_ERR(usb->atu);
	usb->lane = devm_phy_get(dev, "lane");
	if (IS_ERR(usb->lane))
		return dev_err_probe(dev, PTR_ERR(usb->lane), "USB S2 lane\n");
	usb->core_reset = devm_reset_control_get_exclusive(dev, "core");
	if (IS_ERR(usb->core_reset))
		return PTR_ERR(usb->core_reset);
	usb->port_reset = devm_reset_control_get_exclusive(dev, "port1");
	if (IS_ERR(usb->port_reset))
		return PTR_ERR(usb->port_reset);
	usb->vbus = devm_gpiod_get(dev, "vbus", GPIOD_OUT_HIGH);
	if (IS_ERR(usb->vbus))
		return dev_err_probe(dev, PTR_ERR(usb->vbus), "USB VBUS GPIO\n");
	/* Same BL31 call as original get_chip_revision; cut A has a separate table. */
	arm_smccc_smc(0x82001106, 0x12345678, 0, 0, 0, 0, 0, 0, &revision);
	if ((long)revision.a0 < 'A' || revision.a0 > 'Z')
		return dev_err_probe(dev, -ENODEV, "USB chip revision: %ld\n", revision.a0);
	usb2_values = revision.a0 == 'A' ? "realtek,usb2-values-a" : "realtek,usb2-values-b";
	usb3_addresses = revision.a0 == 'A' ? "realtek,usb3-addresses-a" : "realtek,usb3-addresses-b";
	usb3_values = revision.a0 == 'A' ? "realtek,usb3-values-a" : "realtek,usb3-values-b";
	ret = device_property_read_u8_array(dev, "realtek,usb2-addresses", usb->usb2_address, 10);
	if (!ret)
		ret = device_property_read_u8_array(dev, usb2_values, usb->usb2_value, 10);
	if (ret)
		return ret;
	ret = device_property_count_u8(dev, usb3_addresses);
	if (ret <= 0 || ret > ARRAY_SIZE(usb->usb3_address))
		return -EINVAL;
	usb->usb3_rows = ret;
	ret = device_property_read_u8_array(dev, usb3_addresses, usb->usb3_address, usb->usb3_rows);
	if (!ret)
		ret = device_property_read_u16_array(dev, usb3_values, usb->usb3_value, usb->usb3_rows);
	if (ret)
		return ret;
	phy = devm_phy_create(dev, NULL, &rtl_usb_ops);
	if (IS_ERR(phy))
		return PTR_ERR(phy);
	phy_set_drvdata(phy, usb);
	provider = devm_of_phy_provider_register(dev, of_phy_simple_xlate);
	if (IS_ERR(provider))
		return PTR_ERR(provider);
	dev_info(dev, "native USB2/USB3 PHY, cut %c, S2 lane, %u SerDes rows\n",
		(char)revision.a0, usb->usb3_rows);
	return 0;
}

static const struct of_device_id rtl_usb_match[] = {
	{ .compatible = "realtek,rtl9617c-usb-phy" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_usb_match);

static struct platform_driver rtl_usb_driver = {
	.probe = rtl_usb_probe,
	.driver = { .name = "rtl9617c-usb-phy", .of_match_table = rtl_usb_match },
};
module_platform_driver(rtl_usb_driver);
MODULE_DESCRIPTION("Native RTL9617C USB PHY and VBUS control");
MODULE_LICENSE("GPL");
