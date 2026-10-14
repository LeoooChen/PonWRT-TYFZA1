// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Copyright (c) Cortina-Access Limited 2015. All rights reserved.
 * Native mii_bus port of the Taurus MDIO/OCP access in aal_phy.c,
 * GPL-for-GP3000 5090fbcb6ba743dd7b1314811ef557bad0460147.
 * The C22 and internal OCP sequences also match TYFZA1 ca-ne.ko disassembly.
 * No PHY reset, PHY microcode, calibration, NI/DMA or optical initialization.
 */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/gpio/consumer.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of_mdio.h>
#include <linux/phy.h>
#include <linux/platform_device.h>

#define MDIO_CFG	0x00
#define MDIO_MANUAL	BIT(0)
#define MDIO_ADDR	0x04
#define MDIO_PHY	GENMASK(4, 0)
#define MDIO_REG	GENMASK(12, 8)
#define MDIO_READ	BIT(15)
#define MDIO_C45	BIT(16)
#define MDIO_OP		GENMASK(19, 18)
#define MDIO_WRDATA	0x08
#define MDIO_RDDATA	0x0c
#define MDIO_CTRL	0x10
#define MDIO_DONE	BIT(0)
#define MDIO_START	BIT(7)
#define GPHY_CTRL12	0x30
#define GPHY_CTRL13	0x34
#define GPHY_OCP_TIMEOUT GENMASK(31, 24)
#define GPHY_OCP_SELECT	BIT(0)

struct rtl9617c_mdio {
	void __iomem *per;
	void __iomem *ocp;
	void __iomem *wrap;
	u16 page[4];
};

static bool rtl_mdio_internal(int phy)
{
	return phy >= 1 && phy <= 4;
}

static u32 rtl_mdio_ocp_offset(struct rtl9617c_mdio *mdio, int phy, int reg)
{
	u32 address;

	if (reg >= 16 && reg < 24)
		address = ((mdio->page[phy - 1] & 0xfff) << 4) + ((reg - 16) << 1);
	else
		address = 0xa400 + (reg << 1);
	return ((phy - 1) << 18) | ((address & 0xffff) << 2);
}

static int rtl_mdio_xfer(struct rtl9617c_mdio *mdio, u32 address, u16 value,
			 bool read)
{
	u32 status;
	int ret;

	/* DONE is W1C. Do not accept completion left by an earlier command. */
	writel(MDIO_DONE, mdio->per + MDIO_CTRL);
	writel(address, mdio->per + MDIO_ADDR);
	if (!read)
		writel(value, mdio->per + MDIO_WRDATA);
	writel(MDIO_START, mdio->per + MDIO_CTRL);
	/* Stock ca-ne uses 50000 polls separated by ca_udelay(1). */
	ret = readl_poll_timeout(mdio->per + MDIO_CTRL, status,
				status & MDIO_DONE, 1, 50000);
	if (ret)
		return ret;
	value = read ? readl(mdio->per + MDIO_RDDATA) & 0xffff : 0;
	writel(MDIO_DONE, mdio->per + MDIO_CTRL);
	return value;
}

static int rtl_mdio_read(struct mii_bus *bus, int phy, int reg)
{
	struct rtl9617c_mdio *mdio = bus->priv;

	if (phy < 0 || phy > 31 || reg < 0 || reg > 31)
		return -EINVAL;
	if (rtl_mdio_internal(phy)) {
		if (reg == 31)
			return mdio->page[phy - 1];
		return readl(mdio->ocp + rtl_mdio_ocp_offset(mdio, phy, reg)) & 0xffff;
	}
	return rtl_mdio_xfer(mdio, FIELD_PREP(MDIO_PHY, phy) |
		FIELD_PREP(MDIO_REG, reg) | FIELD_PREP(MDIO_OP, 3) | MDIO_READ, 0, true);
}

static int rtl_mdio_write(struct mii_bus *bus, int phy, int reg, u16 value)
{
	struct rtl9617c_mdio *mdio = bus->priv;

	if (phy < 0 || phy > 31 || reg < 0 || reg > 31)
		return -EINVAL;
	if (rtl_mdio_internal(phy)) {
		if (reg == 31)
			mdio->page[phy - 1] = value;
		else
			writel(value, mdio->ocp + rtl_mdio_ocp_offset(mdio, phy, reg));
		/* Keep the documented Taurus OCP write settling interval. */
		usleep_range(1000, 2000);
		return 0;
	}
	return rtl_mdio_xfer(mdio, FIELD_PREP(MDIO_PHY, phy) |
		FIELD_PREP(MDIO_REG, reg) | FIELD_PREP(MDIO_OP, 1), value, false);
}

