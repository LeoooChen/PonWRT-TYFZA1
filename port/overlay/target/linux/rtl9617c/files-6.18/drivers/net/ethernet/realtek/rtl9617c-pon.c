// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Taurus PON platform binding and MAC state observation.
 * Register definitions: GPL Cortina-Access 2015 aal_pon.c/aal_mpcp.c,
 * Realtek Taurus register header, and the TYFZA1 stock DTB NI resources.
 */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/i2c.h>
#include <linux/etherdevice.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/rtnetlink.h>
#include "rtl9617c-pon.h"
#include "rtl9617c-bosa.h"
#include "rtl9617c-pon-fec.h"

#define PON_EPON_MODE BIT(0)
#define EPON_STATUS 0x0010
#define MPCP_REGISTERED 0x2014

int rtl_pon_serdes_configure(struct rtl9617c_pon *pon, enum rtl_pon_mode mode)
{
	int ret;

	mutex_lock(&pon->control_lock);
	ret = rtl_pon_serdes_configure_locked(pon, mode);
	mutex_unlock(&pon->control_lock);
	return ret;
}

int rtl_pon_epon_prepare(struct rtl9617c_pon *pon)
{
	int ret;

	mutex_lock(&pon->control_lock);
	ret = rtl_pon_epon_prepare_locked(pon);
	mutex_unlock(&pon->control_lock);
	return ret;
}

int rtl_pon_epon_traffic(struct rtl9617c_pon *pon, bool enable)
{
	int ret;

	mutex_lock(&pon->control_lock);
	ret = rtl_pon_epon_traffic_locked(pon, enable);
	mutex_unlock(&pon->control_lock);
	return ret;
}

void rtl_pon_mpcp_stop(struct rtl9617c_pon *pon)
{
	mutex_lock(&pon->control_lock);
	rtl_pon_mpcp_stop_locked(pon);
	mutex_unlock(&pon->control_lock);
}

int rtl_pon_mpcp_configure(struct rtl9617c_pon *pon)
{
	int ret;

	mutex_lock(&pon->control_lock);
	ret = rtl_pon_mpcp_configure_locked(pon);
	mutex_unlock(&pon->control_lock);
	return ret;
}

int rtl_pon_pdc_configure(struct rtl9617c_pon *pon)
{
	int ret;

	mutex_lock(&pon->control_lock);
	ret = rtl_pon_pdc_configure_locked(pon);
	mutex_unlock(&pon->control_lock);
	return ret;
}

int rtl_pon_puc_configure(struct rtl9617c_pon *pon)
{
	int ret;

	mutex_lock(&pon->control_lock);
	ret = rtl_pon_puc_configure_locked(pon);
	mutex_unlock(&pon->control_lock);
	return ret;
}

static ssize_t state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	u32 control = readl(pon->control);
	u32 status = 0, registered = 0;

	if (!(control & PON_EPON_MODE))
		return sysfs_emit(buf, "family=gpon control=%08x serdes_reset_n=%u register_reset_n=%u\n",
			control, !!(control & BIT(1)), !!(control & BIT(2)));
	status = readl(pon->mac + EPON_STATUS);
	registered = readl(pon->mac + MPCP_REGISTERED);
	return sysfs_emit(buf,
		"family=%s control=%08x serdes_reset_n=%u register_reset_n=%u epon_status=%08x signal_loss=%u pcs10g_sync=%u pcs1g_sync=%u registered_llids=%08x\n",
		control & PON_EPON_MODE ? "epon" : "gpon",
		control, !!(control & BIT(1)), !!(control & BIT(2)), status,
		!!(status & BIT(0)), !!(status & BIT(1)), !!(status & BIT(2)), registered);
}
static DEVICE_ATTR_RO(state);

static ssize_t bosa_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);

	return rtl9617c_bosa_state(pon->bosa_dev, buf);
}

