// SPDX-License-Identifier: GPL-2.0-only
/* TYFZA1 CPU clock, rewritten from its stock 5.10.70 clock operations.
 * CPLL also parents CPU trace. FPLL, PE and AXI controls stay untouched.
 */
#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/clk-provider.h>
#include <linux/cpu.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_opp.h>
#include <linux/slab.h>

#define CPLLDIV		0x00
#define CPLLMUX		0x04
#define PEDIV		0x10
#define CPLL0		0x30
#define FPLL0		0x40
#define CORTEX_DIV	GENMASK(5, 1)
#define DIV_OVERRIDE	BIT(6)
#define F2C_DIV		GENMASK(10, 8)
#define CPU_FROM_FPLL	BIT(11)
#define MODE_OVERRIDE	BIT(31)
#define PLL_DIVN		GENMASK(7, 0)
#define PLL_RESET	GENMASK(11, 10)
#define PLL_PREDIV	GENMASK(13, 12)

struct rtl_cpu_rate {
	unsigned long hz;
	u16 divn;
	u8 prediv, divider;
};

static const struct rtl_cpu_rate rtl_cpu_rates[] = {
	{  400000000, 240, 5, 3 },
	{  625000000, 250, 5, 2 },
	{  700000000, 168, 3, 2 },
	{  800000000, 192, 3, 2 },
	{  900000000, 216, 3, 2 },
	{ 1000000000, 200, 5, 1 },
	{ 1100000000, 220, 5, 1 },
	{ 1200000000, 240, 5, 1 },
};

struct rtl_cpu_clock {
	struct clk_hw hw;
	void __iomem *base;
	struct mutex lock;
	int transition_error;
};

#define to_rtl_cpu(clock_hw) container_of(clock_hw, struct rtl_cpu_clock, hw)

static unsigned long rtl_cpu_read_rate(struct rtl_cpu_clock *cpu,
				      unsigned long parent_rate)
{
	u32 divider = readl(cpu->base + CPLLDIV);
	u32 pll = readl(cpu->base + (divider & CPU_FROM_FPLL ? FPLL0 : CPLL0));
	unsigned long rate = parent_rate * (FIELD_GET(PLL_DIVN, pll) + 3) /
		(FIELD_GET(PLL_PREDIV, pll) + 2);
	u32 div = FIELD_GET(CORTEX_DIV, divider);

	if (!(pll & PLL_RESET))
		return 0;
	if (divider & CPU_FROM_FPLL)
		rate /= max_t(u32, FIELD_GET(F2C_DIV, divider), 1);
	return rate / max_t(u32, div, 1);
}

static unsigned long rtl_cpu_recalc_rate(struct clk_hw *hw,
					unsigned long parent_rate)
{
	struct rtl_cpu_clock *cpu = to_rtl_cpu(hw);
	unsigned long rate;

	mutex_lock(&cpu->lock);
	rate = rtl_cpu_read_rate(cpu, parent_rate);
	mutex_unlock(&cpu->lock);
	return rate;
}

static int rtl_cpu_determine_rate(struct clk_hw *hw, struct clk_rate_request *req)
{
	unsigned long best = 0, distance = ULONG_MAX;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(rtl_cpu_rates); i++) {
		unsigned long hz = rtl_cpu_rates[i].hz;
		unsigned long delta = hz > req->rate ? hz - req->rate : req->rate - hz;

		if (hz >= req->min_rate && hz <= req->max_rate && delta < distance) {
			best = hz;
			distance = delta;
		}
	}
	if (!best)
		return -EINVAL;
	req->rate = best;
	return 0;
}

static int rtl_cpu_program_rate(struct rtl_cpu_clock *cpu, unsigned long rate,
				unsigned long parent_rate)
{
	const struct rtl_cpu_rate *target = NULL;
	u32 divider, pll;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(rtl_cpu_rates); i++)
		if (rate == rtl_cpu_rates[i].hz) {
			target = &rtl_cpu_rates[i];
			break;
		}
	if (!target)
		return -EINVAL;
	/* Matching Hz on FPLL is an unfinished transition, not a no-op. */
	if (!(readl(cpu->base + CPLLDIV) & CPU_FROM_FPLL) &&
	    rtl_cpu_read_rate(cpu, parent_rate) == rate)
		return 0;
	/* PE normally uses FPLL. Changing CPLL while PE selects it would
	 * change the packet engine's clock as well. */
	if (readl(cpu->base + PEDIV) & BIT(6))
		return -EBUSY;
	if (!(readl(cpu->base + FPLL0) & PLL_RESET))
		return -EIO;

	divider = readl(cpu->base + CPLLDIV);
	writel(divider | CPU_FROM_FPLL, cpu->base + CPLLDIV);
	/* Never reset the PLL still supplying the CPU. */
	if (readl(cpu->base + CPLLDIV) != (divider | CPU_FROM_FPLL))
		return -EIO;
	pll = readl(cpu->base + CPLL0) & ~PLL_RESET;
	writel(pll, cpu->base + CPLL0);
	if (readl(cpu->base + CPLL0) & PLL_RESET)
		return -EIO;
	pll &= ~(PLL_DIVN | PLL_PREDIV);
	pll |= FIELD_PREP(PLL_DIVN, target->divn - 3) |
		FIELD_PREP(PLL_PREDIV, target->prediv - 2);
	writel(pll, cpu->base + CPLL0);
	if ((readl(cpu->base + CPLL0) & (PLL_DIVN | PLL_PREDIV | PLL_RESET)) !=
	    (pll & (PLL_DIVN | PLL_PREDIV | PLL_RESET)))
		return -EIO;
	divider |= CPU_FROM_FPLL | MODE_OVERRIDE;
	writel(divider, cpu->base + CPLLDIV);
	if (readl(cpu->base + CPLLDIV) != divider)
		return -EIO;
	writel(pll | FIELD_PREP(PLL_RESET, 1), cpu->base + CPLL0);
	if (FIELD_GET(PLL_RESET, readl(cpu->base + CPLL0)) != 1)
		return -EIO;
	udelay(200);
	divider &= ~GENMASK(5, 0);
	divider |= FIELD_PREP(CORTEX_DIV, target->divider) | DIV_OVERRIDE;
	writel(divider, cpu->base + CPLLDIV);
	if (readl(cpu->base + CPLLDIV) != divider)
		return -EIO;
	writel(divider & ~CPU_FROM_FPLL, cpu->base + CPLLDIV);
	if (readl(cpu->base + CPLLDIV) != (divider & ~CPU_FROM_FPLL) ||
	    rtl_cpu_read_rate(cpu, parent_rate) != rate)
		return -EIO;
	return 0;
}

