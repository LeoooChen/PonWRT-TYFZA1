// SPDX-License-Identifier: GPL-2.0-only
/* Native TYFZA1 radio endpoint backend. No vendor host module is loaded. */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/pci.h>
#include "fw.h"
#include "power.h"
#include "download.h"
#include "transport.h"

struct rtl_ax_ap {
	struct pci_dev *pdev;
	void __iomem *bar;
	struct rtl_ax_io io;
	struct rtl_ax_calibration calibration;
	struct rtl_ax_identity identity;
	struct rtl_ax_ap_image firmware;
	struct rtl_ax_fw_transport transport;
	unsigned char *h2c_ring, *h2c_packet;
	dma_addr_t h2c_ring_dma, h2c_packet_dma;
	unsigned int h2c_producer;
	unsigned int h2c_available[2];
	unsigned char chip;
	bool powered;
	bool download_prepared;
	bool firmware_ready;
};

static unsigned char rtl_ax_read8(void *ctx, unsigned int reg)
{
	return readb(((struct rtl_ax_ap *)ctx)->bar + reg);
}

static unsigned short rtl_ax_read16(void *ctx, unsigned int reg)
{
	return readw(((struct rtl_ax_ap *)ctx)->bar + reg);
}

static unsigned int rtl_ax_read32(void *ctx, unsigned int reg)
{
	return readl(((struct rtl_ax_ap *)ctx)->bar + reg);
}

static void rtl_ax_write8(void *ctx, unsigned int reg, unsigned char value)
{
	writeb(value, ((struct rtl_ax_ap *)ctx)->bar + reg);
}

static void rtl_ax_write16(void *ctx, unsigned int reg, unsigned short value)
{
	writew(value, ((struct rtl_ax_ap *)ctx)->bar + reg);
}

static void rtl_ax_write32(void *ctx, unsigned int reg, unsigned int value)
{
	writel(value, ((struct rtl_ax_ap *)ctx)->bar + reg);
}

static void rtl_ax_delay_us(void *ctx, unsigned int usec)
{
	fsleep(usec);
}

static int rtl_ax_poll32(void *ctx, unsigned int reg, unsigned int mask,
	unsigned int expected, unsigned int delay_us, unsigned int timeout_us,
	unsigned int *last)
{
	struct rtl_ax_ap *radio = ctx;

	return readl_poll_timeout(radio->bar + reg, *last,
				 (*last & mask) == expected, delay_us, timeout_us);
}

static int rtl_ax_h2c_send(void *ctx, const unsigned char *data,
			   unsigned int bytes, int section)
{
	struct rtl_ax_ap *radio = ctx;
	unsigned int next, last, class = section ? 1 : 0;
	unsigned int slot = class * RTL_AX_H2C_CLASS_BUFFERS + radio->h2c_available[class];
	unsigned int offset = slot * RTL_AX_H2C_BUFFER_BYTES;
	int length, ret;

	length = rtl_ax_fw_packet(radio->h2c_packet + offset, RTL_AX_H2C_BUFFER_BYTES,
				data, bytes, 0, section);
	if (length < 0)
		return length;
	rtl_ax_txbd(radio->h2c_ring + radio->h2c_producer * 8,
		    lower_32_bits(radio->h2c_packet_dma + offset), length);
	next = (radio->h2c_producer + 1) % RTL_AX_H2C_ENTRIES;
	/* Publish the coherent packet/descriptor before advancing producer. */
	dma_wmb();
	rtl_ax_write16(radio, RTL_AX_H2C_INDEX, next);
	ret = radio->io.poll32(radio, RTL_AX_H2C_INDEX, 0x3ff0000, next << 16,
			       10, 400000, &last);
	if (ret)
		return ret;
	dma_rmb();
	radio->h2c_producer = next;
	/* The board sets txbd_upd_lmt=1 and txbd_multi_tag=8. A consumed
	 * packet remains in its DATA/LONG_DATA work ring through eight more
	 * completions of that class. The ninth slot supplies the next packet. */
	radio->h2c_available[class] =
		(radio->h2c_available[class] + 1) % RTL_AX_H2C_CLASS_BUFFERS;
	return 0;
}

static int rtl_ax_wait_firmware(void *ctx, unsigned int *control)
{
	struct rtl_ax_ap *radio = ctx;

	/* Linux elapsed-time polling retains the original 400ms bound even
	 * when the scheduler extends a requested polling sleep. */
	return readb_poll_timeout(radio->bar + RTL_AX_WCPU_CONTROL, *control,
				 rtl_ax_fw_result(*control) != -EINPROGRESS, 10, 400000);
}

