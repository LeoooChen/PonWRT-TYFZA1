// SPDX-License-Identifier: GPL-2.0-only
/*
 * RTL9617C peripheral watchdog, ported from cortina_per_wdt.c.
 * Copyright (C) 2017 Cortina Access, Inc.
 * Hardware sequence: GPL-for-GP3000 5090fbcb6ba743dd7b1314811ef557bad0460147.
 * Use watchdog core/procd instead of vendor SDK hooks or private kick threads.
 */
#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/mfd/syscon.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/watchdog.h>

#define WDT_CTRL	0x00
#define WDT_ENABLE	BIT(0)
#define WDT_RESET	BIT(1)
#define WDT_CLKSEL	BIT(2)
#define WDT_DELAY	GENMASK(31, 12)
#define WDT_PS		0x04
#define WDT_DIV		0x08
#define WDT_LOAD	0x0c
#define WDT_RELOAD	0x10
#define WDT_COUNT	0x14
#define WDT_IRQ_ENABLE	0x18
#define WDT_IRQ_PENDING	0x1c
#define WDT_RELOAD_BOTH	(BIT(0) | BIT(1))
/* Vendor Taurus reset routing: subsystem, PCIe, all blocks, remap, external. */
#define WDT_RESET_ROUTE	GENMASK(8, 4)

struct rtl9617c_wdt {
	void __iomem *base;
	struct regmap *reset;
	u32 prescaler;
	u32 delay_cycles;
	spinlock_t lock;
	struct watchdog_device wdd;
};

static int rtl_wdt_ping(struct watchdog_device *wdd)
{
	struct rtl9617c_wdt *wdt = watchdog_get_drvdata(wdd);

	writel(WDT_RELOAD_BOTH, wdt->base + WDT_RELOAD);
	return 0;
}

static int rtl_wdt_start(struct watchdog_device *wdd)
{
	struct rtl9617c_wdt *wdt = watchdog_get_drvdata(wdd);
	unsigned long flags;
	u32 ctrl;
	int ret;

	ret = regmap_update_bits(wdt->reset, 0, WDT_RESET_ROUTE, WDT_RESET_ROUTE);
	if (ret)
		return ret;
	spin_lock_irqsave(&wdt->lock, flags);
	ctrl = readl(wdt->base + WDT_CTRL);
	writel(ctrl & ~WDT_ENABLE, wdt->base + WDT_CTRL);
	/* Vendor normal-resolution path: APB/1000 - 1, then divide by 1000. */
	writel(wdt->prescaler, wdt->base + WDT_PS);
	writel(1000, wdt->base + WDT_DIV);
	writel(wdd->timeout, wdt->base + WDT_LOAD);
	writel(WDT_RELOAD_BOTH, wdt->base + WDT_RELOAD);
	writel(1, wdt->base + WDT_IRQ_PENDING);
	writel(1, wdt->base + WDT_IRQ_ENABLE);
	ctrl &= ~(WDT_CLKSEL | WDT_DELAY);
	ctrl |= WDT_ENABLE | WDT_RESET | FIELD_PREP(WDT_DELAY, wdt->delay_cycles);
	writel(ctrl, wdt->base + WDT_CTRL);
	spin_unlock_irqrestore(&wdt->lock, flags);
	return 0;
}

static int rtl_wdt_stop(struct watchdog_device *wdd)
{
	struct rtl9617c_wdt *wdt = watchdog_get_drvdata(wdd);
	unsigned long flags;

	spin_lock_irqsave(&wdt->lock, flags);
	writel(readl(wdt->base + WDT_CTRL) & ~WDT_ENABLE, wdt->base + WDT_CTRL);
	writel(0, wdt->base + WDT_IRQ_ENABLE);
	spin_unlock_irqrestore(&wdt->lock, flags);
	return 0;
}

static int rtl_wdt_set_timeout(struct watchdog_device *wdd, unsigned int timeout)
{
	wdd->timeout = timeout;
	if (watchdog_active(wdd) || watchdog_hw_running(wdd))
		return rtl_wdt_start(wdd);
	return 0;
}

static unsigned int rtl_wdt_get_timeleft(struct watchdog_device *wdd)
{
	struct rtl9617c_wdt *wdt = watchdog_get_drvdata(wdd);

	return readl(wdt->base + WDT_COUNT);
}

static irqreturn_t rtl_wdt_irq(int irq, void *arg)
{
	struct rtl9617c_wdt *wdt = arg;

	spin_lock(&wdt->lock);
	if (!(readl(wdt->base + WDT_IRQ_PENDING) & BIT(0))) {
		spin_unlock(&wdt->lock);
		return IRQ_NONE;
	}
	writel(1, wdt->base + WDT_IRQ_PENDING);
	writel(0, wdt->base + WDT_IRQ_ENABLE);
	spin_unlock(&wdt->lock);
	/* Hardware resets after its configured delay; do not reload here. */
	return IRQ_HANDLED;
}