static ssize_t bosa_store(struct device *dev, struct device_attribute *attr,
			 const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool prepare;
	int ret = kstrtobool(buf, &prepare);

	if (ret)
		return ret;
	if (!prepare)
		return -EINVAL;
	mutex_lock(&pon->control_lock);
	rtl_pon_epon_traffic_locked(pon, false);
	rtl_pon_mpcp_stop_locked(pon);
	ret = rtl9617c_bosa_prepare(pon->bosa_dev);
	mutex_unlock(&pon->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(bosa);

static ssize_t serdes_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;

	mutex_lock(&pon->lock);
	size = rtl_pon_serdes_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t serdes_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	unsigned int mode;
	int ret;

	for (mode = 0; mode < RTL_PON_MODE_COUNT; mode++)
		if (sysfs_streq(buf, rtl_pon_mode_name(mode)))
			break;
	if (mode == RTL_PON_MODE_COUNT)
		return -EINVAL;
	ret = rtl_pon_serdes_configure(pon, mode);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(serdes);

static ssize_t serdes_registers_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_serdes_registers(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}
static DEVICE_ATTR_RO(serdes_registers);

static ssize_t optical_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	u32 status = readl(pon->optical_status);
	return sysfs_emit(buf, "control=%08x status=%08x rx_los=%u module_absent=%u tx_signal_detect=%u tx_fault=%u tx_disabled=%u irq_count=%u last_irq=%08x\n",
		readl(pon->optical_control), status, !!(status & BIT(3)), !!(status & BIT(2)),
		!!(status & BIT(1)), !!(status & BIT(0)), gpiod_get_value_cansleep(pon->tx_disable),
		pon->optical_irq_count, pon->last_optical_irq);
}
static DEVICE_ATTR_RO(optical);

static ssize_t mac_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_epon_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t mac_store(struct device *dev, struct device_attribute *attr,
			const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool prepare;
	int ret = kstrtobool(buf, &prepare);
	if (ret)
		return ret;
	if (!prepare)
		return -EINVAL;
	ret = rtl_pon_epon_prepare(pon);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(mac);

static unsigned int rtl_fec_read(void *context, unsigned int reg)
{
	struct rtl9617c_pon *pon = context;

	return readl(pon->mac + reg);
}

static void rtl_fec_write(void *context, unsigned int reg, unsigned int value)
{
	struct rtl9617c_pon *pon = context;

	writel(value, pon->mac + reg);
}

static ssize_t fec_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;

	mutex_lock(&pon->lock);
	if (!rtl_pon_mode_is_epon(pon->mode))
		size = -EOPNOTSUPP;
	else
		size = sysfs_emit(buf, "supported=1 upstream=%u encoder=%08x btc=%08x report=%08x\n",
			pon->mode == RTL_PON_EPON_BI10G ? !(readl(pon->mac + 0x24) & BIT(0)) :
			!!(readl(pon->mac + 0x810) & BIT(1)), readl(pon->mac + 0x810),
			readl(pon->mac + 0x8050), readl(pon->mac + 0x8054));
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t fec_store(struct device *dev, struct device_attribute *attr, const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	const struct rtl_dq_io io = {pon, rtl_fec_read, rtl_fec_write, NULL};
	bool enable;
	int ret = kstrtobool(buf, &enable);

	if (ret)
		return ret;
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (!rtl_pon_mode_is_epon(pon->mode) || !pon->mac_prepared)
		ret = -EOPNOTSUPP;
	else {
		rtl_pon_fec_set(&io, pon->mode, enable);
		if (!enable && pon->mode != RTL_PON_EPON_BI10G)
			msleep(5);
	}
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(fec);

static ssize_t onu_mac_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	return sysfs_emit(buf, "%pM\n", pon->onu_mac);
}

static ssize_t onu_mac_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	u8 mac[ETH_ALEN];
	if (!mac_pton(buf, mac) || !is_valid_ether_addr(mac))
		return -EINVAL;
	mutex_lock(&pon->control_lock);
	rtnl_lock();
	mutex_lock(&pon->lock);
	if (pon->mpcp_enabled) {
		mutex_unlock(&pon->lock);
		rtnl_unlock();
		mutex_unlock(&pon->control_lock);
		return -EBUSY;
	}
	ether_addr_copy(pon->onu_mac, mac);
	mutex_unlock(&pon->lock);
	rtl_pon_oam_address(pon);
	rtnl_unlock();
	mutex_unlock(&pon->control_lock);
	return count;
}
static DEVICE_ATTR_RW(onu_mac);

static ssize_t llid_count_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	return sysfs_emit(buf, "%u\n", pon->llid_count);
}

static ssize_t llid_count_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	unsigned int value;
	int ret = kstrtouint(buf, 0, &value);
	if (ret)
		return ret;
	if (!value || value > 32)
		return -EINVAL;
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	if (pon->mpcp_enabled) {
		mutex_unlock(&pon->lock);
		mutex_unlock(&pon->control_lock);
		return -EBUSY;
	}
	mutex_unlock(&pon->lock);
	ret = rtl_pon_oam_resize(pon, value);
	mutex_lock(&pon->lock);
	if (!ret)
		pon->llid_count = value;
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(llid_count);

