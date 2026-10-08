// SPDX-License-Identifier: GPL-2.0-or-later
/* Temporary RAM-only platform/lookup bindings for this already running board. */
#include <linux/gpio/machine.h>
#include <linux/module.h>
#include <linux/platform_device.h>

static struct platform_device *gpio_device;
static struct gpiod_lookup_table lookup = {
	.dev_id = "4f5500000.pon",
	.table = { GPIO_LOOKUP("rtl9617c-gpio", 15, "tx-disable", GPIO_ACTIVE_HIGH), { } },
};

static int __init bindings_init(void)
{
	struct resource memory = DEFINE_RES_MEM(0x4f43292c0ULL, 0xb4);

	gpiod_add_lookup_table(&lookup);
	gpio_device = platform_device_register_simple("rtl9617c-gpio", -1, &memory, 1);
	if (IS_ERR(gpio_device)) {
		gpiod_remove_lookup_table(&lookup);
		return PTR_ERR(gpio_device);
	}
	return 0;
}

static void __exit bindings_exit(void)
{
	platform_device_unregister(gpio_device);
	gpiod_remove_lookup_table(&lookup);
}
module_init(bindings_init);
module_exit(bindings_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 RAM bring-up GPIO bindings");
