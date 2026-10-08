// SPDX-License-Identifier: GPL-2.0-or-later
/* Taurus five-bank GPIO; Cortina GPL gpio-ca77xx.c register map. */
#include <linux/gpio/driver.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/spinlock.h>

struct rtl_gpio { void __iomem *base; spinlock_t lock; struct gpio_chip chip; };

static void __iomem *rtl_gpio_reg(struct rtl_gpio *gpio, unsigned int pin, unsigned int reg)
{
	return gpio->base + (pin / 32) * 0x24 + reg;
}

static void rtl_gpio_update(struct rtl_gpio *gpio, unsigned int pin, unsigned int reg, bool set)
{
	void __iomem *address = rtl_gpio_reg(gpio, pin, reg);
	u32 mask = BIT(pin % 32), value = readl(address);

	writel(set ? value | mask : value & ~mask, address);
}

static int rtl_gpio_get(struct gpio_chip *chip, unsigned int pin)
{
	struct rtl_gpio *gpio = gpiochip_get_data(chip);

	return !!(readl(rtl_gpio_reg(gpio, pin, 8)) & BIT(pin % 32));
}

static int rtl_gpio_set(struct gpio_chip *chip, unsigned int pin, int value)
{
	struct rtl_gpio *gpio = gpiochip_get_data(chip);
	unsigned long flags;

	spin_lock_irqsave(&gpio->lock, flags);
	rtl_gpio_update(gpio, pin, 4, value);
	spin_unlock_irqrestore(&gpio->lock, flags);
	return 0;
}

static int rtl_gpio_direction(struct gpio_chip *chip, unsigned int pin)
{
	struct rtl_gpio *gpio = gpiochip_get_data(chip);

	return readl(rtl_gpio_reg(gpio, pin, 0)) & BIT(pin % 32) ?
		GPIO_LINE_DIRECTION_IN : GPIO_LINE_DIRECTION_OUT;
}

static int rtl_gpio_input(struct gpio_chip *chip, unsigned int pin)
{
	struct rtl_gpio *gpio = gpiochip_get_data(chip);
	unsigned long flags;

	spin_lock_irqsave(&gpio->lock, flags);
	rtl_gpio_update(gpio, pin, 0, true);
	spin_unlock_irqrestore(&gpio->lock, flags);
	return 0;
}

static int rtl_gpio_output(struct gpio_chip *chip, unsigned int pin, int value)
{
	struct rtl_gpio *gpio = gpiochip_get_data(chip);
	unsigned long flags;

	spin_lock_irqsave(&gpio->lock, flags);
	rtl_gpio_update(gpio, pin, 4, value);
	rtl_gpio_update(gpio, pin, 0, false);
	spin_unlock_irqrestore(&gpio->lock, flags);
	return 0;
}

static int rtl_gpio_probe(struct platform_device *pdev)
{
	struct rtl_gpio *gpio = devm_kzalloc(&pdev->dev, sizeof(*gpio), GFP_KERNEL);
	int ret;

	if (!gpio)
		return -ENOMEM;
	gpio->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(gpio->base))
		return PTR_ERR(gpio->base);
	spin_lock_init(&gpio->lock);
	gpio->chip = (struct gpio_chip) {
		.label = "rtl9617c-gpio", .parent = &pdev->dev, .owner = THIS_MODULE,
		.base = -1, .ngpio = 160,
		.request = gpiochip_generic_request, .free = gpiochip_generic_free,
		.get = rtl_gpio_get, .set = rtl_gpio_set, .get_direction = rtl_gpio_direction,
		.direction_input = rtl_gpio_input, .direction_output = rtl_gpio_output,
	};
	ret = devm_gpiochip_add_data(&pdev->dev, &gpio->chip, gpio);
	if (ret)
		return ret;
	if (!pdev->dev.of_node) {
		ret = gpiochip_add_pin_range(&gpio->chip, "4f43200f0.pinctrl", 0, 0, 160);
		if (ret)
			return ret;
	}
	dev_info(&pdev->dev, "native GPIO0..159, five 32-bit banks\n");
	return 0;
}

static const struct of_device_id rtl_gpio_match[] = {
	{ .compatible = "realtek,rtl9617c-gpio" }, { }
};
MODULE_DEVICE_TABLE(of, rtl_gpio_match);
static struct platform_driver rtl_gpio_driver = {
	.probe = rtl_gpio_probe,
	.driver = { .name = "rtl9617c-gpio", .of_match_table = rtl_gpio_match },
};
module_platform_driver(rtl_gpio_driver);
MODULE_DESCRIPTION("Native RTL9617C banked GPIO");
MODULE_LICENSE("GPL");
