// SPDX-License-Identifier: GPL-2.0-only
/* TYFZA1 sensor sequence and signed conversion from its stock 5.10.70 kernel. */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/thermal.h>

static int rtl_thermal_get_temp(struct thermal_zone_device *zone, int *temp)
{
	void __iomem *sensor = thermal_zone_device_priv(zone);
	u32 value = readl(sensor + 0x0c);
	int integer = FIELD_GET(GENMASK(17, 10), value);
	int fraction = FIELD_GET(GENMASK(9, 0), value);

	if ((readl(sensor) & GENMASK(28, 0)) != 0x07f80000 ||
	    (readl(sensor + 4) & GENMASK(21, 0)) != 0x00374000 ||
	    (readl(sensor + 8) & (BIT(0) | BIT(3) | BIT(4))) !=
	    (BIT(0) | BIT(3) | BIT(4)))
		return -EIO;
	/* The stock implementation adds the fractional field in milli-Celsius,
	 * and uses signed integer minus fraction for negative temperatures. */
	if (value & BIT(18))
		*temp = (integer - 256) * 1000 - fraction;
	else
		*temp = integer * 1000 + fraction;
	return 0;
}

static const struct thermal_zone_device_ops rtl_thermal_ops = {
	.get_temp = rtl_thermal_get_temp,
};

static int rtl_thermal_probe(struct platform_device *pdev)
{
	struct thermal_zone_device *zone;
	void __iomem *sensor;

	sensor = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(sensor))
		return PTR_ERR(sensor);
	writel(0x07f80000, sensor);
	writel(0x00374000, sensor + 4);
	writel(0x0101010c, sensor + 8);
	writel(0x0101011d, sensor + 8);
	msleep(300);
	readl(sensor + 8);
	readl(sensor + 0x0c);
	readl(sensor + 0x0c);
	if ((readl(sensor) & GENMASK(28, 0)) != 0x07f80000 ||
	    (readl(sensor + 4) & GENMASK(21, 0)) != 0x00374000)
		return -EIO;
	zone = devm_thermal_of_zone_register(&pdev->dev, 0, sensor, &rtl_thermal_ops);
	return PTR_ERR_OR_ZERO(zone);
}

static const struct of_device_id rtl_thermal_match[] = {
	{ .compatible = "realtek,rtl9617c-thermal" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_thermal_match);

static struct platform_driver rtl_thermal_driver = {
	.probe = rtl_thermal_probe,
	.driver = {
		.name = "rtl9617c-thermal",
		.of_match_table = rtl_thermal_match,
	},
};
builtin_platform_driver(rtl_thermal_driver);
MODULE_DESCRIPTION("Realtek RTL9617C SoC temperature");
MODULE_LICENSE("GPL");