static ssize_t mpcp_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_mpcp_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t mpcp_store(struct device *dev, struct device_attribute *attr,
			 const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool enabled;
	int ret = kstrtobool(buf, &enabled);
	if (ret)
		return ret;
	if (enabled)
		ret = rtl_pon_mpcp_configure(pon);
	else
		rtl_pon_mpcp_stop(pon);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(mpcp);

static ssize_t llid_table_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_llid_table(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t llid_table_store(struct device *dev, struct device_attribute *attr,
			       const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	char operation[7];
	unsigned int index, mac, llid, multicast;
	bool add;
	int ret;
	if (sscanf(buf, "%6s %u %u %x %u", operation, &index, &mac, &llid, &multicast) != 5 ||
	    index > 63 || mac > 31 || llid > 0xffff || multicast > 1)
		return -EINVAL;
	if (!strcmp(operation, "add"))
		add = true;
	else if (!strcmp(operation, "delete"))
		add = false;
	else
		return -EINVAL;
	mutex_lock(&pon->control_lock);
	mutex_lock(&pon->lock);
	ret = rtl_pon_llid_update(pon, add, index, mac, llid, multicast);
	if (!ret)
		ret = rtl_pon_pdc_refresh(pon);
	mutex_unlock(&pon->lock);
	mutex_unlock(&pon->control_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(llid_table);

static ssize_t pdc_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_pdc_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t pdc_store(struct device *dev, struct device_attribute *attr,
			const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool enabled;
	int ret = kstrtobool(buf, &enabled);
	if (ret)
		return ret;
	if (!enabled)
		return -EINVAL;
	ret = rtl_pon_pdc_configure(pon);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(pdc);

static ssize_t puc_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	ssize_t size;
	mutex_lock(&pon->lock);
	size = rtl_pon_puc_state(pon, buf);
	mutex_unlock(&pon->lock);
	return size;
}

static ssize_t puc_store(struct device *dev, struct device_attribute *attr,
			const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool enabled;
	int ret = kstrtobool(buf, &enabled);
	if (ret)
		return ret;
	if (!enabled)
		return -EINVAL;
	ret = rtl_pon_puc_configure(pon);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(puc);

static ssize_t traffic_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	struct net_device *oam = pon->oam_endpoint.netdev;
	unsigned long received = 0, transmitted = 0, receive_drops = 0, transmit_drops = 0;
	unsigned int i;

	rcu_read_lock();
	for (i = 0; i < 32; i++) {
		struct net_device *port = rcu_dereference(pon->oam_devices[i]);

		if (port) {
			received += port->stats.rx_packets;
			transmitted += port->stats.tx_packets;
			receive_drops += port->stats.rx_dropped;
			transmit_drops += port->stats.tx_dropped;
		}
	}
	rcu_read_unlock();

	return sysfs_emit(buf, "enabled=%u oam_device=%s carrier=%u last_llid=%u last_source=%u rx=%lu tx=%lu rx_dropped=%lu tx_dropped=%lu\n",
		pon->traffic_enabled, oam->name, netif_carrier_ok(oam),
		READ_ONCE(pon->last_oam_llid), READ_ONCE(pon->last_oam_source),
		received, transmitted, receive_drops, transmit_drops);
}

static ssize_t traffic_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	struct rtl9617c_pon *pon = dev_get_drvdata(dev);
	bool enable;
	int ret = kstrtobool(buf, &enable);

	if (ret)
		return ret;
	ret = rtl_pon_epon_traffic(pon, enable);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(traffic);

static struct attribute *rtl9617c_pon_attrs[] = {
	&dev_attr_state.attr,
	&dev_attr_bosa.attr,
	&dev_attr_serdes.attr,
	&dev_attr_serdes_registers.attr,
	&dev_attr_optical.attr,
	&dev_attr_mac.attr,
	&dev_attr_fec.attr,
	&dev_attr_onu_mac.attr,
	&dev_attr_llid_count.attr,
	&dev_attr_mpcp.attr,
	&dev_attr_llid_table.attr,
	&dev_attr_pdc.attr,
	&dev_attr_puc.attr,
	&dev_attr_traffic.attr,
	NULL,
};
ATTRIBUTE_GROUPS(rtl9617c_pon);

static int rtl9617c_pon_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rtl9617c_pon *pon;

	pon = devm_kzalloc(dev, sizeof(*pon), GFP_KERNEL);
	if (!pon)
		return -ENOMEM;
	pon->dev = dev;
	pon->tx_disable = devm_gpiod_get(dev, "tx-disable", GPIOD_OUT_HIGH);
	if (IS_ERR(pon->tx_disable))
		return dev_err_probe(dev, PTR_ERR(pon->tx_disable), "PON TX-disable GPIO\n");
	{
		struct device_node *node = of_parse_phandle(dev->of_node, "realtek,optical-front-end", 0);
		struct i2c_client *client = of_find_i2c_device_by_node(node);

		of_node_put(node);
		if (!client)
			return -EPROBE_DEFER;
		if (!i2c_get_clientdata(client)) {
			put_device(&client->dev);
			return -EPROBE_DEFER;
		}
		pon->bosa_dev = &client->dev;
		if (!device_link_add(dev, pon->bosa_dev, DL_FLAG_AUTOREMOVE_CONSUMER)) {
			put_device(&client->dev);
			return -ENOMEM;
		}
		put_device(&client->dev);
	}
	{
		struct device_node *node = of_parse_phandle(dev->of_node, "realtek,network-engine", 0);
		struct platform_device *engine = of_find_device_by_node(node);
		of_node_put(node);
		if (!engine)
			return -EPROBE_DEFER;
		if (!platform_get_drvdata(engine)) {
			put_device(&engine->dev);
			return -EPROBE_DEFER;
		}
		pon->ni_dev = &engine->dev;
		if (!device_link_add(dev, pon->ni_dev, DL_FLAG_AUTOREMOVE_CONSUMER)) {
			put_device(&engine->dev);
			return -ENOMEM;
		}
		put_device(&engine->dev);
	}
	pon->mode = RTL_PON_EPON_D10G;
	mutex_init(&pon->lock);
	mutex_init(&pon->control_lock);
	rtl_pon_serdes_init_work(pon);
	pon->control = devm_platform_ioremap_resource(pdev, 1);
	if (IS_ERR(pon->control))
		return PTR_ERR(pon->control);
	pon->mac = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(pon->mac))
		return PTR_ERR(pon->mac);
	pon->epon_control = devm_platform_ioremap_resource(pdev, 2);
	if (IS_ERR(pon->epon_control))
		return PTR_ERR(pon->epon_control);
	pon->gpon_control = devm_platform_ioremap_resource(pdev, 7);
	if (IS_ERR(pon->gpon_control))
		return PTR_ERR(pon->gpon_control);
	pon->serdes_init = devm_platform_ioremap_resource(pdev, 3);
	if (IS_ERR(pon->serdes_init))
		return PTR_ERR(pon->serdes_init);
	of_property_read_u32(dev->of_node, "realtek,burst-polarity", &pon->burst_polarity);
	if (pon->burst_polarity > 1)
		return -EINVAL;
	pon->optical_control = devm_platform_ioremap_resource(pdev, 4);
	if (IS_ERR(pon->optical_control))
		return PTR_ERR(pon->optical_control);
	pon->optical_status = devm_platform_ioremap_resource(pdev, 5);
	if (IS_ERR(pon->optical_status))
		return PTR_ERR(pon->optical_status);
	{
		struct resource optical = *platform_get_resource(pdev, IORESOURCE_MEM, 4);

		optical.start += 8;
		optical.end = optical.start + 7;
		optical.name = "optical-irq";
		pon->optical_irq = devm_ioremap_resource(dev, &optical);
		if (IS_ERR(pon->optical_irq))
			return PTR_ERR(pon->optical_irq);
	}
	of_property_read_u32(dev->of_node, "realtek,los-invert", &pon->los_invert);
	if (pon->los_invert > 1)
		return -EINVAL;
	pon->top_irq = devm_platform_ioremap_resource(pdev, 6);
	if (IS_ERR(pon->top_irq))
		return PTR_ERR(pon->top_irq);
	{
		int irq = platform_get_irq(pdev, 0);
		int ret;
		if (irq < 0)
			return irq;
		ret = rtl_pon_mpcp_init(pon, irq);
		if (ret)
			return ret;
	}
	platform_set_drvdata(pdev, pon);
	dev_info(dev, "native PON MAC binding: global control=%08x\n", readl(pon->control));
	return rtl_pon_oam_init(pon);
}

static void rtl9617c_pon_remove(struct platform_device *pdev)
{
	struct rtl9617c_pon *pon = platform_get_drvdata(pdev);

	mutex_lock(&pon->control_lock);
	rtl_pon_epon_traffic_locked(pon, false);
	rtl_pon_mpcp_stop_locked(pon);
	rtl_pon_serdes_stop_work(pon);
	writel(0, pon->top_irq + 4);
	writel(0, pon->optical_irq + 4);
	synchronize_irq(pon->mpcp_irq);
	rtl_pon_oam_remove(pon);
	mutex_unlock(&pon->control_lock);
}

static const struct of_device_id rtl9617c_pon_of_match[] = {
	{ .compatible = "realtek,rtl9617c-pon" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl9617c_pon_of_match);

static struct platform_driver rtl9617c_pon_driver = {
	.probe = rtl9617c_pon_probe,
	.remove = rtl9617c_pon_remove,
	.driver = {
		.name = "rtl9617c-pon",
		.of_match_table = rtl9617c_pon_of_match,
		.dev_groups = rtl9617c_pon_groups,
	},
};
module_platform_driver(rtl9617c_pon_driver);
MODULE_DESCRIPTION("Native RTL9617C PON MAC platform binding");
MODULE_LICENSE("GPL");
