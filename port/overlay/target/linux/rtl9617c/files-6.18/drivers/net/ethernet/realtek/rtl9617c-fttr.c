// SPDX-License-Identifier: GPL-2.0-only
/* Native TYFZA1 MiniOLT control transport, derived from stock machine code. */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/mdio.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/phy.h>
#include <linux/regmap.h>

#include "rtl9617c-fttr-mdio.h"
#include "rtl9617c-fttr-i2c.h"

struct rtl_fttr {
	struct mdio_device *mdio;
	struct regmap *map;
	struct rtl_fttr_mdio_io io;
	struct rtl_fttr_reg_io optical_io;
	struct i2c_adapter optical;
	enum rtl_fttr_backend backend;
};

static int rtl_fttr_mdio_read(void *context, unsigned int reg)
{
	return __mdiodev_read(context, reg);
}

static int rtl_fttr_mdio_write(void *context, unsigned int reg, unsigned short value)
{
	return __mdiodev_write(context, reg, value);
}

static void rtl_fttr_delay(void *context, unsigned int usec)
{
	usleep_range(usec, usec + 50);
}

static int rtl_fttr_map_read(void *context, unsigned int reg, unsigned int *value)
{
	struct rtl_fttr *fttr = context;
	unsigned int data = 0;
	int ret;

	mutex_lock(&fttr->mdio->bus->mdio_lock);
	ret = rtl_fttr_reg_xfer(&fttr->io, fttr->backend, reg, &data, 0);
	mutex_unlock(&fttr->mdio->bus->mdio_lock);
	if (!ret)
		*value = data;
	return ret;
}

static int rtl_fttr_map_write(void *context, unsigned int reg, unsigned int value)
{
	struct rtl_fttr *fttr = context;
	int ret;

	mutex_lock(&fttr->mdio->bus->mdio_lock);
	ret = rtl_fttr_reg_xfer(&fttr->io, fttr->backend, reg, &value, 1);
	mutex_unlock(&fttr->mdio->bus->mdio_lock);
	return ret;
}

static const struct regmap_bus rtl_fttr_regmap_bus = {
	.reg_read = rtl_fttr_map_read,
	.reg_write = rtl_fttr_map_write,
};

static const struct regmap_config rtl_fttr_regmap_config = {
	.reg_bits = 32,
	.val_bits = 32,
	.cache_type = REGCACHE_NONE,
};

static int rtl_fttr_optical_read(void *context, unsigned int reg, unsigned int *value)
{
	return regmap_read(context, reg, value);
}

static int rtl_fttr_optical_write(void *context, unsigned int reg, unsigned int value)
{
	return regmap_write(context, reg, value);
}

static s32 rtl_fttr_smbus_xfer(struct i2c_adapter *adapter, u16 addr,
			     unsigned short flags, char read_write, u8 command,
			     int size, union i2c_smbus_data *data)
{
	struct rtl_fttr *fttr = i2c_get_adapdata(adapter);
	bool read = read_write == I2C_SMBUS_READ;
	unsigned int value;
	int ret;

	if (flags & I2C_CLIENT_TEN)
		return -EOPNOTSUPP;
	if (size != I2C_SMBUS_BYTE_DATA)
		return -EOPNOTSUPP;
	value = read ? 0 : data->byte;
	ret = rtl_fttr_i2c_xfer(&fttr->optical_io, addr, command, &value, 0, read);
	if (!ret && read)
		data->byte = value;
	return ret;
}

static u32 rtl_fttr_i2c_functionality(struct i2c_adapter *adapter)
{
	return I2C_FUNC_SMBUS_BYTE_DATA;
}

static const struct i2c_algorithm rtl_fttr_i2c_algorithm = {
	.smbus_xfer = rtl_fttr_smbus_xfer,
	.functionality = rtl_fttr_i2c_functionality,
};

static ssize_t backend_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	static const char * const names[] = { "", "fpga1", "fpga2", "asic9690" };

	return sysfs_emit(buf, "%s\n", names[fttr->backend]);
}
static DEVICE_ATTR_RO(backend);

static ssize_t version_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int version;
	int ret = regmap_read(fttr->map, 0, &version);

	return ret ? ret : sysfs_emit(buf, "%08x\n", version);
}
static DEVICE_ATTR_RO(version);

static struct attribute *rtl_fttr_attrs[] = {
	&dev_attr_backend.attr,
	&dev_attr_version.attr,
	NULL,
};
ATTRIBUTE_GROUPS(rtl_fttr);

static int rtl_fttr_probe(struct mdio_device *mdio)
{
	struct device *dev = &mdio->dev;
	struct rtl_fttr *fttr;
	int ret;

	fttr = devm_kzalloc(dev, sizeof(*fttr), GFP_KERNEL);
	if (!fttr)
		return -ENOMEM;
	fttr->mdio = mdio;
	fttr->io = (struct rtl_fttr_mdio_io) {
		.context = mdio,
		.read = rtl_fttr_mdio_read,
		.write = rtl_fttr_mdio_write,
		.delay = rtl_fttr_delay,
	};
	mutex_lock(&mdio->bus->mdio_lock);
	ret = rtl_fttr_identify(&fttr->io, &fttr->backend);
	mutex_unlock(&mdio->bus->mdio_lock);
	if (ret)
		return dev_err_probe(dev, ret, "MiniOLT identification failed\n");
	fttr->map = devm_regmap_init(dev, &rtl_fttr_regmap_bus, fttr,
				    &rtl_fttr_regmap_config);
	if (IS_ERR(fttr->map))
		return PTR_ERR(fttr->map);
	dev_set_drvdata(dev, fttr);
	fttr->optical_io = (struct rtl_fttr_reg_io) {
		.context = fttr->map,
		.read = rtl_fttr_optical_read,
		.write = rtl_fttr_optical_write,
		.delay = rtl_fttr_delay,
	};
	fttr->optical.owner = THIS_MODULE;
	fttr->optical.algo = &rtl_fttr_i2c_algorithm;
	fttr->optical.dev.parent = dev;
	fttr->optical.dev.of_node = dev->of_node;
	strscpy(fttr->optical.name, "TYFZA1 FTTR optical I2C", sizeof(fttr->optical.name));
	i2c_set_adapdata(&fttr->optical, fttr);
	return devm_i2c_add_adapter(dev, &fttr->optical);
}

static const struct of_device_id rtl_fttr_match[] = {
	{ .compatible = "realtek,tyfza1-miniolt" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_fttr_match);

static struct mdio_driver rtl_fttr_driver = {
	.probe = rtl_fttr_probe,
	.mdiodrv.driver = {
		.name = "tyfza1-miniolt",
		.of_match_table = rtl_fttr_match,
		.dev_groups = rtl_fttr_groups,
	},
};
mdio_module_driver(rtl_fttr_driver);
MODULE_DESCRIPTION("TYFZA1 native FTTR MiniOLT register transport");
MODULE_LICENSE("GPL");
