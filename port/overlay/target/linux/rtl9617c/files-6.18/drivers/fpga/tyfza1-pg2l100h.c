// SPDX-License-Identifier: GPL-2.0-only
/* PG2L100H serial loading: TYFZA1 sloader.ko and fpga_dev_spi_multi_write. */
#include <linux/delay.h>
#include <linux/fpga/fpga-mgr.h>
#include <linux/gpio/consumer.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/spi/spi.h>
#include <linux/string.h>

struct tyfza1_fpga {
	struct spi_device *spi;
	struct gpio_desc *progb;
	struct gpio_desc *initb;
	struct gpio_desc *done;
};

static int tyfza1_fpga_wait(struct gpio_desc *gpio, int level, unsigned int timeout)
{
	int value, ret;

	ret = read_poll_timeout(gpiod_get_raw_value_cansleep, value,
				value < 0 || value == level, 10000, timeout, false, gpio);
	return value < 0 ? value : ret;
}

static enum fpga_mgr_states tyfza1_fpga_state(struct fpga_manager *mgr)
{
	struct tyfza1_fpga *fpga = mgr->priv;
	int done = gpiod_get_raw_value_cansleep(fpga->done);

	if (done < 0)
		return FPGA_MGR_STATE_UNKNOWN;
	return done ? FPGA_MGR_STATE_OPERATING : FPGA_MGR_STATE_RESET;
}

static int tyfza1_fpga_write_init(struct fpga_manager *mgr,
				struct fpga_image_info *info, const char *buf, size_t count)
{
	struct tyfza1_fpga *fpga = mgr->priv;
	int ret;

	if (info->flags & FPGA_MGR_PARTIAL_RECONFIG)
		return -EOPNOTSUPP;
	ret = gpiod_set_value_cansleep(fpga->progb, 1);
	if (ret)
		return ret;
	ret = tyfza1_fpga_wait(fpga->initb, 0, 2000000);
	if (ret)
		return ret;
	ret = gpiod_set_value_cansleep(fpga->progb, 0);
	if (ret)
		return ret;
	ret = tyfza1_fpga_wait(fpga->initb, 1, 10000000);
	if (ret)
		return ret;
	ret = gpiod_get_raw_value_cansleep(fpga->done);
	return ret < 0 ? ret : ret ? -EIO : 0;
}

static int tyfza1_fpga_write(struct fpga_manager *mgr, const char *buf, size_t count)
{
	struct tyfza1_fpga *fpga = mgr->priv;

	return spi_write(fpga->spi, buf, count);
}

static int tyfza1_fpga_write_complete(struct fpga_manager *mgr,
				    struct fpga_image_info *info)
{
	struct tyfza1_fpga *fpga = mgr->priv;

	return tyfza1_fpga_wait(fpga->done, 1, 10000000);
}

static const struct fpga_manager_ops tyfza1_fpga_ops = {
	.state = tyfza1_fpga_state,
	.write_init = tyfza1_fpga_write_init,
	.write = tyfza1_fpga_write,
	.write_complete = tyfza1_fpga_write_complete,
};

static ssize_t firmware_store(struct device *dev, struct device_attribute *attr,
			     const char *buf, size_t count)
{
	struct fpga_manager *mgr = spi_get_drvdata(to_spi_device(dev));
	struct fpga_image_info *info;
	char *name;
	int ret;

	name = devm_kmalloc(dev, count + 1, GFP_KERNEL);
	if (!name)
		return -ENOMEM;
	memcpy(name, buf, count);
	name[count] = '\0';
	strim(name);
	info = fpga_image_info_alloc(dev);
	if (!info) {
		devm_kfree(dev, name);
		return -ENOMEM;
	}
	info->firmware_name = name;
	ret = fpga_mgr_lock(mgr);
	if (!ret) {
		ret = fpga_mgr_load(mgr, info);
		fpga_mgr_unlock(mgr);
	}
	fpga_image_info_free(info);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(firmware);

static struct attribute *tyfza1_fpga_attrs[] = {
	&dev_attr_firmware.attr,
	NULL,
};
ATTRIBUTE_GROUPS(tyfza1_fpga);

static int tyfza1_fpga_probe(struct spi_device *spi)
{
	struct device *dev = &spi->dev;
	struct fpga_manager *mgr;
	struct tyfza1_fpga *fpga;
	struct gpio_desc *gpio;
	int ret;

	fpga = devm_kzalloc(dev, sizeof(*fpga), GFP_KERNEL);
	if (!fpga)
		return -ENOMEM;
	fpga->spi = spi;
	/* The original loader overrides these unused SPI inputs to GPIO. */
	gpio = devm_gpiod_get(dev, "cs-input", GPIOD_IN);
	if (IS_ERR(gpio))
		return dev_err_probe(dev, PTR_ERR(gpio), "CS input unavailable\n");
	gpio = devm_gpiod_get(dev, "rxd-input", GPIOD_IN);
	if (IS_ERR(gpio))
		return dev_err_probe(dev, PTR_ERR(gpio), "RX input unavailable\n");
	gpio = devm_gpiod_get(dev, "reserved", GPIOD_OUT_HIGH);
	if (IS_ERR(gpio))
		return dev_err_probe(dev, PTR_ERR(gpio), "FPGA reserved output unavailable\n");
	fpga->initb = devm_gpiod_get(dev, "initb", GPIOD_IN);
	if (IS_ERR(fpga->initb))
		return dev_err_probe(dev, PTR_ERR(fpga->initb), "INITB unavailable\n");
	fpga->done = devm_gpiod_get(dev, "done", GPIOD_IN);
	if (IS_ERR(fpga->done))
		return dev_err_probe(dev, PTR_ERR(fpga->done), "DONE unavailable\n");
	fpga->progb = devm_gpiod_get(dev, "progb", GPIOD_OUT_LOW);
	if (IS_ERR(fpga->progb))
		return dev_err_probe(dev, PTR_ERR(fpga->progb), "PROGB unavailable\n");
	spi->bits_per_word = 8;
	ret = spi_setup(spi);
	if (ret)
		return ret;
	mgr = devm_fpga_mgr_register(dev, "TYFZA1 PG2L100H", &tyfza1_fpga_ops, fpga);
	if (IS_ERR(mgr))
		return PTR_ERR(mgr);
	spi_set_drvdata(spi, mgr);
	return 0;
}

static const struct of_device_id tyfza1_fpga_match[] = {
	{ .compatible = "pango,tyfza1-pg2l100h" },
	{ }
};
MODULE_DEVICE_TABLE(of, tyfza1_fpga_match);

static struct spi_driver tyfza1_fpga_driver = {
	.probe = tyfza1_fpga_probe,
	.driver = {
		.name = "tyfza1-pg2l100h",
		.of_match_table = tyfza1_fpga_match,
		.dev_groups = tyfza1_fpga_groups,
	},
};
module_spi_driver(tyfza1_fpga_driver);
MODULE_DESCRIPTION("TYFZA1 PG2L100H native serial FPGA manager");
MODULE_LICENSE("GPL");