static int rtl_mdio_c45(struct mii_bus *bus, int phy, int devad, int reg,
			 u16 value, bool read)
{
	struct rtl9617c_mdio *mdio = bus->priv;
	u32 address;
	int ret;

	if (phy < 0 || phy > 31 || devad < 0 || devad > 31 || reg < 0 || reg > 0xffff)
		return -EINVAL;
	if (rtl_mdio_internal(phy))
		return -EOPNOTSUPP;
	address = FIELD_PREP(MDIO_PHY, phy) | FIELD_PREP(MDIO_REG, devad) | MDIO_C45;
	/* mii_bus core serializes both phases under its mdio_lock. */
	ret = rtl_mdio_xfer(mdio, address, reg, false);
	if (ret)
		return ret;
	address |= FIELD_PREP(MDIO_OP, read ? 3 : 1) | (read ? MDIO_READ : 0);
	return rtl_mdio_xfer(mdio, address, value, read);
}

static int rtl_mdio_read_c45(struct mii_bus *bus, int phy, int devad, int reg)
{
	return rtl_mdio_c45(bus, phy, devad, reg, 0, true);
}

static int rtl_mdio_write_c45(struct mii_bus *bus, int phy, int devad, int reg, u16 value)
{
	return rtl_mdio_c45(bus, phy, devad, reg, value, false);
}

static int rtl_mdio_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rtl9617c_mdio *mdio;
	struct mii_bus *bus;
	struct gpio_desc *external;

	/* Actual rtl8221b.ko releases GPIO14 high before probing MDIO address7. */
	external = devm_gpiod_get_optional(dev, "external-phy-enable", GPIOD_OUT_HIGH);
	if (IS_ERR(external))
		return PTR_ERR(external);
	if (external)
		msleep(300);

	bus = devm_mdiobus_alloc_size(dev, sizeof(*mdio));
	if (!bus)
		return -ENOMEM;
	mdio = bus->priv;
	mdio->per = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(mdio->per))
		return PTR_ERR(mdio->per);
	mdio->ocp = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(mdio->ocp))
		return PTR_ERR(mdio->ocp);
	mdio->wrap = devm_platform_ioremap_resource(pdev, 2);
	if (IS_ERR(mdio->wrap))
		return PTR_ERR(mdio->wrap);
	/* Preserve bootloader MDC prescaler, PHY patch/calibration and pinmux. */
	writel(readl(mdio->per + MDIO_CFG) | MDIO_MANUAL, mdio->per + MDIO_CFG);
	writel(readl(mdio->wrap + GPHY_CTRL12) | GPHY_OCP_TIMEOUT,
	       mdio->wrap + GPHY_CTRL12);
	writel(readl(mdio->wrap + GPHY_CTRL13) | GPHY_OCP_SELECT,
	       mdio->wrap + GPHY_CTRL13);
	usleep_range(1000, 2000);
	bus->name = "RTL9617C MDIO/OCP";
	bus->parent = dev;
	bus->read = rtl_mdio_read;
	bus->write = rtl_mdio_write;
	bus->read_c45 = rtl_mdio_read_c45;
	bus->write_c45 = rtl_mdio_write_c45;
	snprintf(bus->id, MII_BUS_ID_SIZE, "%s", dev_name(dev));
	return devm_of_mdiobus_register(dev, bus, dev->of_node);
}

static const struct of_device_id rtl_mdio_match[] = {
	{ .compatible = "realtek,rtl9617c-mdio" }, { }
};
MODULE_DEVICE_TABLE(of, rtl_mdio_match);

static struct platform_driver rtl_mdio_driver = {
	.probe = rtl_mdio_probe,
	.driver = { .name = "rtl9617c-mdio", .of_match_table = rtl_mdio_match },
};
module_platform_driver(rtl_mdio_driver);
MODULE_DESCRIPTION("Native Taurus MDIO and internal copper PHY OCP access");
MODULE_LICENSE("GPL");
