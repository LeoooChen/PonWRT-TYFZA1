// SPDX-License-Identifier: GPL-2.0-only
/* Native GN25L99 calibration on the TYFZA1 downstream optical bus. */
#include <linux/firmware.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>

#include "rtl9617c-fttr-bosa.h"

struct rtl_fttr_bosa {
	struct i2c_client *client;
	struct mutex lock;
	unsigned int writes;
	int error;
	bool configured;
};

static int rtl_fttr_bosa_write(void *context, unsigned int reg, unsigned char value)
{
	struct rtl_fttr_bosa *bosa = context;
	int ret = i2c_smbus_write_byte_data(bosa->client, reg, value);

	if (!ret)
		bosa->writes++;
	return ret;
}

static int rtl_fttr_bosa_configure(struct rtl_fttr_bosa *bosa)
{
	const struct firmware *calibration;
	int ret;

	mutex_lock(&bosa->lock);
	bosa->configured = false;
	bosa->writes = 0;
	ret = request_firmware_direct(&calibration, "realtek/rtl9617c/fttr-bosa-calibration.bin",
				      &bosa->client->dev);
	if (ret)
		goto out;
	ret = rtl_fttr_gn25l99_validate(calibration->data, calibration->size,
				      bosa->client->addr);
	if (!ret)
		ret = rtl_fttr_gn25l99_calibrate(bosa, rtl_fttr_bosa_write, calibration->data);
	release_firmware(calibration);
	if (ret)
		goto out;
	ret = i2c_smbus_read_byte_data(bosa->client, 0x60);
	if (ret >= 0)
		ret = ret == 0xb0 ? 0 : -EIO;
	bosa->configured = !ret;
out:
	bosa->error = ret;
	mutex_unlock(&bosa->lock);
	return ret;
}

static ssize_t calibration_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr_bosa *bosa = dev_get_drvdata(dev);
	ssize_t size;
	int marker;

	mutex_lock(&bosa->lock);
	marker = i2c_smbus_read_byte_data(bosa->client, 0x60);
	if (marker < 0) {
		bosa->configured = false;
		bosa->error = marker;
		size = marker;
	} else {
		bosa->configured &= marker == 0xb0;
		size = sysfs_emit(buf, "configured=%u model=GN25L99 marker=%02x writes=%u error=%d\n",
			bosa->configured, marker, bosa->writes, bosa->error);
	}
	mutex_unlock(&bosa->lock);
	return size;
}

static ssize_t calibration_store(struct device *dev, struct device_attribute *attr,
				 const char *buf, size_t size)
{
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	ret = rtl_fttr_bosa_configure(dev_get_drvdata(dev));
	return ret ? ret : size;
}
static DEVICE_ATTR_RW(calibration);

static struct attribute *rtl_fttr_bosa_attrs[] = {
	&dev_attr_calibration.attr,
	NULL,
};
ATTRIBUTE_GROUPS(rtl_fttr_bosa);

static int rtl_fttr_bosa_probe(struct i2c_client *client)
{
	struct rtl_fttr_bosa *bosa;

	if (!i2c_check_functionality(client->adapter, I2C_FUNC_SMBUS_BYTE_DATA))
		return -EOPNOTSUPP;
	bosa = devm_kzalloc(&client->dev, sizeof(*bosa), GFP_KERNEL);
	if (!bosa)
		return -ENOMEM;
	bosa->client = client;
	mutex_init(&bosa->lock);
	i2c_set_clientdata(client, bosa);
	return 0;
}

static const struct of_device_id rtl_fttr_bosa_match[] = {
	{ .compatible = "semtech,tyfza1-gn25l99" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl_fttr_bosa_match);

static struct i2c_driver rtl_fttr_bosa_driver = {
	.probe = rtl_fttr_bosa_probe,
	.driver = {
		.name = "tyfza1-fttr-bosa",
		.of_match_table = rtl_fttr_bosa_match,
		.dev_groups = rtl_fttr_bosa_groups,
	},
};
module_i2c_driver(rtl_fttr_bosa_driver);
MODULE_DESCRIPTION("TYFZA1 native downstream GN25L99 calibration");
MODULE_LICENSE("GPL");
