// SPDX-License-Identifier: GPL-2.0-only
/* Native RTL9617C DesignWare host glue. Taurus reset, SerDes and interrupt
 * behavior references Cortina's GPL pcie-cortina.c; no vendor PCI core. */
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/irqdomain.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/phy/phy.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/reset.h>
#include <linux/sizes.h>

#include "pcie-designware.h"
#include "rtl9617c-pcie-sequence.h"

#define GLB_IRQ_STATUS 0x00
#define GLB_IRQ_ENABLE 0x04
#define GLB_CORE_CONFIG 0x18
#define GLB_LINK_STATUS 0x1c
#define GLB_MSI_ENABLE 0x1a4
#define GLB_MSI_IRQ BIT(8)
#define GLB_INTX_IRQS 0x55

struct rtl_pcie {
	struct dw_pcie pci;
	void __iomem *global, *serdes, *coherent_atu;
	struct clk *clock;
	struct reset_control *core_reset, *phy_reset;
	struct gpio_desc *endpoint_reset;
	struct phy *phy;
	struct irq_domain *intx;
	raw_spinlock_t irq_lock;
	int irq;
	unsigned int index, endpoint_ready_ms, ddr_bytes, alias;
	char profile;
	u32 *parameters;
	unsigned int parameter_words;
	u32 atu_saved[8][6], atu_enabled;
	bool clock_on, phy_on, atu_owned;
};

