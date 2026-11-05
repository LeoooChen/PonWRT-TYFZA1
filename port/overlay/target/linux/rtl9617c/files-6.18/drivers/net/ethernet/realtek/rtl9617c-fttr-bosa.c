// SPDX-License-Identifier: GPL-2.0-only
/* Original rtkoltbosa calibration on the TYFZA1 downstream optical bus. */
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
	unsigned int model;
	unsigned char ux_calibration[588];
	int error;
	bool configured;
};

static int rtl_fttr_bosa_read(void *context, unsigned int reg)
{
	struct rtl_fttr_bosa *bosa = context;

	return i2c_smbus_read_byte_data(bosa->client, reg);
}

static int rtl_fttr_bosa_write(void *context, unsigned int reg, unsigned char value)
{
	struct rtl_fttr_bosa *bosa = context;
	int ret = i2c_smbus_write_byte_data(bosa->client, reg, value);

	if (!ret)
		bosa->writes++;
	return ret;
}

static int rtl_fttr_bosa_read_word(void *context, unsigned int reg)
{
	struct rtl_fttr_bosa *bosa = context;

	return i2c_smbus_read_word_swapped(bosa->client, reg);
}

static int rtl_fttr_bosa_maintain_write(void *context, unsigned int reg, unsigned char value)
{
	struct rtl_fttr_bosa *bosa = context;

	/* Runtime temperature adjustment does not alter the calibration count. */
	return i2c_smbus_write_byte_data(bosa->client, reg, value);
}

