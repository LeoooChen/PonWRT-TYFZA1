// SPDX-License-Identifier: GPL-2.0-only
/* Native TYFZA1 MiniOLT control transport, derived from stock machine code. */
#include <linux/delay.h>
#include <linux/etherdevice.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/mdio.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/phy.h>
#include <linux/regmap.h>

#include "rtl9617c-fttr-mdio.h"
#include "rtl9617c-fttr-core.h"
#include "rtl9617c-fttr-i2c.h"
#include "rtl9617c-fttr-omci.h"
#include "rtl9617c-fttr-onu.h"
#include "rtl9617c-fttr-serdes.h"

struct rtl_fttr {
	struct mdio_device *mdio;
	struct regmap *map;
	struct rtl_fttr_mdio_io io;
	struct rtl_fttr_reg_io reg_io;
	struct i2c_adapter optical;
	struct gpio_desc *reset;
	struct mutex control_lock;
	enum rtl_fttr_backend backend;
	unsigned int host_mode, pon_mode;
	bool host_nway;
	int host_error, pon_error;
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

static int rtl_fttr_reg_read(void *context, unsigned int reg, unsigned int *value)
{
	return regmap_read(context, reg, value);
}

static int rtl_fttr_reg_write(void *context, unsigned int reg, unsigned int value)
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
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_i2c_xfer(&fttr->reg_io, addr, command, &value, 0, read);
	mutex_unlock(&fttr->control_lock);
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

static ssize_t omci_link_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	struct rtl_fttr_omci_link link;
	int ret;

	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_omci_link_read(&fttr->reg_io, &link);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : sysfs_emit(buf, "host=%pM olt=%pM ethertype=%04x downstream_length=%u\n",
		link.host, link.olt, link.ethertype, link.downstream_length);
}

static ssize_t omci_link_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	u8 host[ETH_ALEN];
	int ret;

	if (!mac_pton(buf, host) || !is_valid_ether_addr(host))
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_omci_init(&fttr->reg_io, host);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(omci_link);

static ssize_t core_init_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_core_init(&fttr->reg_io);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(core_init);

static ssize_t dba_store(struct device *dev, struct device_attribute *attr,
			 const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_dba_defaults(&fttr->reg_io);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(dba);

static ssize_t tcont_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int id, tcont, fixed, assured, maximum;
	int ret;

	if (sscanf(buf, "%u %u %u %u %u", &id, &tcont, &fixed, &assured, &maximum) != 5 ||
	    id >= RTL_FTTR_ONUS || tcont > 7 || fixed > 0xffff || assured > 0xffff || maximum > 0xffff)
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_tcont_program(&fttr->reg_io, id, tcont, fixed, assured, maximum);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(tcont);

static ssize_t onu_table_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	struct rtl_fttr_onu onu;
	unsigned int id;
	ssize_t size = 0;
	int ret;

	mutex_lock(&fttr->control_lock);
	for (id = 0; id < RTL_FTTR_ONUS; id++) {
		ret = rtl_fttr_onu_read(&fttr->reg_io, id, &onu);
		if (ret) {
			size = ret;
			break;
		}
		size += sysfs_emit_at(buf, size, "%u %08x %08x %08x %8phN\n",
			id, onu.downstream, onu.upstream, onu.eqd, onu.serial);
	}
	mutex_unlock(&fttr->control_lock);
	return size;
}
static DEVICE_ATTR_RO(onu_table);

static ssize_t onu_block_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int id, blocked;
	int ret;

	if (sscanf(buf, "%u %u", &id, &blocked) != 2 || id >= RTL_FTTR_ONUS || blocked > 1)
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_onu_block(&fttr->reg_io, id, blocked);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(onu_block);

static ssize_t ploam_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int repeat, words[3];
	int ret;

	if (sscanf(buf, "%u %x %x %x", &repeat, &words[0], &words[1], &words[2]) != 4 ||
	    !repeat || repeat > 3)
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_ploam_send(&fttr->reg_io, words, repeat);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(ploam);

static ssize_t tx_enable_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	bool enabled;
	int ret = kstrtobool(buf, &enabled);

	if (ret)
		return ret;
	mutex_lock(&fttr->control_lock);
	ret = regmap_write(fttr->map, 0x20, !enabled);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(tx_enable);

static ssize_t reset_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	fttr->host_mode = 0;
	fttr->pon_mode = 0;
	ret = regmap_write(fttr->map, 0x20, 1);
	if (ret)
		goto out;
	ret = gpiod_direction_output(fttr->reset, 1);
	if (ret)
		goto out;
	usleep_range(1000, 1050);
	gpiod_set_value_cansleep(fttr->reset, 0);
	msleep(500);
	ret = regmap_write(fttr->map, 0x20, 1);
	if (!ret)
		ret = rtl_fttr_asic_mux(&fttr->reg_io);
out:
	fttr->host_error = ret;
	fttr->pon_error = ret;
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(reset);

static ssize_t host_serdes_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int status, link = 0;
	int ret;

	mutex_lock(&fttr->control_lock);
	ret = regmap_read(fttr->map, 0xc08008, &status);
	if (!ret && fttr->host_mode)
		ret = rtl_fttr_wtg_link(&fttr->reg_io, fttr->host_mode, &link);
	if (!ret)
		ret = sysfs_emit(buf, "mode=%u nway=%u pll=%u error=%d link=%u\n",
			fttr->host_mode, fttr->host_nway, !!(status & BIT(6)), fttr->host_error, link);
	mutex_unlock(&fttr->control_lock);
	return ret;
}

