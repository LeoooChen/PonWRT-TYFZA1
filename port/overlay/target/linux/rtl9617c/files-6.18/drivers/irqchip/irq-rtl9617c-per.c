// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Copyright (c) Cortina-Access Limited 2015. All rights reserved.
 * Port of irq-ca-peri-ictl.c, GPL-for-GP3000
 * 5090fbcb6ba743dd7b1314811ef557bad0460147, CPU0 peripheral interrupt bank.
 */
#include <linux/io.h>
#include <linux/irq.h>
#include <linux/irqchip.h>
#include <linux/irqchip/chained_irq.h>
#include <linux/of_address.h>
#include <linux/of_irq.h>

#define PER_STATUS	0x00
#define PER_ENABLE	0x04

static void rtl_per_irq_handler(struct irq_desc *desc)
{
	struct irq_domain *domain = irq_desc_get_handler_data(desc);
	struct irq_chip_generic *gc = irq_get_domain_generic_chip(domain, 0);
	struct irq_chip *chip = irq_desc_get_chip(desc);
	u32 pending;

	chained_irq_enter(chip, desc);
	pending = readl(gc->reg_base + PER_STATUS) & readl(gc->reg_base + PER_ENABLE);
	while (pending) {
		unsigned int hwirq = __ffs(pending);

		generic_handle_domain_irq(domain, hwirq);
		pending &= ~BIT(hwirq);
	}
	chained_irq_exit(chip, desc);
}

static int __init rtl_per_irq_init(struct device_node *node,
				  struct device_node *parent)
{
	struct irq_chip_generic *gc;
	struct irq_domain *domain;
	struct resource res;
	void __iomem *base;
	int irq, ret;

	irq = irq_of_parse_and_map(node, 0);
	if (!irq)
		return -EINVAL;
	ret = of_address_to_resource(node, 0, &res);
	if (ret)
		return ret;
	if (resource_size(&res) < 8)
		return -EINVAL;
	if (!request_mem_region(res.start, resource_size(&res), node->full_name))
		return -EBUSY;
	base = ioremap(res.start, resource_size(&res));
	if (!base) {
		ret = -ENOMEM;
		goto release;
	}
	writel(0, base + PER_ENABLE);
	domain = irq_domain_add_linear(node, 32, &irq_generic_chip_ops, NULL);
	if (!domain) {
		ret = -ENOMEM;
		goto unmap;
	}
	ret = irq_alloc_domain_generic_chips(domain, 32, 1, "rtl9617c-per",
					     handle_level_irq,
					     IRQ_NOREQUEST | IRQ_NOPROBE | IRQ_NOAUTOEN,
					     0, IRQ_GC_INIT_MASK_CACHE);
	if (ret)
		goto remove_domain;
	gc = irq_get_domain_generic_chip(domain, 0);
	gc->reg_base = base;
	gc->chip_types[0].regs.ack = PER_STATUS;
	gc->chip_types[0].regs.mask = PER_ENABLE;
	gc->chip_types[0].chip.irq_ack = irq_gc_ack_set_bit;
	/* Hardware enable bit is active-high, unlike a conventional mask. */
	gc->chip_types[0].chip.irq_mask = irq_gc_mask_clr_bit;
	gc->chip_types[0].chip.irq_unmask = irq_gc_mask_set_bit;
	irq_set_chained_handler_and_data(irq, rtl_per_irq_handler, domain);
	return 0;

remove_domain:
	irq_domain_remove(domain);
unmap:
	iounmap(base);
release:
	release_mem_region(res.start, resource_size(&res));
	return ret;
}
IRQCHIP_DECLARE(rtl9617c_per, "realtek,rtl9617c-per-ictl", rtl_per_irq_init);
