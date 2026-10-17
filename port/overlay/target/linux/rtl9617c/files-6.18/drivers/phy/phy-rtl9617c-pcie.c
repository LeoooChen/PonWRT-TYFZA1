// SPDX-License-Identifier: GPL-2.0-only
/* Native Taurus PCIe clock/reset/PHY providers. Register behavior from
 * GPL-for-GP3000 phy-rtk-taurus.c and this board's original device tree. */
#include <linux/clk-provider.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/mfd/syscon.h>
#include <linux/of.h>
#include <linux/phy/phy.h>
#include <linux/platform_device.h>
#include <linux/reset-controller.h>
#include <linux/regmap.h>
#include "../net/ethernet/realtek/rtl9617c-sgmii.h"

struct rtl_pcie_controls;
struct rtl_pcie_lane {
	struct rtl_pcie_controls *controls;
	unsigned int id;
};
struct rtl_pcie_controls {
	void __iomem *core_reset, *phy_reset, *phy_control, *power, *usb_reset;
	void __iomem *sgmii_analog, *sgmii_serdes;
	struct regmap *global_config;
	spinlock_t lock;
	u32 powered_lanes;
	struct reset_controller_dev reset;
	struct clk_hw_onecell_data *clocks;
	struct phy *phys[6];
	struct rtl_pcie_lane lanes[6];
};
struct rtl_pcie_gate {
	struct clk_hw hw;
	struct regmap *regmap;
	unsigned int bit;
};
static int rtl_pcie_gate_enable(struct clk_hw *hw)
{
	struct rtl_pcie_gate *gate = container_of(hw, struct rtl_pcie_gate, hw);

	return regmap_update_bits(gate->regmap, 0, BIT(gate->bit), BIT(gate->bit));
}
static void rtl_pcie_gate_disable(struct clk_hw *hw)
{
	struct rtl_pcie_gate *gate = container_of(hw, struct rtl_pcie_gate, hw);

	regmap_update_bits(gate->regmap, 0, BIT(gate->bit), 0);
}
static int rtl_pcie_gate_enabled(struct clk_hw *hw)
{
	struct rtl_pcie_gate *gate = container_of(hw, struct rtl_pcie_gate, hw);
	u32 value;
	int ret = regmap_read(gate->regmap, 0, &value);

	return ret ? ret : !!(value & BIT(gate->bit));
}
static const struct clk_ops rtl_pcie_gate_ops = {
	.enable = rtl_pcie_gate_enable,
	.disable = rtl_pcie_gate_disable,
	.is_enabled = rtl_pcie_gate_enabled,
};

static void rtl_pcie_update(struct rtl_pcie_controls *controls,
			    void __iomem *reg, u32 mask, u32 value)
{
	unsigned long flags;

	spin_lock_irqsave(&controls->lock, flags);
	writel((readl(reg) & ~mask) | value, reg);
	spin_unlock_irqrestore(&controls->lock, flags);
}
static int rtl_pcie_reset_set(struct reset_controller_dev *reset,
			      unsigned long id, bool asserted)
{
	struct rtl_pcie_controls *controls = container_of(reset, struct rtl_pcie_controls, reset);
	void __iomem *reg = id < 2 ? controls->core_reset : controls->phy_reset;
	u32 mask = BIT(id < 2 ? 6 + id : id - 2);

	if (id == 4) {
		reg = controls->core_reset;
		mask = BIT(13);
	} else if (id == 5) {
		reg = controls->usb_reset;
		mask = BIT(5);
	} else if (id == 6) {
		reg = controls->core_reset;
		mask = BIT(31);
	}

	rtl_pcie_update(controls, reg, mask, asserted ? mask : 0);
	return 0;
}
static int rtl_pcie_reset_assert(struct reset_controller_dev *reset, unsigned long id)
{
	return rtl_pcie_reset_set(reset, id, true);
}
static int rtl_pcie_reset_deassert(struct reset_controller_dev *reset, unsigned long id)
{
	return rtl_pcie_reset_set(reset, id, false);
}
static const struct reset_control_ops rtl_pcie_reset_ops = {
	.assert = rtl_pcie_reset_assert,
	.deassert = rtl_pcie_reset_deassert,
};

