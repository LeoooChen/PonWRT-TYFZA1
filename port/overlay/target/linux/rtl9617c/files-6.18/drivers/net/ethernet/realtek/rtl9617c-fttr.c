// SPDX-License-Identifier: GPL-2.0-only
/* Native TYFZA1 MiniOLT control transport, derived from stock machine code. */
#include <linux/delay.h>
#include <linux/etherdevice.h>
#include <linux/firmware.h>
#include <linux/fpga/fpga-mgr.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/mdio.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/phy.h>
#include <linux/regmap.h>
#include <linux/swab.h>
#include <linux/unaligned.h>

#include "rtl9617c-fttr-mdio.h"
#include "rtl9617c-fttr-core.h"
#include "rtl9617c-fttr-i2c.h"
#include "rtl9617c-fttr-omci.h"
#include "rtl9617c-fttr-onu.h"
#include "rtl9617c-fttr-move.h"
#include "rtl9617c-fttr-serdes.h"
#include "rtl9617c-fttr-fpga.h"
#include "rtl9617c-fttr-bosa.h"

struct rtl_fttr {
	struct mdio_device *mdio;
	struct regmap *map;
	struct rtl_fttr_mdio_io io;
	struct rtl_fttr_reg_io reg_io;
	struct i2c_adapter optical;
	struct i2c_client *optical_client;
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
	bool word = size == I2C_SMBUS_WORD_DATA;
	unsigned int value;
	int ret;

	if (flags & I2C_CLIENT_TEN)
		return -EOPNOTSUPP;
	if (size != I2C_SMBUS_BYTE_DATA && !word)
		return -EOPNOTSUPP;
	/* MiniOLT word mode puts the first register in the high byte; Linux
	 * SMBus words put the first wire byte in the low byte.
	 */
	value = read ? 0 : word ? swab16(data->word) : data->byte;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_i2c_xfer(&fttr->reg_io, addr, command, &value, word, read);
	mutex_unlock(&fttr->control_lock);
	if (!ret && read) {
		if (word)
			data->word = swab16(value);
		else
			data->byte = value;
	}
	return ret;
}