static void rtl_ax_ring_free(struct rtl_ax_ap *radio)
{
	if (radio->h2c_packet)
		dma_free_coherent(&radio->pdev->dev, RTL_AX_H2C_POOL_BYTES,
				  radio->h2c_packet, radio->h2c_packet_dma);
	if (radio->h2c_ring)
		dma_free_coherent(&radio->pdev->dev, RTL_AX_H2C_ENTRIES * 8,
				  radio->h2c_ring, radio->h2c_ring_dma);
	radio->h2c_packet = radio->h2c_ring = NULL;
}

static int rtl_ax_ring_prepare(struct rtl_ax_ap *radio)
{
	radio->h2c_ring = dma_alloc_coherent(&radio->pdev->dev, RTL_AX_H2C_ENTRIES * 8,
					    &radio->h2c_ring_dma, GFP_KERNEL);
	if (!radio->h2c_ring)
		return -ENOMEM;
	radio->h2c_packet = dma_alloc_coherent(&radio->pdev->dev, RTL_AX_H2C_POOL_BYTES,
					      &radio->h2c_packet_dma, GFP_KERNEL);
	if (!radio->h2c_packet)
		return -ENOMEM;
	memset(radio->h2c_ring, 0, RTL_AX_H2C_ENTRIES * 8);
	return rtl_ax_h2c_ring_configure(&radio->io, lower_32_bits(radio->h2c_ring_dma));
}

static int rtl_ax_load_firmware(struct rtl_ax_ap *radio, const struct firmware *fw)
{
	int ret;

	ret = rtl_ax_ring_prepare(radio);
	if (ret)
		return ret;
	radio->transport = (struct rtl_ax_fw_transport) {
		.io = &radio->io, .ctx = radio, .send = rtl_ax_h2c_send,
		.wait_ready = rtl_ax_wait_firmware,
	};
	pci_set_master(radio->pdev);
	rtl_ax_h2c_start(&radio->io);
	ret = rtl_ax_wcpu_enable(&radio->io);
	if (!ret)
		ret = rtl_ax_fw_download(&radio->transport, fw->data, &radio->firmware);
	rtl_ax_hci_pause(&radio->io);
	pci_clear_master(radio->pdev);
	if (ret) {
		dev_err(&radio->pdev->dev,
			"AP download failed: %d, control=0x%08x index=0x%08x packets=%u bytes=%u\n",
			ret, rtl_ax_read32(radio, RTL_AX_WCPU_CONTROL),
			rtl_ax_read32(radio, RTL_AX_H2C_INDEX), radio->transport.packets,
			radio->transport.transferred_bytes);
		return ret;
	}
	radio->firmware_ready = true;
	return 0;
}

static ssize_t radio_state_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_ap_image *image = &radio->firmware;

	return sysfs_emit(buffer,
		"chip=0x%02x hardware_cv=%u cv=%u factory=%c adie=0x%04x "
		"power=%s calibration=0x%02x efuse5ea=0x%02x "
		"firmware=%u.%u.%u.%u firmware_cut=%u sections=%u "
		"firmware_state=%s download_memory=%s hci=paused "
		"transport=channel12 packets=%u bytes=%u control=0x%02x mac80211=pending\n",
		radio->chip, radio->identity.hardware_cv, radio->identity.cv,
		radio->identity.factory, radio->identity.adie, radio->powered ? "on" : "off",
		radio->calibration.tag, radio->calibration.process,
		image->version[0], image->version[1], image->version[2], image->version[3],
		image->cut, image->section_count, radio->firmware_ready ? "ready" : "off",
		radio->download_prepared ? "ready" : "off", radio->transport.packets,
		radio->transport.transferred_bytes, radio->transport.last_control);
}
static DEVICE_ATTR_RO(radio_state);
static struct attribute *rtl_ax_attrs[] = {
	&dev_attr_radio_state.attr, NULL,
};
ATTRIBUTE_GROUPS(rtl_ax);

static void rtl_ax_stop(struct rtl_ax_ap *radio)
{
	int ret;

	if (!radio->powered)
		return;
	pci_clear_master(radio->pdev);
	if (radio->download_prepared) {
		rtl_ax_download_stop(&radio->io);
		rtl_ax_wcpu_stop(&radio->io);
		radio->firmware_ready = false;
		radio->download_prepared = false;
	}
	ret = rtl_ax_ap_power_off(&radio->io);
	if (ret)
		dev_err(&radio->pdev->dev, "AP power off failed: %d\n", ret);
	if (!ret)
		radio->powered = false;
	/* Bus mastering was cleared before pausing/resetting radio DMA and
	 * powering down the MAC. The host ring no longer belongs to the device. */
	rtl_ax_ring_free(radio);
}