static int rtl_pcie_lane_power(struct phy *phy, bool enabled)
{
	struct rtl_pcie_lane *lane = phy_get_drvdata(phy);
	struct rtl_pcie_controls *controls = lane->controls;
	unsigned int id = lane->id;
	u32 supply = BIT(7 + id) | BIT(20 + id * 2) | BIT(21 + id * 2);
	u32 isolation = BIT(id);
	u32 analog = BIT(16 + id / 2), value;
	unsigned long flags;

	/* S0/S1 ISO_ANA_B belongs to both lanes, not to the lane0 consumer. */
	spin_lock_irqsave(&controls->lock, flags);
	if (id == 5) {
		value = readl(controls->phy_control);
		if (enabled)
			value = (value | BIT(25)) & ~BIT(26);
		writel(value, controls->phy_control);
		value = readl(controls->power);
		value = enabled ? (value | 0xc0080000) & ~0x6020 :
			(value & ~0xc0080000) | 0x6020;
		writel(value, controls->power);
		if (enabled) {
			writel(0x5359, controls->sgmii_serdes + 0x1c);
			writel(0x30, controls->phy_control + 0x0c);
			writel(0x34200248, controls->phy_control + 0x10);
		}
		spin_unlock_irqrestore(&controls->lock, flags);
		return 0;
	}
	/* Stock PHY5 is the USB S2 lane. Share the PCIe register lock so
	 * USB initialization cannot overwrite another lane's power bits. */
	if (id == 4) {
		value = readl(controls->phy_control);
		if (enabled)
			value = (value | 0x00180000) & ~0x00600000;
		writel(value, controls->phy_control);
		value = readl(controls->power);
		value = enabled ? (value | 0x30040800) & ~0x1010 :
			(value & ~0x30040800) | 0x1010;
		writel(value, controls->power);
		spin_unlock_irqrestore(&controls->lock, flags);
		return 0;
	}
	if (enabled) {
		controls->powered_lanes |= BIT(id);
		if (id < 2)
			writel(readl(controls->phy_control) & ~BIT(27), controls->phy_control);
	} else {
		controls->powered_lanes &= ~BIT(id);
	}
	value = readl(controls->power) & ~(supply | isolation | analog);
	value |= enabled ? supply : isolation;
	if (controls->powered_lanes & (3U << (id & ~1U)))
		value |= analog;
	writel(value, controls->power);
	spin_unlock_irqrestore(&controls->lock, flags);
	return 0;
}
static int rtl_pcie_lane_on(struct phy *phy)
{
	return rtl_pcie_lane_power(phy, true);
}
static int rtl_pcie_lane_off(struct phy *phy)
{
	return rtl_pcie_lane_power(phy, false);
}
static int rtl_pcie_lane_speed(struct phy *phy, int speed)
{
	struct rtl_pcie_lane *lane = phy_get_drvdata(phy);
	struct rtl_pcie_controls *controls = lane->controls;
	unsigned long flags;

	if (lane->id != 5 || (speed != 10 && speed != 100 && speed != 1000 && speed != 2500))
		return -EINVAL;
	spin_lock_irqsave(&controls->lock, flags);
	rtl_sgmii_link(controls->phy_control, controls->phy_reset,
		controls->phy_control + 0x10, controls->sgmii_analog, speed);
	spin_unlock_irqrestore(&controls->lock, flags);
	return 0;
}
static const struct phy_ops rtl_pcie_phy_ops = {
	.power_on = rtl_pcie_lane_on,
	.power_off = rtl_pcie_lane_off,
	.set_speed = rtl_pcie_lane_speed,
	.owner = THIS_MODULE,
};
static struct phy *rtl_pcie_phy_xlate(struct device *dev, const struct of_phandle_args *args)
{
	struct rtl_pcie_controls *controls = dev_get_drvdata(dev);

