// SPDX-License-Identifier: GPL-2.0-or-later
/* Taurus BIW I2C, native Linux adapter; Cortina/OpenCores register model. */
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>

#define BIW_CFG 0x00
#define BIW_CTRL 0x04
#define BIW_TX 0x08
#define BIW_RX 0x0c
#define BIW_ACK 0x10
#define BIW_IE0 0x14
#define BIW_IE1 0x1c
#define BIW_DONE BIT(0)
#define BIW_NACK BIT(3)
#define BIW_WRITE BIT(4)
#define BIW_READ BIT(5)
#define BIW_STOP BIT(6)
#define BIW_START BIT(7)

struct rtl9617c_i2c {
	void __iomem *base;
	struct i2c_adapter adapter;
};

static int rtl_i2c_command(struct rtl9617c_i2c *i2c, u32 command)
{
	u32 value;
	int ret;

	writel(command, i2c->base + BIW_CTRL);
	ret = readl_poll_timeout(i2c->base + BIW_CTRL, value, value & BIW_DONE, 10, 10000);
	if (ret)
		return ret;
	writel(value, i2c->base + BIW_CTRL);
	value = readl(i2c->base + BIW_ACK);
	if (value & BIT(0))
		return -EAGAIN;
	if (!(command & BIW_READ) && value & BIT(2))
		return -ENXIO;
	return 0;
}

static int rtl_i2c_transfer(struct i2c_adapter *adapter, struct i2c_msg *messages, int count)
{
	struct rtl9617c_i2c *i2c = i2c_get_adapdata(adapter);
	u32 value;
	int message, byte, ret;

	ret = readl_poll_timeout(i2c->base + BIW_ACK, value, !(value & BIT(1)), 10, 100000);
	if (ret)
		return ret;
	for (message = 0; message < count; message++) {
		struct i2c_msg *msg = &messages[message];
		bool read = msg->flags & I2C_M_RD;

		if (msg->flags & ~(I2C_M_RD | I2C_M_DMA_SAFE)) {
			ret = -EOPNOTSUPP;
			goto stop;
		}
		writel(msg->addr << 1 | read, i2c->base + BIW_TX);
		ret = rtl_i2c_command(i2c, BIW_START | BIW_WRITE |
			(count == message + 1 && !msg->len ? BIW_STOP : 0));
		if (ret)
			goto stop;
		for (byte = 0; byte < msg->len; byte++) {
			bool last = byte == msg->len - 1;
			u32 command = read ? BIW_READ : BIW_WRITE;

			if (!read)
				writel(msg->buf[byte], i2c->base + BIW_TX);
			if (read && last)
				command |= BIW_NACK;
			if (last && message == count - 1)
				command |= BIW_STOP;
			ret = rtl_i2c_command(i2c, command);
			if (ret)
				goto stop;
			if (read)
				msg->buf[byte] = readl(i2c->base + BIW_RX);
		}
	}
	return count;
stop:
	rtl_i2c_command(i2c, BIW_STOP);
	return ret;
}

static u32 rtl_i2c_functionality(struct i2c_adapter *adapter)
{
	return I2C_FUNC_I2C | I2C_FUNC_SMBUS_BYTE | I2C_FUNC_SMBUS_BYTE_DATA |
	       I2C_FUNC_SMBUS_WORD_DATA | I2C_FUNC_SMBUS_I2C_BLOCK;
}

static const struct i2c_algorithm rtl_i2c_algorithm = {
	.xfer = rtl_i2c_transfer,
	.functionality = rtl_i2c_functionality,
};

static int rtl_i2c_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rtl9617c_i2c *i2c;
	struct clk *clock;
	u32 frequency = 100000, prescale;
	unsigned long rate;

	i2c = devm_kzalloc(dev, sizeof(*i2c), GFP_KERNEL);
	if (!i2c)
		return -ENOMEM;
	i2c->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(i2c->base))
		return PTR_ERR(i2c->base);
	clock = devm_clk_get_enabled(dev, NULL);
	if (IS_ERR(clock))
		return PTR_ERR(clock);
	rate = clk_get_rate(clock);
	device_property_read_u32(dev, "clock-frequency", &frequency);
	if (!frequency || rate < 5UL * frequency || rate / (5UL * frequency) > 65536)
		return -EINVAL;
	prescale = rate / (5UL * frequency) - 1;
	writel(BIT(1), i2c->base + BIW_CFG);
	usleep_range(1000, 2000);
	writel(prescale << 16 | BIT(0), i2c->base + BIW_CFG);
	writel(0, i2c->base + BIW_IE0);
	writel(0, i2c->base + BIW_IE1);
	i2c->adapter.owner = THIS_MODULE;
	i2c->adapter.algo = &rtl_i2c_algorithm;
	i2c->adapter.dev.parent = dev;
	i2c->adapter.dev.of_node = dev->of_node;
	strscpy(i2c->adapter.name, "RTL9617C BIW I2C", sizeof(i2c->adapter.name));
	i2c_set_adapdata(&i2c->adapter, i2c);
	platform_set_drvdata(pdev, i2c);
	dev_info(dev, "native BIW adapter, %u Hz, APB %lu Hz, prescale %u\n", frequency, rate, prescale);
	return devm_i2c_add_adapter(dev, &i2c->adapter);
}

static const struct of_device_id rtl_i2c_match[] = {
	{ .compatible = "realtek,rtl9617c-i2c" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_i2c_match);

static struct platform_driver rtl_i2c_driver = {
	.probe = rtl_i2c_probe,
	.driver = {
		.name = "rtl9617c-i2c",
		.of_match_table = rtl_i2c_match,
	},
};
module_platform_driver(rtl_i2c_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("RTL9617C native BIW I2C controller");
