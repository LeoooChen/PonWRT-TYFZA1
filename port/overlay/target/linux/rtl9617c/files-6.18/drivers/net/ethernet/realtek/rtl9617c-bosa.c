// SPDX-License-Identifier: GPL-2.0-or-later
/* Native I2C/firmware BOSA calibration and optical diagnostic client. */
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/i2c.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/unaligned.h>
#include "rtl9617c-bosa-sequence.h"
#include "rtl9617c-bosa.h"

struct rtl_bosa {
	struct i2c_client *client;
	struct mutex lock;
	enum rtl_bosa_model model;
	bool configured;
	bool eeprom;
	unsigned int writes;
	int error;
	u8 identity[3];
};

static int rtl_bosa_write(void *context, unsigned int reg, unsigned char value)
{
	struct rtl_bosa *bosa = context;
	int ret = i2c_smbus_write_byte_data(bosa->client, reg, value);

	if (!ret)
		bosa->writes++;
	return ret;
}

static int rtl_bosa_read(void *context, unsigned int reg, unsigned char *value)
{
	struct rtl_bosa *bosa = context;
	int ret = i2c_smbus_read_byte_data(bosa->client, reg);

	if (ret < 0)
		return ret;
	*value = ret;
	return 0;
}

static int rtl_bosa_wait(void *context, unsigned int reg, unsigned char mask, unsigned char value)
{
	struct rtl_bosa *bosa = context;
	int status, ret;

	ret = read_poll_timeout(i2c_smbus_read_byte_data, status,
		status < 0 || (status & mask) == value, 1000, 1000000, false, bosa->client, reg);
	return status < 0 ? status : ret;
}

static int rtl_bosa_identify(struct rtl_bosa *bosa)
{
	u8 id, digital;
	int ret;
	unsigned int i;
	static const u8 registers[] = {0x80, 0x85, 0x86};

	ret = rtl_bosa_write(bosa, 0x7f, 0xff);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(registers); i++) {
		ret = rtl_bosa_read(bosa, registers[i], &bosa->identity[i]);
		if (ret)
			return ret;
	}
	if (!memcmp(bosa->identity, "G96", 3))
		bosa->model = RTL_BOSA_GN28L96;
	else if (!memcmp(bosa->identity, "G97", 3))
		bosa->model = RTL_BOSA_GN28L97;
	else if (!memcmp(bosa->identity, "\xa1\0\0", 3))
		bosa->model = RTL_BOSA_GN28L95;
	else {
		ret = rtl_bosa_write(bosa, 0x7f, 2);
		if (ret)
			return ret;
		ret = rtl_bosa_read(bosa, 0xd1, &id);
		if (ret)
			return ret;
		if ((id & 0xf0) == 0xa0)
			bosa->model = RTL_BOSA_GN25L95;
		else if ((id & 0xf0) == 0xc0)
			bosa->model = RTL_BOSA_GN25L98;
		else
			return -ENODEV;
	}
	ret = rtl_bosa_write(bosa, 0x7f, bosa->model >= RTL_BOSA_GN28L96 ? 0x81 : 2);
	if (ret)
		return ret;
	ret = rtl_bosa_read(bosa, bosa->model >= RTL_BOSA_GN28L96 ? 0x89 :
		bosa->model == RTL_BOSA_GN28L95 ? 0xc7 : 0xc0, &digital);
	if (ret)
		return ret;
	/* The shipping GN28L96/97 EEPROM-present flag has inverse polarity. */
	bosa->eeprom = bosa->model >= RTL_BOSA_GN28L96 ? !(digital & 1) : digital & 1;
	return 0;
}