static ssize_t host_serdes_store(struct device *dev, struct device_attribute *attr,
				 const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int mode, nway;
	int ret;

	if (sscanf(buf, "%u %u", &mode, &nway) != 2 ||
	    (mode != 5 && mode != 9) || nway > 1)
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	fttr->host_mode = 0;
	fttr->pon_mode = 0;
	ret = rtl_fttr_wtg_configure(&fttr->reg_io, mode, nway);
	fttr->host_error = ret;
	if (!ret) {
		fttr->host_mode = mode;
		fttr->host_nway = nway;
	}
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(host_serdes);

static ssize_t pon_serdes_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int status;
	int ret;

	mutex_lock(&fttr->control_lock);
	ret = regmap_read(fttr->map, 0xc0c5c0, &status);
	if (!ret)
		ret = sysfs_emit(buf, "mode=%u locks=%x error=%d\n",
			fttr->pon_mode, (status >> 22) & 0xf, fttr->pon_error);
	mutex_unlock(&fttr->control_lock);
	return ret;
}

static ssize_t pon_serdes_store(struct device *dev, struct device_attribute *attr,
				const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int mode;
	int ret = kstrtouint(buf, 0, &mode);

	if (ret)
		return ret;
	if (mode < 1 || mode > 3)
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	fttr->pon_mode = 0;
	ret = rtl_fttr_pon_configure(&fttr->reg_io, mode);
	fttr->pon_error = ret;
	if (!ret)
		fttr->pon_mode = mode;
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(pon_serdes);

static struct attribute *rtl_fttr_attrs[] = {
	&dev_attr_backend.attr,
	&dev_attr_version.attr,
	&dev_attr_reset.attr,
	&dev_attr_host_serdes.attr,
	&dev_attr_pon_serdes.attr,
	&dev_attr_omci_link.attr,
	&dev_attr_onu_table.attr,
	&dev_attr_onu_block.attr,
	&dev_attr_ploam.attr,
	&dev_attr_tx_enable.attr,
	&dev_attr_core_init.attr,
	&dev_attr_dba.attr,
	&dev_attr_tcont.attr,
	NULL,
};

static umode_t rtl_fttr_attr_visible(struct kobject *kobj, struct attribute *attr, int n)
{
	struct rtl_fttr *fttr = dev_get_drvdata(kobj_to_dev(kobj));

	if (n >= 2 && fttr->backend != RTL_FTTR_ASIC9690)
		return 0;
	return attr->mode;
}

static const struct attribute_group rtl_fttr_group = {
	.attrs = rtl_fttr_attrs,
	.is_visible = rtl_fttr_attr_visible,
};
__ATTRIBUTE_GROUPS(rtl_fttr);

static int rtl_fttr_probe(struct mdio_device *mdio)
{
	struct device *dev = &mdio->dev;
	struct rtl_fttr *fttr;
	int ret;

	fttr = devm_kzalloc(dev, sizeof(*fttr), GFP_KERNEL);
	if (!fttr)
		return -ENOMEM;
	fttr->mdio = mdio;
	mutex_init(&fttr->control_lock);
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
	if (fttr->backend == RTL_FTTR_ASIC9690) {
		fttr->reset = devm_gpiod_get(dev, "reset", GPIOD_ASIS);
		if (IS_ERR(fttr->reset))
			return dev_err_probe(dev, PTR_ERR(fttr->reset), "MiniOLT reset GPIO\n");
		ret = regmap_write(fttr->map, 0x20, 1);
		if (ret)
			return dev_err_probe(dev, ret, "MiniOLT initial TX disable failed\n");
	}
	dev_set_drvdata(dev, fttr);
	fttr->reg_io = (struct rtl_fttr_reg_io) {
		.context = fttr->map,
		.read = rtl_fttr_reg_read,
		.write = rtl_fttr_reg_write,
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

static void rtl_fttr_remove(struct mdio_device *mdio)
{
	struct rtl_fttr *fttr = dev_get_drvdata(&mdio->dev);
	int ret;

	if (fttr->backend != RTL_FTTR_ASIC9690)
		return;
	mutex_lock(&fttr->control_lock);
	ret = regmap_write(fttr->map, 0x20, 1);
	mutex_unlock(&fttr->control_lock);
	if (ret)
		dev_err(&mdio->dev, "MiniOLT TX disable on removal failed: %d\n", ret);
}

static const struct of_device_id rtl_fttr_match[] = {
	{ .compatible = "realtek,tyfza1-miniolt" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_fttr_match);

static struct mdio_driver rtl_fttr_driver = {
	.probe = rtl_fttr_probe,
	.remove = rtl_fttr_remove,
	.mdiodrv.driver = {
		.name = "tyfza1-miniolt",
		.of_match_table = rtl_fttr_match,
		.dev_groups = rtl_fttr_groups,
	},
};
mdio_module_driver(rtl_fttr_driver);
MODULE_DESCRIPTION("TYFZA1 native FTTR MiniOLT register transport");
MODULE_LICENSE("GPL");
