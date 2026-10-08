// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Taurus pinmux, from GPL Cortina pinctrl and the TYFZA1 stock DT. */
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pinctrl/pinconf-generic.h>
#include <linux/pinctrl/pinctrl.h>
#include <linux/pinctrl/pinmux.h>
#include <linux/spinlock.h>

#define RTL_PIN_COUNT 224
#define RTL_GPIO_PINS 160

struct rtl9617c_pinctrl {
	void __iomem *base;
	spinlock_t lock;
	struct pinctrl_desc descriptor;
	struct pinctrl_pin_desc pins[RTL_PIN_COUNT];
	const char *names[RTL_PIN_COUNT];
	unsigned int count;
};

static int rtl_pin_count(struct pinctrl_dev *dev)
{
	struct rtl9617c_pinctrl *controller = pinctrl_dev_get_drvdata(dev);

	return controller->count;
}

static const char *rtl_pin_name(struct pinctrl_dev *dev, unsigned int group)
{
	struct rtl9617c_pinctrl *controller = pinctrl_dev_get_drvdata(dev);

	return controller->names[group];
}

static int rtl_pin_group(struct pinctrl_dev *dev, unsigned int group,
			 const unsigned int **pins, unsigned int *count)
{
	struct rtl9617c_pinctrl *controller = pinctrl_dev_get_drvdata(dev);

	*pins = &controller->pins[group].number;
	*count = 1;
	return 0;
}

static const struct pinctrl_ops rtl_pin_ops = {
	.get_groups_count = rtl_pin_count,
	.get_group_name = rtl_pin_name,
	.get_group_pins = rtl_pin_group,
	.dt_node_to_map = pinconf_generic_dt_node_to_map_group,
	.dt_free_map = pinconf_generic_dt_free_map,
};

static int rtl_function_count(struct pinctrl_dev *dev)
{
	return 2;
}

static const char *rtl_function_name(struct pinctrl_dev *dev, unsigned int function)
{
	return function ? "multi-fn" : "gpio";
}

static int rtl_function_groups(struct pinctrl_dev *dev, unsigned int function,
			       const char * const **groups, unsigned int *count)
{
	struct rtl9617c_pinctrl *controller = pinctrl_dev_get_drvdata(dev);

	*groups = controller->names;
	*count = controller->count;
	return 0;
}

static int rtl_set_mux(struct pinctrl_dev *dev, unsigned int function, unsigned int group)
{
	struct rtl9617c_pinctrl *controller = pinctrl_dev_get_drvdata(dev);
	unsigned int pin = controller->pins[group].number;
	bool pmux = pin >= RTL_GPIO_PINS;
	unsigned int reg = pmux ? (pin - RTL_GPIO_PINS) / 32 * 4 : 0x10 + pin / 32 * 4;
	u32 mask = BIT(pin % 32), value;
	unsigned long flags;
	bool set = pmux ? function == 1 : function == 0;

	spin_lock_irqsave(&controller->lock, flags);
	value = readl(controller->base + reg);
	writel(set ? value | mask : value & ~mask, controller->base + reg);
	spin_unlock_irqrestore(&controller->lock, flags);
	return 0;
}

static const struct pinmux_ops rtl_mux_ops = {
	.get_functions_count = rtl_function_count,
	.get_function_name = rtl_function_name,
	.get_function_groups = rtl_function_groups,
	.set_mux = rtl_set_mux,
	.strict = true,
};

static int rtl_pinctrl_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rtl9617c_pinctrl *controller;
	struct pinctrl_dev *pinctrl;
	unsigned int pin;

	controller = devm_kzalloc(dev, sizeof(*controller), GFP_KERNEL);
	if (!controller)
		return -ENOMEM;
	controller->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(controller->base))
		return PTR_ERR(controller->base);
	spin_lock_init(&controller->lock);
	for (pin = 0; pin < RTL_PIN_COUNT; pin++) {
		unsigned int local = pin < RTL_GPIO_PINS ? pin : pin - RTL_GPIO_PINS;
		unsigned int index = controller->count;

		/* These GPIO1 pads do not exist on Taurus; retain hardware pin IDs. */
		if ((pin >= 36 && pin <= 41) || (pin >= 43 && pin <= 46))
			continue;
		controller->names[index] = devm_kasprintf(dev, GFP_KERNEL, "%s_%u%02u",
			pin < RTL_GPIO_PINS ? "GPIO" : "PMUX", local / 32, local % 32);
		if (!controller->names[index])
			return -ENOMEM;
		controller->pins[index].number = pin;
		controller->pins[index].name = controller->names[index];
		controller->count++;
	}
	controller->descriptor.name = dev_name(dev);
	controller->descriptor.owner = THIS_MODULE;
	controller->descriptor.pins = controller->pins;
	controller->descriptor.npins = controller->count;
	controller->descriptor.pctlops = &rtl_pin_ops;
	controller->descriptor.pmxops = &rtl_mux_ops;
	pinctrl = devm_pinctrl_register(dev, &controller->descriptor, controller);
	return PTR_ERR_OR_ZERO(pinctrl);
}

static const struct of_device_id rtl_pinctrl_match[] = {
	{ .compatible = "realtek,rtl9617c-pinctrl" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_pinctrl_match);

static struct platform_driver rtl_pinctrl_driver = {
	.probe = rtl_pinctrl_probe,
	.driver = {
		.name = "rtl9617c-pinctrl",
		.of_match_table = rtl_pinctrl_match,
	},
};
module_platform_driver(rtl_pinctrl_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("RTL9617C native pinmux controller");
