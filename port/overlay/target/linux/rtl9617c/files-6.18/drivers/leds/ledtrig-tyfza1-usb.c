// SPDX-License-Identifier: GPL-2.0-only
/* OEM usbdev_trig_timer: root ports 1-1/2-1, 50 ms activity phases. */
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/usb.h>
#include <linux/workqueue.h>

struct tyfza1_usb_sample {
	unsigned int present;
	unsigned int urbs[2];
};

struct tyfza1_usb_led {
	struct led_classdev *led;
	struct delayed_work work;
	unsigned int urbs[2];
	bool on;
};

static int tyfza1_usb_sample(struct usb_device *usb, void *context)
{
	struct tyfza1_usb_sample *sample = context;
	const char *name = dev_name(&usb->dev);
	unsigned int port;

	if (!strcmp(name, "1-1"))
		port = 0;
	else if (!strcmp(name, "2-1"))
		port = 1;
	else
		return 0;
	sample->present |= BIT(port);
	sample->urbs[port] = atomic_read(&usb->urbnum);
	return 0;
}

static bool tyfza1_usb_output(struct tyfza1_usb_led *data,
			      const struct tyfza1_usb_sample *sample)
{
	bool on = true;
	unsigned int port;

	if (!sample->present)
		return false;
	/* An activity-off phase always ends on the following tick. */
	if (data->on) {
		for (port = 0; port < 2; port++) {
			if (!(sample->present & BIT(port)))
				continue;
			if (data->urbs[port] != sample->urbs[port])
				on = false;
			data->urbs[port] = sample->urbs[port];
		}
	}
	return on;
}

static void tyfza1_usb_poll(struct work_struct *work)
{
	struct tyfza1_usb_led *data = container_of(to_delayed_work(work),
						struct tyfza1_usb_led, work);
	struct tyfza1_usb_sample sample = {};
	bool on;

	/* USB core supplies referenced devices; no pointers survive the scan. */
	usb_for_each_dev(&sample, tyfza1_usb_sample);
	on = tyfza1_usb_output(data, &sample);
	if (on != data->on)
		led_set_brightness(data->led, on ? LED_FULL : LED_OFF);
	data->on = on;
	schedule_delayed_work(&data->work, msecs_to_jiffies(50));
}

static int tyfza1_usb_activate(struct led_classdev *led)
{
	struct tyfza1_usb_led *data = kzalloc(sizeof(*data), GFP_KERNEL);

	if (!data)
		return -ENOMEM;
	data->led = led;
	INIT_DELAYED_WORK(&data->work, tyfza1_usb_poll);
	led_set_trigger_data(led, data);
	led_set_brightness(led, LED_OFF);
	schedule_delayed_work(&data->work, 0);
	return 0;
}

static void tyfza1_usb_deactivate(struct led_classdev *led)
{
	struct tyfza1_usb_led *data = led_get_trigger_data(led);

	cancel_delayed_work_sync(&data->work);
	kfree(data);
}

static struct led_trigger tyfza1_usb_trigger = {
	.name = "tyfza1-usb",
	.activate = tyfza1_usb_activate,
	.deactivate = tyfza1_usb_deactivate,
};
module_led_trigger(tyfza1_usb_trigger);
MODULE_DESCRIPTION("TYFZA1 USB presence and activity LED");
MODULE_LICENSE("GPL");