static struct rtl_pcie *rtl_pcie_from_dw(struct dw_pcie *pci)
{
	return container_of(pci, struct rtl_pcie, pci);
}
static unsigned int rtl_serdes_read(void *context, unsigned int reg)
{
	return readl(((struct rtl_pcie *)context)->serdes + reg);
}
static void rtl_serdes_write(void *context, unsigned int reg, unsigned int value)
{
	writel(value, ((struct rtl_pcie *)context)->serdes + reg);
}
static void rtl_serdes_delay(void *context)
{
	usleep_range(10, 20);
}
static int rtl_serdes_wait(void *context, unsigned int reg, unsigned int mask)
{
	u32 value;

	return readl_poll_timeout(((struct rtl_pcie *)context)->serdes + reg, value,
				 (value & mask) == mask, 10, 100000);
}
static unsigned int rtl_atu_read(void *context, unsigned int reg)
{
	return readl(((struct rtl_pcie *)context)->coherent_atu + reg);
}
static void rtl_atu_write(void *context, unsigned int reg, unsigned int value)
{
	writel(value, ((struct rtl_pcie *)context)->coherent_atu + reg);
}
static int rtl_atu_wait(void *context, unsigned int reg, unsigned int mask)
{
	u32 value;

	return readl_poll_timeout(((struct rtl_pcie *)context)->coherent_atu + reg,
				 value, !(value & mask), 1, 100000);
}
static int rtl_atu_prepare(struct rtl_pcie *pcie)
{
	struct rtl_pcie_io io = {pcie, rtl_atu_read, rtl_atu_write, NULL, rtl_atu_wait};
	unsigned int entry, word;
	int ret;

	pcie->atu_enabled = readl(pcie->coherent_atu) & 255;
	for (entry = 0; entry < 8; entry++) {
		writel(BIT(30) | (entry << 24) | pcie->atu_enabled, pcie->coherent_atu);
		ret = rtl_atu_wait(pcie, 0, BIT(30));
		if (ret)
			return ret;
		for (word = 0; word < 6; word++)
			pcie->atu_saved[entry][word] = readl(pcie->coherent_atu + 4 + word * 4);
	}
	pcie->atu_owned = true;
	return rtl_pcie_atu_ddr(&io, pcie->ddr_bytes >> 16, pcie->alias);
}
static void rtl_atu_restore(struct rtl_pcie *pcie)
{
	unsigned int entry, word;
	int ret;

	if (!pcie->atu_owned)
		return;
	for (entry = 0; entry < 8; entry++) {
		for (word = 0; word < 6; word++)
			writel(pcie->atu_saved[entry][word], pcie->coherent_atu + 4 + word * 4);
		writel(BIT(31) | (entry << 24) | pcie->atu_enabled, pcie->coherent_atu);
		ret = rtl_atu_wait(pcie, 0, BIT(31));
		if (ret) {
			dev_err(pcie->pci.dev, "coherent ATU restore entry%u:%d\n", entry, ret);
			return;
		}
	}
	pcie->atu_owned = false;
}
static void rtl_pcie_power_stop(struct rtl_pcie *pcie)
{
	gpiod_set_value_cansleep(pcie->endpoint_reset, 1);
	reset_control_assert(pcie->core_reset);
	reset_control_assert(pcie->phy_reset);
	if (pcie->phy_on) {
		phy_power_off(pcie->phy);
		pcie->phy_on = false;
	}
	rtl_atu_restore(pcie);
	if (pcie->clock_on) {
		clk_disable_unprepare(pcie->clock);
		pcie->clock_on = false;
	}
}
static int rtl_pcie_release_internal(struct rtl_pcie *pcie)
{
	int ret;

	ret = phy_power_on(pcie->phy);
	if (ret)
		return ret;
	pcie->phy_on = true;
	usleep_range(1000, 2000);
	ret = reset_control_deassert(pcie->phy_reset);
	if (ret)
		return ret;
	usleep_range(1000, 2000);
	ret = reset_control_deassert(pcie->core_reset);
	usleep_range(1000, 2000);
	return ret;
}
static int rtl_pcie_host_start(struct dw_pcie_rp *pp)
{
	struct rtl_pcie *pcie = rtl_pcie_from_dw(to_dw_pcie_from_pp(pp));
	struct rtl_pcie_io io = {pcie, rtl_serdes_read, rtl_serdes_write,
			       rtl_serdes_delay, rtl_serdes_wait};
	struct platform_device *pdev = to_platform_device(pcie->pci.dev);
	struct resource *dbi = platform_get_resource_byname(pdev, IORESOURCE_MEM, "dbi");
	struct resource *atu = platform_get_resource_byname(pdev, IORESOURCE_MEM, "atu");
	unsigned int i;
	int ret;

	ret = clk_prepare_enable(pcie->clock);
	if (ret)
		return ret;
	pcie->clock_on = true;
	gpiod_set_value_cansleep(pcie->endpoint_reset, 1);
	ret = reset_control_assert(pcie->core_reset);
	if (ret)
		goto stop;
	usleep_range(1000, 2000);
	ret = reset_control_assert(pcie->phy_reset);
	if (ret)
		goto stop;
	usleep_range(1000, 2000);
	for (i = 0; i < pcie->parameter_words; i += 2)
		writel(pcie->parameters[i + 1], pcie->serdes + pcie->parameters[i]);
	ret = rtl_pcie_release_internal(pcie);
	if (ret)
		goto stop;
	ret = rtl_pcie_serdes_calibrate(&io, pcie->index, pcie->profile);
	if (ret)
		goto stop;
	if (pcie->profile == 'B' && !pcie->index) {
		/* Original B calibration resets DPHY/core once after the analog pass. */
		phy_power_off(pcie->phy);
		pcie->phy_on = false;
		usleep_range(1000, 2000);
		reset_control_assert(pcie->phy_reset);
		usleep_range(1000, 2000);
		reset_control_assert(pcie->core_reset);
		usleep_range(1000, 2000);
		ret = rtl_pcie_release_internal(pcie);
		if (ret)
			goto stop;
	}
	ret = rtl_serdes_wait(pcie, 0x7c, BIT(0));
	if (!ret)
		ret = rtl_serdes_wait(pcie, 0x17c, BIT(0));
	if (ret)
		goto stop;
	ret = rtl_atu_prepare(pcie);
	if (ret)
		goto stop;
	writel(dbi->start, pcie->global + 0x40);
	writel(dbi->end, pcie->global + 0x44);
	writel(atu->start & 0xfff80000, pcie->global + 0x48);
	gpiod_set_value_cansleep(pcie->endpoint_reset, 0);
	msleep(pcie->endpoint_ready_ms);
	dev_info(pcie->pci.dev, "PCIe%u native profile%c,DDR%uMiB CCI alias%u\n",
		 pcie->index, pcie->profile, pcie->ddr_bytes >> 20, pcie->alias);
	return 0;
stop:
	rtl_pcie_power_stop(pcie);
	return dev_err_probe(pcie->pci.dev, ret, "PCIe analog/DMA preparation failed\n");
}
static void rtl_pcie_host_stop(struct dw_pcie_rp *pp)
{
	rtl_pcie_power_stop(rtl_pcie_from_dw(to_dw_pcie_from_pp(pp)));
}
static bool rtl_pcie_link_up(struct dw_pcie *pci)
{
	struct rtl_pcie *pcie = rtl_pcie_from_dw(pci);
	u32 value = readl(pcie->global + GLB_LINK_STATUS);

	return (value & BIT(18)) && ((value >> 4) & 63) == 0x11;
}
static int rtl_pcie_link_start(struct dw_pcie *pci)
{
	writel(BIT(0), rtl_pcie_from_dw(pci)->global + GLB_CORE_CONFIG);
	return 0;
}
static void rtl_pcie_link_stop(struct dw_pcie *pci)
{
	void __iomem *global = rtl_pcie_from_dw(pci)->global;

	writel(BIT(6), global + GLB_CORE_CONFIG);
	usleep_range(100, 200);
	writel(0, global + GLB_CORE_CONFIG);
}
static int rtl_pcie_msi_init(struct dw_pcie_rp *pp)
{
	/* Wrapper IRQ multiplexes MSI and INTx. The generic MSI domain remains
 * owned by DesignWare; this glue supplies its parent interrupt handler. */
	pp->has_msi_ctrl = true;
	pp->msi_irq[0] = -1;
	pp->num_vectors = 32;
	return dw_pcie_msi_host_init(pp);
}
static void rtl_pcie_post_init(struct dw_pcie_rp *pp)
{
	struct rtl_pcie *pcie = rtl_pcie_from_dw(to_dw_pcie_from_pp(pp));

	writel(~0U, pcie->global + GLB_MSI_ENABLE);
	writel(readl(pcie->global + GLB_IRQ_STATUS), pcie->global + GLB_IRQ_STATUS);
	/* INTx lines are admitted by their IRQ chip only after Linux maps them. */
	writel(pp->has_msi_ctrl ? GLB_MSI_IRQ : 0, pcie->global + GLB_IRQ_ENABLE);
}
static const struct dw_pcie_host_ops rtl_pcie_host_ops = {
	.init = rtl_pcie_host_start,
	.deinit = rtl_pcie_host_stop,
	.msi_init = rtl_pcie_msi_init,
	.post_init = rtl_pcie_post_init,
};
static const struct dw_pcie_ops rtl_pcie_ops = {
	.link_up = rtl_pcie_link_up,
	.start_link = rtl_pcie_link_start,
	.stop_link = rtl_pcie_link_stop,
};
static void rtl_pcie_intx_mask_set(struct irq_data *data, bool enabled)
{
	struct rtl_pcie *pcie = irq_data_get_irq_chip_data(data);
	unsigned long flags;
	u32 value, mask = BIT(data->hwirq * 2);

	raw_spin_lock_irqsave(&pcie->irq_lock, flags);
	value = readl(pcie->global + GLB_IRQ_ENABLE);
	writel(enabled ? value | mask : value & ~mask, pcie->global + GLB_IRQ_ENABLE);
	raw_spin_unlock_irqrestore(&pcie->irq_lock, flags);
}
static void rtl_pcie_intx_mask(struct irq_data *data)
{
	rtl_pcie_intx_mask_set(data, false);
}
static void rtl_pcie_intx_unmask(struct irq_data *data)
{
	rtl_pcie_intx_mask_set(data, true);
}
static struct irq_chip rtl_pcie_intx_chip = {
	.name = "rtl9617c-pcie-intx",
	.irq_mask = rtl_pcie_intx_mask,
	.irq_unmask = rtl_pcie_intx_unmask,
};
static int rtl_pcie_intx_map(struct irq_domain *domain, unsigned int irq, irq_hw_number_t pin)
{
	irq_set_chip_data(irq, domain->host_data);
	irq_set_chip_and_handler(irq, &rtl_pcie_intx_chip, handle_level_irq);
	return 0;
}
static const struct irq_domain_ops rtl_pcie_intx_ops = {
	.map = rtl_pcie_intx_map,
	.xlate = irq_domain_xlate_onecell,
};
static irqreturn_t rtl_pcie_irq(int irq, void *data)
{
	struct rtl_pcie *pcie = data;
	u32 status = readl(pcie->global + GLB_IRQ_STATUS);
	unsigned int pin;

	if (!(status & (GLB_INTX_IRQS | GLB_MSI_IRQ)))
		return IRQ_NONE;
	if (status & GLB_MSI_IRQ)
		dw_handle_msi_irq(&pcie->pci.pp);
	for (pin = 0; pin < 4; pin++)
		if (status & BIT(pin * 2))
			generic_handle_domain_irq(pcie->intx, pin);
	writel(status, pcie->global + GLB_IRQ_STATUS);
	return IRQ_HANDLED;
}
static void rtl_pcie_phy_exit(void *data)
{
	phy_exit(data);
}
static int rtl_pcie_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *child;
	struct rtl_pcie *pcie;
	struct resource *serdes;
	const char *profile;
	unsigned int i;
	int ret;

	pcie = devm_kzalloc(dev, sizeof(*pcie), GFP_KERNEL);
	if (!pcie)
		return -ENOMEM;
	pcie->pci.dev = dev;
	pcie->pci.ops = &rtl_pcie_ops;
	pcie->pci.pp.ops = &rtl_pcie_host_ops;
	pcie->pci.num_lanes = 1;
	pcie->global = devm_platform_ioremap_resource_byname(pdev, "global");
	if (IS_ERR(pcie->global))
		return PTR_ERR(pcie->global);
	pcie->serdes = devm_platform_ioremap_resource_byname(pdev, "serdes");
	if (IS_ERR(pcie->serdes))
		return PTR_ERR(pcie->serdes);
	pcie->coherent_atu = devm_platform_ioremap_resource_byname(pdev, "coherent-atu");
	if (IS_ERR(pcie->coherent_atu))
		return PTR_ERR(pcie->coherent_atu);
	pcie->clock = devm_clk_get(dev, NULL);
	if (IS_ERR(pcie->clock))
		return dev_err_probe(dev, PTR_ERR(pcie->clock), "PCIe clock\n");
	pcie->core_reset = devm_reset_control_get_exclusive(dev, "core");
	if (IS_ERR(pcie->core_reset))
		return dev_err_probe(dev, PTR_ERR(pcie->core_reset), "PCIe core reset\n");
	pcie->phy_reset = devm_reset_control_get_exclusive(dev, "phy");
	if (IS_ERR(pcie->phy_reset))
		return dev_err_probe(dev, PTR_ERR(pcie->phy_reset), "PCIe PHY reset\n");
	pcie->endpoint_reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(pcie->endpoint_reset))
		return dev_err_probe(dev, PTR_ERR(pcie->endpoint_reset), "PCIe endpoint reset\n");
	pcie->phy = devm_phy_get(dev, "pcie");
	if (IS_ERR(pcie->phy))
		return dev_err_probe(dev, PTR_ERR(pcie->phy), "PCIe PHY\n");
	ret = phy_init(pcie->phy);
	if (ret)
		return ret;
	ret = devm_add_action_or_reset(dev, rtl_pcie_phy_exit, pcie->phy);
	if (ret)
		return ret;
	ret = device_property_read_u32(dev, "realtek,controller-index", &pcie->index);
	if (ret || pcie->index > 1)
		return -EINVAL;
	ret = device_property_read_u32(dev, "realtek,ddr-bytes", &pcie->ddr_bytes);
	if (ret || pcie->ddr_bytes < SZ_64K || !is_power_of_2(pcie->ddr_bytes))
		return -EINVAL;
	ret = device_property_read_u32(dev, "realtek,coherent-hi-addr", &pcie->alias);
	if (ret)
		return ret;
	ret = device_property_read_u32(dev, "reset-delay-ms", &pcie->endpoint_ready_ms);
	if (ret)
		return ret;
	ret = device_property_read_string(dev, "realtek,serdes-profile", &profile);
	if (ret || (strcmp(profile, "A") && strcmp(profile, "B")))
		return -EINVAL;
	pcie->profile = profile[0];
	ret = device_property_count_u32(dev, pcie->profile == 'A' ? "serdes-cfg-dataA" : "serdes-cfg-dataB");
	if (ret <= 0 || ret & 1)
		return -EINVAL;
	pcie->parameter_words = ret;
	pcie->parameters = devm_kcalloc(dev, ret, sizeof(u32), GFP_KERNEL);
	if (!pcie->parameters)
		return -ENOMEM;
	ret = device_property_read_u32_array(dev,
		pcie->profile == 'A' ? "serdes-cfg-dataA" : "serdes-cfg-dataB",
		pcie->parameters, pcie->parameter_words);
	if (ret)
		return ret;
	serdes = platform_get_resource_byname(pdev, IORESOURCE_MEM, "serdes");
	for (i = 0; i < pcie->parameter_words; i += 2)
		if (pcie->parameters[i] & 3 || pcie->parameters[i] > resource_size(serdes) - 4)
			return -EINVAL;
	writel(0, pcie->global + GLB_IRQ_ENABLE);
	writel(0, pcie->global + GLB_MSI_ENABLE);
	pcie->irq = platform_get_irq(pdev, 0);
	if (pcie->irq < 0)
		return pcie->irq;
	raw_spin_lock_init(&pcie->irq_lock);
	child = of_get_child_by_name(dev->of_node, "interrupt-controller");
	pcie->intx = irq_domain_add_linear(child, 4, &rtl_pcie_intx_ops, pcie);
	of_node_put(child);
	if (!pcie->intx)
		return -ENOMEM;
	platform_set_drvdata(pdev, pcie);
	ret = devm_request_irq(dev, pcie->irq, rtl_pcie_irq, 0, dev_name(dev), pcie);
	if (ret)
		goto remove_domain;
	ret = dw_pcie_host_init(&pcie->pci.pp);
	if (!ret)
		return 0;
	writel(0, pcie->global + GLB_IRQ_ENABLE);
	synchronize_irq(pcie->irq);