static int rtl_cpu_set_rate(struct clk_hw *hw, unsigned long rate,
			   unsigned long parent_rate)
{
	struct rtl_cpu_clock *cpu = to_rtl_cpu(hw);
	int ret;

	mutex_lock(&cpu->lock);
	ret = rtl_cpu_program_rate(cpu, rate, parent_rate);
	cpu->transition_error = ret;
	mutex_unlock(&cpu->lock);
	return ret;
}

static const struct clk_ops rtl_cpu_ops = {
	.recalc_rate = rtl_cpu_recalc_rate,
	.determine_rate = rtl_cpu_determine_rate,
	.set_rate = rtl_cpu_set_rate,
};

static int rtl_cpu_opp_set_rate(struct device *dev, struct opp_table *table,
			       struct dev_pm_opp *old_opp, struct dev_pm_opp *opp,
			       void *data, bool scaling_down)
{
	unsigned long rate = data ? *(unsigned long *)data : dev_pm_opp_get_freq(opp);
	struct clk *clk = clk_get(dev, "cpu");
	struct rtl_cpu_clock *cpu;
	unsigned long hardware_rate;
	int ret;

	if (IS_ERR(clk))
		return PTR_ERR(clk);
	cpu = to_rtl_cpu(__clk_get_hw(clk));
	/* OPP owns this transition. CCF skips set_rate when Hz already match,
	 * even if the CPU is still on FPLL after a failed transition. Use the
	 * same serialized hardware operation, then refresh CCF's rate cache. */
	ret = rtl_cpu_set_rate(&cpu->hw, rate, clk_get_rate(clk_get_parent(clk)));
	hardware_rate = clk_get_rate(clk);
	if (ret)
		dev_err(dev, "CPU clock change failed: %d, request %lu Hz, hardware %lu Hz\n",
			ret, rate, hardware_rate);
	clk_put(clk);
	return ret;
}

static ssize_t transition_error_show(struct device *dev,
				     struct device_attribute *attr, char *buffer)
{
	struct rtl_cpu_clock *cpu = dev_get_drvdata(dev);
	int error;

	mutex_lock(&cpu->lock);
	error = cpu->transition_error;
	mutex_unlock(&cpu->lock);
	return sysfs_emit(buffer, "%d\n", error);
}
static DEVICE_ATTR_RO(transition_error);

static struct attribute *rtl_cpu_attrs[] = {
	&dev_attr_transition_error.attr,
	NULL,
};
ATTRIBUTE_GROUPS(rtl_cpu);

static int rtl_cpu_probe(struct platform_device *pdev)
{
	static const char * const clock_names[] = { "cpu", NULL };
	struct dev_pm_opp_config opp_config = {
		.clk_names = clock_names,
		.config_clks = rtl_cpu_opp_set_rate,
	};
	struct device *dev = &pdev->dev;
	struct device *cpu_dev = get_cpu_device(0);
	struct clk_parent_data parent = { .index = 0 };
	struct clk_init_data init = {
		.name = "rtl9617c-cpu",
		.ops = &rtl_cpu_ops,
		.parent_data = &parent,
		.num_parents = 1,
		.flags = CLK_GET_RATE_NOCACHE,
	};
	struct rtl_cpu_clock *cpu;
	int ret;

	if (!cpu_dev)
		return -EPROBE_DEFER;
	cpu = devm_kzalloc(dev, sizeof(*cpu), GFP_KERNEL);
	if (!cpu)
		return -ENOMEM;
	mutex_init(&cpu->lock);
	cpu->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(cpu->base))
		return PTR_ERR(cpu->base);
	/* Stock rtl8277c_cpu_init: guard switching to avoid a CPU stall. */
	writel(0xff000000, cpu->base + CPLLMUX);
	if (readl(cpu->base + CPLLMUX) != 0xff000000)
		return -EIO;
	platform_set_drvdata(pdev, cpu);
	cpu->hw.init = &init;
	ret = devm_clk_hw_register(dev, &cpu->hw);
	if (ret)
		return ret;
	ret = devm_of_clk_add_hw_provider(dev, of_clk_hw_simple_get, &cpu->hw);
	if (ret)
		return ret;
	return devm_pm_opp_set_config(cpu_dev, &opp_config);
}

static const struct of_device_id rtl_cpu_match[] = {
	{ .compatible = "realtek,rtl9617c-cpu-clock" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_cpu_match);

static struct platform_driver rtl_cpu_driver = {
	.probe = rtl_cpu_probe,
	.driver = {
		.name = "rtl9617c-cpu-clock",
		.of_match_table = rtl_cpu_match,
		.dev_groups = rtl_cpu_groups,
	},
};
builtin_platform_driver(rtl_cpu_driver);
MODULE_DESCRIPTION("Realtek RTL9617C CPU clock");
MODULE_LICENSE("GPL");