static int rtl_ax_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
	const struct firmware *fw;
	const char *name;
	struct rtl_ax_ap *radio;
	int bar, ret;

	ret = pcim_enable_device(pdev);
	if (ret)
		return ret;
	pci_clear_master(pdev);
	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;
	radio = devm_kzalloc(&pdev->dev, sizeof(*radio), GFP_KERNEL);
	if (!radio)
		return -ENOMEM;
	radio->pdev = pdev;
	/* The original driver chooses the first memory BAR, not a fixed index. */
	bar = pci_select_bars(pdev, IORESOURCE_MEM);
	if (!bar)
		return -ENODEV;
	bar = __ffs(bar);
	if (pci_resource_len(pdev, bar) < RTL_AX_CMAC + sizeof(u32))
		return -ENODEV;
	radio->bar = pcim_iomap_region(pdev, bar, "rtl_ax_ap");
	if (IS_ERR(radio->bar))
		return PTR_ERR(radio->bar);
	radio->io = (struct rtl_ax_io) {
		.ctx = radio,
		.read8 = rtl_ax_read8, .read16 = rtl_ax_read16, .read32 = rtl_ax_read32,
		.write8 = rtl_ax_write8, .write16 = rtl_ax_write16, .write32 = rtl_ax_write32,
		.delay_us = rtl_ax_delay_us, .poll32 = rtl_ax_poll32,
	};
	radio->chip = rtl_ax_read8(radio, RTL_AX_CHIP);
	radio->identity.hardware_cv = rtl_ax_read8(radio, RTL_AX_CONFIG + 1) >> 4;
	if (radio->chip != RTL_AX_8852C && radio->chip != RTL_AX_8192XB)
		return -ENODEV;
	pci_set_drvdata(pdev, radio);
	ret = rtl_ax_ap_power_on(&radio->io, radio->chip, &radio->calibration);
	if (ret) {
		int off = rtl_ax_ap_power_off(&radio->io);

		dev_err(&pdev->dev, "AP power on failed: %d (power off: %d)\n", ret, off);
		return ret;
	}
	radio->powered = true;
	ret = rtl_ax_read_identity(&radio->io, radio->chip, &radio->calibration,
				   &radio->identity);
	if (ret)
		goto stop;
	name = rtl_ax_ap_name(radio->chip == RTL_AX_8852C ? 2 : 3, radio->identity.cv);
	ret = request_firmware(&fw, name, &pdev->dev);
	if (ret)
		goto stop;
	ret = rtl_ax_ap_parse(fw->data, fw->size, &radio->firmware);
	if (ret)
		goto release;
	ret = rtl_ax_ap_match(radio->chip, radio->identity.cv, &radio->firmware);
	if (ret)
		goto release;
	/* Complete the stock CPU-off/platform-reset workaround before setting
	 * DLE and host DMA rings. Their setup survives no unverified reset bit. */
	rtl_ax_wcpu_stop(&radio->io);
	ret = rtl_ax_download_prepare(&radio->io);
	if (ret)
		goto release;
	radio->download_prepared = true;
	ret = rtl_ax_load_firmware(radio, fw);
	release_firmware(fw);
	if (ret)
		goto stop;
	dev_info(&pdev->dev,
		 "native AP backend chip=0x%02x cv=%u factory=%c adie=0x%04x; %s WCPU ready; packets=%u bytes=%u\n",
		 radio->chip, radio->identity.cv, radio->identity.factory,
		 radio->identity.adie, name, radio->transport.packets,
		 radio->transport.transferred_bytes);
	return 0;
release:
	release_firmware(fw);
stop:
	rtl_ax_stop(radio);
	return dev_err_probe(&pdev->dev, ret, "AP identity or firmware startup failed\n");
}

static void rtl_ax_remove(struct pci_dev *pdev)
{
	rtl_ax_stop(pci_get_drvdata(pdev));
}

static void rtl_ax_shutdown(struct pci_dev *pdev)
{
	rtl_ax_stop(pci_get_drvdata(pdev));
}

static const struct pci_device_id rtl_ax_ids[] = {
	{ PCI_DEVICE(PCI_VENDOR_ID_REALTEK, 0xc852) },
	{ PCI_DEVICE(PCI_VENDOR_ID_REALTEK, 0x0192) },
	{ }
};
MODULE_DEVICE_TABLE(pci, rtl_ax_ids);

static struct pci_driver rtl_ax_driver = {
	.name = "rtl_ax_ap",
	.id_table = rtl_ax_ids,
	.probe = rtl_ax_probe,
	.remove = rtl_ax_remove,
	.shutdown = rtl_ax_shutdown,
	.dev_groups = rtl_ax_groups,
};
module_pci_driver(rtl_ax_driver);
MODULE_DESCRIPTION("Native TYFZA1 RTL8852C/8192XB AP endpoint backend");
MODULE_LICENSE("GPL");
MODULE_FIRMWARE("realtek/tyfza1/rtl8852c-u1-ap.bin");
MODULE_FIRMWARE("realtek/tyfza1/rtl8852c-u2-ap.bin");
MODULE_FIRMWARE("realtek/tyfza1/rtl8192xb-u1-ap.bin");
MODULE_FIRMWARE("realtek/tyfza1/rtl8192xb-u2-ap.bin");