remove_domain:
	irq_domain_remove(pcie->intx);
	return ret;
}
static void rtl_pcie_remove(struct platform_device *pdev)
{
	struct rtl_pcie *pcie = platform_get_drvdata(pdev);

	writel(0, pcie->global + GLB_IRQ_ENABLE);
	synchronize_irq(pcie->irq);
	dw_pcie_host_deinit(&pcie->pci.pp);
	irq_domain_remove(pcie->intx);
}
static void rtl_pcie_shutdown(struct platform_device *pdev)
{
	struct rtl_pcie *pcie = platform_get_drvdata(pdev);

	writel(0, pcie->global + GLB_IRQ_ENABLE);
	synchronize_irq(pcie->irq);
	rtl_pcie_link_stop(&pcie->pci);
	rtl_pcie_power_stop(pcie);
}
static const struct of_device_id rtl_pcie_match[] = {
	{.compatible = "realtek,rtl9617c-pcie"},
	{},
};
MODULE_DEVICE_TABLE(of, rtl_pcie_match);
static struct platform_driver rtl_pcie_driver = {
	.probe = rtl_pcie_probe,
	.remove = rtl_pcie_remove,
	.shutdown = rtl_pcie_shutdown,
	.driver = {.name = "rtl9617c-pcie", .of_match_table = rtl_pcie_match},
};
module_platform_driver(rtl_pcie_driver);
MODULE_DESCRIPTION("Native RTL9617C DesignWare PCIe root complex");
MODULE_LICENSE("GPL");
