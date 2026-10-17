// SPDX-License-Identifier: GPL-2.0-only
/* TYFZA1 GPIO LEDs: native output arbitration, from the stock LED controller. */
#include <linux/gpio/consumer.h>
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/tyfza1-led.h>

struct tyfza1_gpio_led {
	struct tyfza1_led led;
	struct gpio_desc *gpio;
};

static DEFINE_MUTEX(tyfza1_led_lock);
static LIST_HEAD(tyfza1_led_list);
static unsigned int tyfza1_led_mode = 2;

static int tyfza1_led_output(struct tyfza1_led *led)
{
	unsigned int mode = tyfza1_led_mode;

	return led->output(led, mode == 2 ? led->requested : mode);
}

static int tyfza1_led_set(struct led_classdev *cdev, enum led_brightness value)
{
	struct tyfza1_led *led = container_of(cdev, struct tyfza1_led, cdev);
	int ret;

	mutex_lock(&tyfza1_led_lock);
	/* Overrides affect physical output, not the evolving automatic state. */
	led->requested = value;
	ret = tyfza1_led_output(led);
	mutex_unlock(&tyfza1_led_lock);
	return ret;
}

int tyfza1_led_register(struct device *dev, struct tyfza1_led *led,
		       struct led_init_data *init)
{
	int ret;

	led->cdev.brightness = led->requested;
	led->cdev.brightness_set_blocking = tyfza1_led_set;
	ret = led_classdev_register_ext(dev, &led->cdev, init);
	if (ret)
		return ret;
	mutex_lock(&tyfza1_led_lock);
	ret = tyfza1_led_output(led);
	if (!ret)
		list_add_tail(&led->node, &tyfza1_led_list);
	mutex_unlock(&tyfza1_led_lock);
	if (ret)
		led_classdev_unregister(&led->cdev);
	return ret;
}
EXPORT_SYMBOL_GPL(tyfza1_led_register);

void tyfza1_led_unregister(struct tyfza1_led *led)
{
	int ret;

	led_classdev_unregister(&led->cdev);
	mutex_lock(&tyfza1_led_lock);
	list_del(&led->node);
	ret = led->output(led, 0);
	if (ret)
		pr_err("TYFZA1 LED %s: shutdown failed: %d\n", led->cdev.name, ret);
	mutex_unlock(&tyfza1_led_lock);
}
EXPORT_SYMBOL_GPL(tyfza1_led_unregister);

static ssize_t mode_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%u\n", READ_ONCE(tyfza1_led_mode));
}

static ssize_t mode_store(struct device *dev, struct device_attribute *attr,
			 const char *buf, size_t count)
{
	struct tyfza1_led *led;
	unsigned int mode;
	int error = 0;
	int ret = kstrtouint(buf, 0, &mode);

	if (ret)
		return ret;
	if (mode > 2)
		return -EINVAL;
	mutex_lock(&tyfza1_led_lock);
	tyfza1_led_mode = mode;
	list_for_each_entry(led, &tyfza1_led_list, node) {
		ret = tyfza1_led_output(led);
		if (ret) {
			dev_err(led->cdev.dev, "mode %u failed: %d\n", mode, ret);
			error = ret;
		}
	}
	mutex_unlock(&tyfza1_led_lock);
	if (error)
		return error;
	return count;
}
static DEVICE_ATTR_RW(mode);

static struct attribute *tyfza1_led_attrs[] = {
	&dev_attr_mode.attr,
	NULL,
};
ATTRIBUTE_GROUPS(tyfza1_led);

static int tyfza1_gpio_output(struct tyfza1_led *led, unsigned int value)
{
	struct tyfza1_gpio_led *gpio = container_of(led, struct tyfza1_gpio_led, led);

	return gpiod_set_value_cansleep(gpio->gpio, value);
}

static void tyfza1_gpio_unregister(void *data)
{
	tyfza1_led_unregister(data);
}

static int tyfza1_led_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	int ret;

	device_for_each_child_node_scoped(dev, child) {
		struct tyfza1_gpio_led *gpio;
		struct tyfza1_led *led;
		struct led_init_data init = { .fwnode = child };
		const char *state;

		gpio = devm_kzalloc(dev, sizeof(*gpio), GFP_KERNEL);
		if (!gpio)
			return -ENOMEM;
		led = &gpio->led;
		led->cdev.max_brightness = 1;
		led->output = tyfza1_gpio_output;
		gpio->gpio = devm_fwnode_gpiod_get(dev, child, NULL, GPIOD_ASIS, NULL);
		if (IS_ERR(gpio->gpio))
			return dev_err_probe(dev, PTR_ERR(gpio->gpio), "LED GPIO\n");
		if (!fwnode_property_read_string(child, "default-state", &state))
			led->requested = !strcmp(state, "on");
		ret = gpiod_direction_output(gpio->gpio, led->requested);
		if (ret)
			return ret;
		fwnode_property_read_string(child, "linux,default-trigger", &led->cdev.default_trigger);
		ret = tyfza1_led_register(dev, led, &init);
		if (ret)
			return ret;
		ret = devm_add_action_or_reset(dev, tyfza1_gpio_unregister, led);
		if (ret)
			return ret;
	}
	return 0;
}

static const struct of_device_id tyfza1_led_match[] = {
	{ .compatible = "tianyi,tyfza1-leds" },
	{ }
};
MODULE_DEVICE_TABLE(of, tyfza1_led_match);

static struct platform_driver tyfza1_led_driver = {
	.probe = tyfza1_led_probe,
	.driver = {
		.name = "tyfza1-leds",
		.of_match_table = tyfza1_led_match,
		.dev_groups = tyfza1_led_groups,
	},
};
module_platform_driver(tyfza1_led_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 native GPIO LED controller");