static const struct watchdog_info rtl_wdt_info = {
	.identity = "RTL9617C peripheral watchdog",
	.options = WDIOF_SETTIMEOUT | WDIOF_KEEPALIVEPING | WDIOF_MAGICCLOSE,
};

static const struct watchdog_ops rtl_wdt_ops = {
	.owner = THIS_MODULE,
	.start = rtl_wdt_start,
	.stop = rtl_wdt_stop,
	.ping = rtl_wdt_ping,
	.set_timeout = rtl_wdt_set_timeout,
	.get_timeleft = rtl_wdt_get_timeleft,
};

static int rtl_wdt_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rtl9617c_wdt *wdt;
	struct clk *clk;
	unsigned long rate;
	u32 delay_ms;
	bool running;
	int irq, ret;

	if (!of_property_read_bool(dev->of_node, "reset-on-timeout"))
		return dev_err_probe(dev, -EINVAL, "reset-on-timeout is required\n");
	wdt = devm_kzalloc(dev, sizeof(*wdt), GFP_KERNEL);
	if (!wdt)
		return -ENOMEM;
	wdt->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(wdt->base))
		return PTR_ERR(wdt->base);
	wdt->reset = syscon_regmap_lookup_by_phandle(dev->of_node, "realtek,global-config");
	if (IS_ERR(wdt->reset))
		return PTR_ERR(wdt->reset);
	clk = devm_clk_get_enabled(dev, NULL);
	if (IS_ERR(clk))
		return dev_err_probe(dev, PTR_ERR(clk), "watchdog clock unavailable\n");
	rate = clk_get_rate(clk);
	if (rate < 16000 || rate % 1000 || rate / 1000 > U32_MAX)
		return dev_err_probe(dev, -EINVAL, "unsupported APB clock\n");
	wdt->prescaler = rate / 1000 - 1;
	ret = of_property_read_u32(dev->of_node, "delay-reset", &delay_ms);
	if (ret)
		return dev_err_probe(dev, ret, "board reset delay is required\n");
	/* TYFZA1 uses the vendor default delay type, in APB/16 cycles. */
	wdt->delay_cycles = min_t(u64, (u64)delay_ms * (rate / 16000),
				 FIELD_MAX(WDT_DELAY));
	spin_lock_init(&wdt->lock);
	irq = platform_get_irq(pdev, 0);
	if (irq < 0)
		return irq;
	ret = devm_request_irq(dev, irq, rtl_wdt_irq, 0, dev_name(dev), wdt);
	if (ret)
		return ret;
	wdt->wdd.info = &rtl_wdt_info;
	wdt->wdd.ops = &rtl_wdt_ops;
	wdt->wdd.parent = dev;
	wdt->wdd.min_timeout = 1;
	wdt->wdd.max_timeout = UINT_MAX / 1000;
	wdt->wdd.timeout = 60;
	watchdog_set_drvdata(&wdt->wdd, wdt);
	ret = watchdog_init_timeout(&wdt->wdd, 0, dev);
	if (ret)
		return ret;
	watchdog_set_nowayout(&wdt->wdd, WATCHDOG_NOWAYOUT);
	watchdog_stop_on_unregister(&wdt->wdd);
	running = readl(wdt->base + WDT_CTRL) & WDT_ENABLE;
	if (running) {
		/* Trial-slot boot enables WDT in U-Boot. Core must bridge to procd. */
		rtl_wdt_start(&wdt->wdd);
		set_bit(WDOG_HW_RUNNING, &wdt->wdd.status);
	}
	ret = devm_watchdog_register_device(dev, &wdt->wdd);
	if (!ret)
		dev_info(dev, "vendor watchdog %s; native watchdog-core ownership\n",
			 running ? "taken over" : "idle");
	return ret;
}

static const struct of_device_id rtl_wdt_match[] = {
	{ .compatible = "realtek,rtl9617c-wdt" }, { }
};
MODULE_DEVICE_TABLE(of, rtl_wdt_match);

static struct platform_driver rtl_wdt_driver = {
	.probe = rtl_wdt_probe,
	.driver = { .name = "rtl9617c-wdt", .of_match_table = rtl_wdt_match },
};
module_platform_driver(rtl_wdt_driver);
MODULE_DESCRIPTION("RTL9617C watchdog with native bootloader handoff");
MODULE_LICENSE("GPL");