static int rtl_bosa_configure(struct rtl_bosa *bosa)
{
	const struct firmware *calibration;
	struct rtl_bosa_io io = {
		.context = bosa,
		.write = rtl_bosa_write,
		.read = rtl_bosa_read,
		.wait = rtl_bosa_wait,
	};
	u8 page, tx;
	int ret, restore;

	mutex_lock(&bosa->lock);
	bosa->configured = false;
	bosa->writes = 0;
	ret = rtl_bosa_read(bosa, 0x7f, &page);
	if (ret)
		goto unlock;
	ret = rtl_bosa_identify(bosa);
	if (ret)
		goto restore;
	if (!bosa->eeprom) {
		ret = request_firmware_direct(&calibration, "realtek/rtl9617c/bosa-calibration.bin", &bosa->client->dev);
		if (ret)
			goto restore;
		/* The original sequence reads offsets through 0x3ff; preserve any file suffix. */
		if (calibration->size < 1024) {
			dev_err(&bosa->client->dev, "BOSA calibration has %zu bytes; register sequence needs 1024\n",
				calibration->size);
			ret = -EMSGSIZE;
		} else {
			ret = rtl_bosa_calibrate(&io, calibration->data, bosa->model);
		}
		release_firmware(calibration);
		if (ret)
			goto restore;
	}
	ret = rtl_bosa_read(bosa, 0x6e, &tx);
	if (!ret && tx & BIT(6))
		ret = -EIO;
	if (!ret) {
		bosa->configured = true;
		dev_info(&bosa->client->dev, "BOSA model=%u EEPROM=%u, %u writes, tx_control=%02x\n",
			 bosa->model, bosa->eeprom, bosa->writes, tx);
	}
restore:
	restore = rtl_bosa_write(bosa, 0x7f, page);
	if (!ret)
		ret = restore;
unlock:
	if (ret)
		bosa->configured = false;
	bosa->error = ret;
	mutex_unlock(&bosa->lock);
	return ret;
}

int rtl9617c_bosa_prepare(struct device *device)
{
	return rtl_bosa_configure(dev_get_drvdata(device));
}
EXPORT_SYMBOL_GPL(rtl9617c_bosa_prepare);

ssize_t rtl9617c_bosa_state(struct device *dev, char *buf)
{
	struct rtl_bosa *bosa = dev_get_drvdata(dev);

	return sysfs_emit(buf, "configured=%u model=%u eeprom=%u writes=%u error=%d identity=%02x/%02x/%02x\n",
		bosa->configured, bosa->model, bosa->eeprom, bosa->writes, bosa->error,
		bosa->identity[0], bosa->identity[1], bosa->identity[2]);
}

EXPORT_SYMBOL_GPL(rtl9617c_bosa_state);

static ssize_t configure_show(struct device *dev, struct device_attribute *attribute, char *buf)
{
	return rtl9617c_bosa_state(dev, buf);
}

static DEVICE_ATTR_RO(configure);

static ssize_t diagnostics_show(struct device *dev, struct device_attribute *attribute, char *buf)
{
	struct rtl_bosa *bosa = dev_get_drvdata(dev);
	u8 data[14];
	int ret;
	unsigned int i;

	mutex_lock(&bosa->lock);
	for (i = 0; i < ARRAY_SIZE(data); i++) {
		ret = rtl_bosa_read(bosa, 0x60 + i, &data[i]);
		if (ret)
			goto unlock;
	}
	ret = sysfs_emit(buf, "temperature_raw=%d voltage_raw=%u bias_raw=%u tx_power_raw=%u rx_power_raw=%u control=%02x/%02x/%02x/%02x\n",
		(s16)get_unaligned_be16(data), get_unaligned_be16(data + 2),
		get_unaligned_be16(data + 4), get_unaligned_be16(data + 6),
		get_unaligned_be16(data + 8), data[10], data[11], data[12], data[13]);
unlock:
	mutex_unlock(&bosa->lock);
	return ret;
}
static DEVICE_ATTR_RO(diagnostics);

static struct attribute *rtl_bosa_attrs[] = {
	&dev_attr_configure.attr,
	&dev_attr_diagnostics.attr,
	NULL,
};
ATTRIBUTE_GROUPS(rtl_bosa);

static int rtl_bosa_probe(struct i2c_client *client)
{
	struct rtl_bosa *bosa = devm_kzalloc(&client->dev, sizeof(*bosa), GFP_KERNEL);

	if (!bosa)
		return -ENOMEM;
	bosa->client = client;
	mutex_init(&bosa->lock);
	i2c_set_clientdata(client, bosa);
	return 0;
}

static const struct of_device_id rtl_bosa_match[] = {
	{ .compatible = "realtek,rtl9617c-bosa" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_bosa_match);

static struct i2c_driver rtl_bosa_driver = {
	.probe = rtl_bosa_probe,
	.driver = {
		.name = "rtl9617c-bosa",
		.of_match_table = rtl_bosa_match,
		.dev_groups = rtl_bosa_groups,
	},
};
module_i2c_driver(rtl_bosa_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("RTL9617C native Semtech BOSA calibration client");