	if (args->args_count != 1 || args->args[0] >= ARRAY_SIZE(controls->phys))
		return ERR_PTR(-EINVAL);
	return controls->phys[args->args[0]];
}
static int rtl_pcie_controls_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rtl_pcie_controls *controls;
	struct phy_provider *provider;
	unsigned int i;
	int ret;

	controls = devm_kzalloc(dev, sizeof(*controls), GFP_KERNEL);
	if (!controls)
		return -ENOMEM;
	controls->core_reset = devm_platform_ioremap_resource_byname(pdev, "core-reset");
	if (IS_ERR(controls->core_reset))
		return PTR_ERR(controls->core_reset);
	controls->phy_reset = devm_platform_ioremap_resource_byname(pdev, "phy-reset");
	if (IS_ERR(controls->phy_reset))
		return PTR_ERR(controls->phy_reset);
	controls->usb_reset = devm_platform_ioremap_resource_byname(pdev, "usb-reset");
	if (IS_ERR(controls->usb_reset))
		return PTR_ERR(controls->usb_reset);
	controls->global_config = syscon_regmap_lookup_by_phandle(dev->of_node, "realtek,global-config");
	if (IS_ERR(controls->global_config))
		return PTR_ERR(controls->global_config);
	controls->phy_control = devm_platform_ioremap_resource_byname(pdev, "phy-control");
	if (IS_ERR(controls->phy_control))
		return PTR_ERR(controls->phy_control);
	controls->power = devm_platform_ioremap_resource_byname(pdev, "power");
	if (IS_ERR(controls->power))
		return PTR_ERR(controls->power);
	controls->sgmii_analog = devm_platform_ioremap_resource_byname(pdev, "sgmii-analog");
	if (IS_ERR(controls->sgmii_analog))
		return PTR_ERR(controls->sgmii_analog);
	controls->sgmii_serdes = devm_platform_ioremap_resource_byname(pdev, "sgmii-serdes");
	if (IS_ERR(controls->sgmii_serdes))
		return PTR_ERR(controls->sgmii_serdes);
	spin_lock_init(&controls->lock);
	platform_set_drvdata(pdev, controls);
	controls->clocks = devm_kzalloc(dev, struct_size(controls->clocks, hws, 2), GFP_KERNEL);
	if (!controls->clocks)
		return -ENOMEM;
	controls->clocks->num = 2;
	for (i = 0; i < 2; i++) {
		const char *name = devm_kasprintf(dev, GFP_KERNEL, "rtl9617c-pcie%u", i);
		struct clk_init_data init = {.name = name, .ops = &rtl_pcie_gate_ops};
		struct rtl_pcie_gate *gate;

		if (!name)
			return -ENOMEM;
		gate = devm_kzalloc(dev, sizeof(*gate), GFP_KERNEL);
		if (!gate)
			return -ENOMEM;
		gate->regmap = controls->global_config;
		gate->bit = 10 + i * 2;
		gate->hw.init = &init;
		ret = devm_clk_hw_register(dev, &gate->hw);
		if (ret)
			return ret;
		controls->clocks->hws[i] = &gate->hw;
	}
	ret = devm_of_clk_add_hw_provider(dev, of_clk_hw_onecell_get, controls->clocks);
	if (ret)
		return ret;
	controls->reset.owner = THIS_MODULE;
	controls->reset.ops = &rtl_pcie_reset_ops;
	controls->reset.of_node = dev->of_node;
	controls->reset.nr_resets = 7; /* PCIe, USB and the separate P6/QX reset */
	ret = devm_reset_controller_register(dev, &controls->reset);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(controls->phys); i++) {
		controls->lanes[i] = (struct rtl_pcie_lane){.controls = controls, .id = i};
		controls->phys[i] = devm_phy_create(dev, NULL, &rtl_pcie_phy_ops);
		if (IS_ERR(controls->phys[i]))
			return PTR_ERR(controls->phys[i]);
		phy_set_drvdata(controls->phys[i], &controls->lanes[i]);
	}
	provider = devm_of_phy_provider_register(dev, rtl_pcie_phy_xlate);
	return PTR_ERR_OR_ZERO(provider);
}
static const struct of_device_id rtl_pcie_controls_match[] = {
	{.compatible = "realtek,rtl9617c-pcie-controls"},
	{},
};
MODULE_DEVICE_TABLE(of, rtl_pcie_controls_match);
static struct platform_driver rtl_pcie_controls_driver = {
	.probe = rtl_pcie_controls_probe,
	.driver = {.name = "rtl9617c-pcie-controls", .of_match_table = rtl_pcie_controls_match},
};
module_platform_driver(rtl_pcie_controls_driver);
MODULE_DESCRIPTION("Native RTL9617C PCIe clock/reset/PHY controls");
MODULE_LICENSE("GPL");