static u32 rtl_fttr_i2c_functionality(struct i2c_adapter *adapter)
{
	return I2C_FUNC_SMBUS_BYTE_DATA | I2C_FUNC_SMBUS_WORD_DATA;
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

static ssize_t fpga_init_store(struct device *dev, struct device_attribute *attr,
		const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	bool calibrated;
	int ret;

	ret = kstrtobool(buf, &calibrated);
	if (ret)
		return ret;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_fpga_init(&fttr->reg_io, fttr->backend, calibrated);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(fpga_init);

static ssize_t fpga_auth_store(struct device *dev, struct device_attribute *attr,
		const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	const struct firmware *ciphertext;
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_fpga_auth_needed(&fttr->reg_io, fttr->backend);
	if (ret > 0) {
		ret = request_firmware_direct(&ciphertext, "realtek/rtl9617c/fttr-fpga-ciphertext.bin", dev);
		if (!ret) {
			ret = rtl_fttr_fpga_auth(&fttr->reg_io, ciphertext->data, ciphertext->size);
			release_firmware(ciphertext);
		}
	}
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(fpga_auth);

static ssize_t optical_init_store(struct device *dev, struct device_attribute *attr,
		const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	struct i2c_board_info info = { I2C_BOARD_INFO("tyfza1-fttr-bosa", 0) };
	const struct firmware *calibration;
	struct i2c_client *client;
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	ret = request_firmware_direct(&calibration, "realtek/rtl9617c/fttr-bosa-calibration.bin", dev);
	if (ret)
		return ret;
	ret = rtl_fttr_optical_validate(calibration->data, calibration->size);
	if (!ret)
		info.addr = calibration->data[4] >> 1;
	release_firmware(calibration);
	if (ret)
		return ret;
	mutex_lock(&fttr->control_lock);
	if (fttr->optical_client) {
		ret = fttr->optical_client->addr == info.addr ? 0 : -EBUSY;
	} else {
		/* The FPGA sample has no fixed optical model/address in its DT.
		 * Instantiate only the address supplied by its validated factory file.
		 */
		client = i2c_new_client_device(&fttr->optical, &info);
		ret = PTR_ERR_OR_ZERO(client);
		if (!ret)
			fttr->optical_client = client;
	}
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(optical_init);

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
	ret = rtl_fttr_omci_link_read(&fttr->reg_io, fttr->backend, &link);
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
	ret = rtl_fttr_omci_init(&fttr->reg_io, fttr->backend, host);
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
	ret = rtl_fttr_tcont_program(&fttr->reg_io, fttr->backend, id, tcont, fixed, assured, maximum);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(tcont);

/* Eight-byte LE records, indexed by ONU * 8 + physical T-CONT: fixed,
 * assured|max. FPGA1 has only physical slot0; other slots return EINVAL.
 */
static ssize_t tcont_state_read(struct file *file, struct kobject *kobj,
		const struct bin_attribute *attr, char *buf, loff_t offset, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(kobj_to_dev(kobj));
	unsigned int index = offset / 8, fixed, limits;
	int ret;

	if (offset % 8 || count < 8)
		return -EINVAL;
	if (index >= RTL_FTTR_ONUS * 8)
		return 0;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_tcont_read(&fttr->reg_io, fttr->backend, index / 8, index % 8, &fixed, &limits);
	mutex_unlock(&fttr->control_lock);
	if (ret)
		return ret;
	put_unaligned_le32(fixed, buf);
	put_unaligned_le32(limits, buf + 4);
	return 8;
}
static BIN_ATTR_RO(tcont_state, RTL_FTTR_ONUS * 8 * 8);

static ssize_t management_grant_store(struct device *dev, struct device_attribute *attr,
				     const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int id, enable;
	int ret;

	if (sscanf(buf, "%u %u", &id, &enable) != 2 || id >= RTL_FTTR_ONUS || enable > 1)
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_management_grant(&fttr->reg_io, id, enable);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(management_grant);

/* Fixed 20-byte records: three LE32 state words followed by eight serial bytes. */
static ssize_t onu_state_read(struct file *file, struct kobject *kobj,
		const struct bin_attribute *attr, char *buf, loff_t offset, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(kobj_to_dev(kobj));
	struct rtl_fttr_onu onu;
	int ret;

	if (offset % 20 || count < 20)
		return -EINVAL;
	if (offset >= RTL_FTTR_ONUS * 20)
		return 0;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_onu_read(&fttr->reg_io, offset / 20, &onu);
	mutex_unlock(&fttr->control_lock);
	if (ret)
		return ret;
	put_unaligned_le32(onu.downstream, buf);
	put_unaligned_le32(onu.upstream, buf + 4);
	put_unaligned_le32(onu.eqd, buf + 8);
	memcpy(buf + 12, onu.serial, 8);
	return 20;
}
static BIN_ATTR_RO(onu_state, RTL_FTTR_ONUS * 20);

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
	ret = rtl_fttr_onu_block(&fttr->reg_io, fttr->backend, id, blocked);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(onu_block);

static ssize_t onu_admin_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int id, blocked;
	unsigned char serial[8];
	char hex[17], extra;
	int ret;

	if (sscanf(buf, "%u %u %16s %c", &id, &blocked, hex, &extra) != 3 ||
	    id >= RTL_FTTR_ONUS || blocked > 3 || strlen(hex) != 16 ||
	    hex2bin(serial, hex, sizeof(serial)))
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_onu_admin(&fttr->reg_io, fttr->backend, id, blocked, serial);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(onu_admin);

static ssize_t onu_move_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct rtl_fttr *fttr = dev_get_drvdata(dev);
	unsigned int from, to;
	unsigned char serial[8], target[8];
	char hex[17], target_hex[17], extra;
	int ret;

	if (sscanf(buf, "%u %u %16s %16s %c", &from, &to, hex, target_hex, &extra) != 4 ||
	    from >= RTL_FTTR_ONUS || to >= RTL_FTTR_ONUS || from == to ||
	    strlen(hex) != 16 || strlen(target_hex) != 16 ||
	    hex2bin(serial, hex, sizeof(serial)) || hex2bin(target, target_hex, sizeof(target)))
		return -EINVAL;
	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_onu_move(&fttr->reg_io, fttr->backend, from, to, serial, target);
	mutex_unlock(&fttr->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(onu_move);

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
	ret = rtl_fttr_ploam_send(&fttr->reg_io, words, 3, repeat);
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
	ret = rtl_fttr_tx_enable(&fttr->reg_io, fttr->backend, enabled);
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
	&dev_attr_management_grant.attr,
	&dev_attr_fpga_init.attr,
	&dev_attr_fpga_auth.attr,
	&dev_attr_optical_init.attr,
	&dev_attr_onu_admin.attr,
	&dev_attr_onu_move.attr,
	NULL,
};

static umode_t rtl_fttr_attr_visible(struct kobject *kobj, struct attribute *attr, int n)
{
	struct rtl_fttr *fttr = dev_get_drvdata(kobj_to_dev(kobj));

	if (attr == &dev_attr_fpga_auth.attr)
		return fttr->backend == RTL_FTTR_FPGA1 ? attr->mode : 0;
	if (attr == &dev_attr_onu_move.attr)
		return fttr->backend == RTL_FTTR_FPGA2 ? 0 : attr->mode;
	if (attr == &dev_attr_fpga_init.attr || attr == &dev_attr_optical_init.attr)
		return fttr->backend == RTL_FTTR_ASIC9690 ? 0 : attr->mode;
	if (attr == &dev_attr_omci_link.attr || attr == &dev_attr_onu_table.attr ||
	    attr == &dev_attr_tx_enable.attr || attr == &dev_attr_tcont.attr ||
	    attr == &dev_attr_onu_admin.attr)
		return attr->mode;
	if (n >= 2 && fttr->backend != RTL_FTTR_ASIC9690)
		return 0;
	return attr->mode;
}

static umode_t rtl_fttr_bin_visible(struct kobject *kobj, const struct bin_attribute *attr, int n)
{
	return attr->attr.mode;
}

static const struct bin_attribute * const rtl_fttr_bin_attrs[] = {
	&bin_attr_onu_state,
	&bin_attr_tcont_state,
	NULL,
};

static const struct attribute_group rtl_fttr_group = {
	.attrs = rtl_fttr_attrs,
	.is_visible = rtl_fttr_attr_visible,
	.bin_attrs = rtl_fttr_bin_attrs,
	.is_bin_visible = rtl_fttr_bin_visible,
};
__ATTRIBUTE_GROUPS(rtl_fttr);

#if IS_REACHABLE(CONFIG_FPGA)
static void rtl_fttr_fpga_release(void *data)
{
	struct fpga_manager *mgr = data;

	fpga_mgr_put(mgr);
}
#endif

/* The boot-image supplier rejects manual programming while bound. Its device
 * link retires MDIO/I2C consumers before an unbind/reprobe can reload it. */
static int rtl_fttr_fpga_acquire(struct device *dev)
{
	struct device_node *node;
	int ret = -EOPNOTSUPP;
#if IS_REACHABLE(CONFIG_FPGA)
	struct fpga_manager *mgr;
#endif

	if (!of_find_property(dev->of_node, "fpga-mgr", NULL))
		return 0;
	node = of_parse_phandle(dev->of_node, "fpga-mgr", 0);
	if (!node)
		return -EINVAL;
	if (!of_device_is_compatible(node, "pango,tyfza1-pg2l100h") ||
	    !of_find_property(node, "firmware-name", NULL)) {
		of_node_put(node);
		return -EINVAL;
	}
#if IS_REACHABLE(CONFIG_FPGA)
	mgr = of_fpga_mgr_get(node);
	if (IS_ERR(mgr)) {
		ret = PTR_ERR(mgr) == -ENODEV ? -EPROBE_DEFER : PTR_ERR(mgr);
		goto out;
	}
	if (!device_link_add(dev, mgr->dev.parent, DL_FLAG_AUTOREMOVE_CONSUMER)) {
		ret = -ENOMEM;
		goto put;
	}
	if (!device_is_bound(mgr->dev.parent)) {
		ret = -EPROBE_DEFER;
		goto put;
	}
	ret = fpga_mgr_lock(mgr);
	if (ret)
		goto put;
	if (mgr->state != FPGA_MGR_STATE_OPERATING || mgr->mops->state(mgr) != FPGA_MGR_STATE_OPERATING) {
		ret = -EIO;
		fpga_mgr_unlock(mgr);
		goto put;
	}
	fpga_mgr_unlock(mgr);
	ret = devm_add_action_or_reset(dev, rtl_fttr_fpga_release, mgr);
	goto out;
put:
	fpga_mgr_put(mgr);
out:
#endif
	of_node_put(node);
	return ret;
}

static void rtl_fttr_optical_unregister(void *context)
{
	struct rtl_fttr *fttr = context;

	if (fttr->optical_client)
		i2c_unregister_device(fttr->optical_client);
}

static int rtl_fttr_probe(struct mdio_device *mdio)
{
	struct device *dev = &mdio->dev;
	struct rtl_fttr *fttr;
	int ret;

	ret = rtl_fttr_fpga_acquire(dev);
	if (ret)
		return dev_err_probe(dev, ret, "MiniOLT FPGA dependency unavailable\n");
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
	}
	dev_set_drvdata(dev, fttr);
	fttr->reg_io = (struct rtl_fttr_reg_io) {
		.context = fttr->map,
		.read = rtl_fttr_reg_read,
		.write = rtl_fttr_reg_write,
		.delay = rtl_fttr_delay,
	};
	ret = rtl_fttr_tx_enable(&fttr->reg_io, fttr->backend, 0);
	if (ret)
		return dev_err_probe(dev, ret, "MiniOLT initial TX disable failed\n");
	fttr->optical.owner = THIS_MODULE;
	fttr->optical.algo = &rtl_fttr_i2c_algorithm;
	fttr->optical.dev.parent = dev;
	fttr->optical.dev.of_node = dev->of_node;
	strscpy(fttr->optical.name, "TYFZA1 FTTR optical I2C", sizeof(fttr->optical.name));
	i2c_set_adapdata(&fttr->optical, fttr);
	ret = devm_i2c_add_adapter(dev, &fttr->optical);
	if (ret)
		return ret;
	return devm_add_action_or_reset(dev, rtl_fttr_optical_unregister, fttr);
}

static void rtl_fttr_remove(struct mdio_device *mdio)
{
	struct rtl_fttr *fttr = dev_get_drvdata(&mdio->dev);
	int ret;

	mutex_lock(&fttr->control_lock);
	ret = rtl_fttr_tx_enable(&fttr->reg_io, fttr->backend, 0);
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