static int rtl_fttr_bosa_configure(struct rtl_fttr_bosa *bosa)
{
	const struct firmware *calibration;
	unsigned int marker;
	int ret;

	mutex_lock(&bosa->lock);
	bosa->configured = false;
	bosa->writes = 0;
	bosa->model = 0;
	ret = request_firmware_direct(&calibration, "realtek/rtl9617c/fttr-bosa-calibration.bin",
				      &bosa->client->dev);
	if (ret)
		goto out;
	ret = rtl_fttr_optical_validate(calibration->data, calibration->size);
	if (!ret && (calibration->data[4] != bosa->client->addr << 1 ||
	    (of_device_is_compatible(bosa->client->dev.of_node, "semtech,tyfza1-gn25l99") &&
	     calibration->data[3] != RTL_FTTR_GN25L99)))
		ret = -EINVAL;
	if (!ret) {
		bosa->model = calibration->data[3];
		if (bosa->model == RTL_FTTR_UX3326S) {
			ret = rtl_fttr_ux3326s_unlock(bosa, rtl_fttr_bosa_write);
			if (!ret)
				ret = rtl_fttr_bosa_write(bosa, 0x7f, 0x80);
		}
	}
	if (!ret)
		ret = rtl_fttr_optical_identity(bosa, rtl_fttr_bosa_read, bosa->model, &marker);
	/* Count the calibration sequence separately from identification writes. */
	bosa->writes = 0;
	if (!ret)
		ret = rtl_fttr_optical_calibrate(bosa, rtl_fttr_bosa_write, calibration->data);
	if (!ret && bosa->model == RTL_FTTR_UX3326)
		memcpy(bosa->ux_calibration, calibration->data, sizeof(bosa->ux_calibration));
	release_firmware(calibration);
	if (ret)
		goto out;
	ret = rtl_fttr_optical_identity(bosa, rtl_fttr_bosa_read, bosa->model, &marker);
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
	unsigned int marker = 0;
	int ret;

	mutex_lock(&bosa->lock);
	ret = bosa->model ? rtl_fttr_optical_identity(bosa, rtl_fttr_bosa_read,
						    bosa->model, &marker) : 0;
	if (ret) {
		bosa->configured = false;
		bosa->error = ret;
		size = ret;
	} else {
		size = sysfs_emit(buf, "configured=%u model=%s marker=%02x writes=%u error=%d\n",
			bosa->configured, rtl_fttr_optical_name(bosa->model), marker, bosa->writes, bosa->error);
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

static ssize_t maintenance_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr_bosa *bosa = dev_get_drvdata(dev);
	ssize_t size;

	mutex_lock(&bosa->lock);
	size = bosa->configured ? sysfs_emit(buf, "supported=%u interval_ms=%u\n",
		bosa->model == RTL_FTTR_UX3326, bosa->model == RTL_FTTR_UX3326 ?
		(bosa->ux_calibration[6] ? 200 : 1000) : 0) : -EIO;
	mutex_unlock(&bosa->lock);
	return size;
}

static ssize_t maintenance_store(struct device *dev, struct device_attribute *attr,
		const char *buf, size_t size)
{
	struct rtl_fttr_bosa *bosa = dev_get_drvdata(dev);
	const struct firmware *calibration;
	int ret;

	mutex_lock(&bosa->lock);
	if (!bosa->configured) {
		ret = -EIO;
		goto out;
	}
	if (bosa->model != RTL_FTTR_UX3326) {
		ret = -EOPNOTSUPP;
		goto out;
	}
	if (sysfs_streq(buf, "temperature")) {
		ret = rtl_fttr_ux3326_temperature(bosa, rtl_fttr_bosa_read_word,
			rtl_fttr_bosa_maintain_write, bosa->ux_calibration);
	} else if (sysfs_streq(buf, "reset") || sysfs_streq(buf, "power")) {
		/* Original periodically reloads the calibration used by its monitor.
		 * A changed model/address cannot be applied to this bound client.
		 */
		ret = request_firmware_direct(&calibration, "realtek/rtl9617c/fttr-bosa-calibration.bin", dev);
		if (ret)
			goto out;
		ret = rtl_fttr_optical_validate(calibration->data, calibration->size);
		if (!ret && (calibration->data[3] != RTL_FTTR_UX3326 ||
		    calibration->data[4] != bosa->client->addr << 1))
			ret = -EINVAL;
		if (!ret) {
			memcpy(bosa->ux_calibration, calibration->data, sizeof(bosa->ux_calibration));
			ret = sysfs_streq(buf, "reset") ?
				rtl_fttr_ux3326_check_reset(bosa, rtl_fttr_bosa_read,
					rtl_fttr_bosa_read_word, bosa->ux_calibration) :
				rtl_fttr_ux3326_low_power(bosa, rtl_fttr_bosa_read_word, bosa->ux_calibration);
		}
		release_firmware(calibration);
	} else {
		ret = -EINVAL;
	}
out:
	mutex_unlock(&bosa->lock);
	return ret ? ret : size;
}
static DEVICE_ATTR_RW(maintenance);

static ssize_t diagnostics_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl_fttr_bosa *bosa = dev_get_drvdata(dev);
	struct rtl_fttr_optical_diagnostics value;
	const struct firmware *calibration = NULL;
	int ret;

	mutex_lock(&bosa->lock);
	if (!bosa->configured) {
		ret = -EIO;
		goto out;
	}
	if (bosa->model != RTL_FTTR_UX3326S) {
		ret = request_firmware_direct(&calibration, "realtek/rtl9617c/fttr-bosa-calibration.bin", dev);
		if (ret)
			goto out;
		ret = rtl_fttr_optical_validate(calibration->data, calibration->size);
		if (!ret && (calibration->data[3] != bosa->model ||
		    calibration->data[4] != bosa->client->addr << 1))
			ret = -EINVAL;
		if (ret)
			goto release;
	}
	ret = rtl_fttr_optical_diagnostics_read(bosa, rtl_fttr_bosa_read_word, bosa->model,
		calibration ? calibration->data : NULL, &value);
	if (!ret)
		ret = sysfs_emit(buf, "model=%u temperature_q8=%d voltage_100uv=%u bias_2ua=%u "
			"tx_power_q8=%lld rx_power_q8=%lld rx_supported=%u\n",
			bosa->model, value.temperature_q8, value.voltage_100uv, value.bias_2ua,
			value.tx_power_q8, value.rx_power_q8, value.rx_supported);
release:
	if (calibration)
		release_firmware(calibration);
out:
	mutex_unlock(&bosa->lock);
	return ret;
}
static DEVICE_ATTR_RO(diagnostics);

static struct attribute *rtl_fttr_bosa_attrs[] = {
	&dev_attr_calibration.attr,
	&dev_attr_maintenance.attr,
	&dev_attr_diagnostics.attr,
	NULL,
};
ATTRIBUTE_GROUPS(rtl_fttr_bosa);

static int rtl_fttr_bosa_probe(struct i2c_client *client)
{
	struct rtl_fttr_bosa *bosa;

	if (!i2c_check_functionality(client->adapter, I2C_FUNC_SMBUS_BYTE_DATA | I2C_FUNC_SMBUS_WORD_DATA))
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

static const struct i2c_device_id rtl_fttr_bosa_ids[] = {
	{ "tyfza1-fttr-bosa" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, rtl_fttr_bosa_ids);

static struct i2c_driver rtl_fttr_bosa_driver = {
	.probe = rtl_fttr_bosa_probe,
	.id_table = rtl_fttr_bosa_ids,
	.driver = {
		.name = "tyfza1-fttr-bosa",
		.of_match_table = rtl_fttr_bosa_match,
		.dev_groups = rtl_fttr_bosa_groups,
	},
};
module_i2c_driver(rtl_fttr_bosa_driver);
MODULE_DESCRIPTION("TYFZA1 native downstream optical calibration");
MODULE_LICENSE("GPL");
