// SPDX-License-Identifier: GPL-2.0-only
/* Native TYFZA1 radio endpoint backend. No vendor host module is loaded. */
#include <linux/delay.h>
#include <linux/completion.h>
#include <linux/dma-mapping.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pci.h>
#include <linux/spinlock.h>
#include "fw.h"
#include "power.h"
#include "download.h"
#include "transport.h"
#include "mac-runtime.h"
#include "phy.h"
#include "receive.h"
#include "rx.h"
#include "power-tables.h"

/* Original netdev-open policy in os_intfs.c: MU beamforming, OFDMA or
 * force_fw_tx selects firmware scheduling. Preserve all three inputs. */
static bool txbf_mu;
static bool ofdma;
static bool force_fw_tx;
module_param(txbf_mu, bool, 0444);
module_param(ofdma, bool, 0444);
module_param(force_fw_tx, bool, 0444);
MODULE_PARM_DESC(txbf_mu, "Original AP MU beamforming scheduling input");
MODULE_PARM_DESC(ofdma, "Original AP OFDMA scheduling input");
MODULE_PARM_DESC(force_fw_tx, "Original AP force firmware TX input");

struct rtl_ax_ap {
	struct pci_dev *pdev;
	void __iomem *bar;
	struct rtl_ax_io io;
	struct rtl_ax_calibration calibration;
	struct rtl_ax_identity identity;
	struct rtl_ax_ap_image firmware;
	struct rtl_ax_fw_transport transport;
	struct rtl_ax_phy_inputs phy;
	struct rtl_ax_power_inputs power_inputs;
	struct rtl_ax_rx *rx;
	struct mutex command_mutex;
	spinlock_t command_lock;
	struct completion command_done;
	unsigned char *h2c_ring, *h2c_packet;
	dma_addr_t h2c_ring_dma, h2c_packet_dma;
	unsigned int h2c_producer;
	unsigned int h2c_available[RTL_AX_H2C_CLASSES];
	unsigned char command_sequence, pending_sequence, pending_category, pending_class, pending_function;
	unsigned char command_result;
	bool command_pending, command_received;
	u64 receive_acks, done_acks, unmatched_acks;
	unsigned char chip;
	unsigned char secure;
	bool powered;
	bool download_prepared;
	bool firmware_ready;
	bool mac_memory_ready;
	bool dmac_engines_ready, firmware_tx;
	bool cmac_registers_ready;
	bool trx_registers_ready;
	bool phy_inputs_ready;
	bool features_ready;
	bool bb_base_ready;
	bool radio_base_ready;
	bool fem_ready;
	u8 power_stage;
	u32 edpk_gain[2], edpk_rxbb[2], edpk_reference;
	unsigned int radio_passes[RTL_AX_PHY_STAGES];
	unsigned int radio_pages[RTL_AX_PHY_STAGES][2], radio_bytes[RTL_AX_PHY_STAGES][2];
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

static int rtl_ax_poll16(void *ctx, unsigned int reg, unsigned short mask,
	unsigned short expected, unsigned int delay_us, unsigned int timeout_us,
	unsigned short *last)
{
	struct rtl_ax_ap *radio = ctx;

	return readw_poll_timeout(radio->bar + reg, *last,
				 (*last & mask) == expected, delay_us, timeout_us);
}

static int rtl_ax_h2c_publish(struct rtl_ax_ap *radio, unsigned int class, unsigned int length)
{
	unsigned int next, last;
	unsigned int slot = class * RTL_AX_H2C_CLASS_BUFFERS + radio->h2c_available[class];
	unsigned int offset = slot * RTL_AX_H2C_BUFFER_BYTES;
	int ret;

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
	 * packet remains in its CMD/DATA/LONG_DATA work ring through eight more
	 * completions of that class. The ninth slot supplies the next packet. */
	radio->h2c_available[class] =
		(radio->h2c_available[class] + 1) % RTL_AX_H2C_CLASS_BUFFERS;
	return 0;
}

static int rtl_ax_h2c_send(void *ctx, const unsigned char *data,
			   unsigned int bytes, int section)
{
	struct rtl_ax_ap *radio = ctx;
	unsigned int class = section ? RTL_AX_H2C_LONG_DATA : RTL_AX_H2C_DATA;
	unsigned int slot = class * RTL_AX_H2C_CLASS_BUFFERS + radio->h2c_available[class];
	int length;

	length = rtl_ax_fw_packet(radio->h2c_packet + slot * RTL_AX_H2C_BUFFER_BYTES,
				RTL_AX_H2C_BUFFER_BYTES, data, bytes, 0, section);
	if (length < 0)
		return length;
	return rtl_ax_h2c_publish(radio, class, length);
}

static int rtl_ax_receive(void *ctx, const struct rtl_ax_rx_packet *packet)
{
	struct rtl_ax_ap *radio = ctx;
	struct rtl_ax_c2h message;
	unsigned int ack, sequence;
	unsigned long flags;
	int ret;

	if (packet->type != 10)
		return -EOPNOTSUPP;
	ret = rtl_ax_c2h_parse(packet->payload, packet->bytes, &message);
	if (ret)
		return ret;
	/* Original MAC/FW_INFO rec-ACK and done-ACK. The outer C2H sequence
	 * belongs to C2H;the command's H2C sequence is in its ACK payload. */
	if (message.category != 1 || message.class != 0 || message.function > 1)
		return -EOPNOTSUPP;
	if (message.bytes < 4)
		return -EINVAL;
	ack = rtl_ax_ap_read32(message.payload);
	sequence = message.function ? ack >> 24 : (ack >> 16) & 255;
	spin_lock_irqsave(&radio->command_lock, flags);
	if (!radio->command_pending || (ack & 3) != radio->pending_category ||
	    ((ack >> 2) & 63) != radio->pending_class ||
	    ((ack >> 8) & 255) != radio->pending_function || sequence != radio->pending_sequence) {
		radio->unmatched_acks++;
		spin_unlock_irqrestore(&radio->command_lock, flags);
		return -EOPNOTSUPP;
	}
	if (!message.function) {
		radio->command_received = true;
		radio->receive_acks++;
	} else {
		radio->command_result = (ack >> 16) & 255;
		radio->command_pending = false;
		radio->done_acks++;
		complete(&radio->command_done);
	}
	spin_unlock_irqrestore(&radio->command_lock, flags);
	return 0;
}

static int rtl_ax_command(struct rtl_ax_ap *radio, unsigned int buffer_class,
	unsigned int category, unsigned int class, unsigned int function,
	const void *data, unsigned int bytes, bool done_ack)
{
	unsigned int slot;
	unsigned long flags;
	int length, ret;

	mutex_lock(&radio->command_mutex);
	slot = buffer_class * RTL_AX_H2C_CLASS_BUFFERS + radio->h2c_available[buffer_class];
	length = rtl_ax_command_packet(radio->h2c_packet + slot * RTL_AX_H2C_BUFFER_BYTES,
				       RTL_AX_H2C_BUFFER_BYTES, data, bytes, category, class,
				       function, radio->command_sequence, 0, done_ack);
	if (length < 0) {
		ret = length;
		goto unlock;
	}
	if (done_ack) {
		reinit_completion(&radio->command_done);
		spin_lock_irqsave(&radio->command_lock, flags);
		radio->pending_category = category;
		radio->pending_class = class;
		radio->pending_function = function;
		radio->pending_sequence = radio->command_sequence;
		radio->command_pending = true;
		radio->command_received = false;
		spin_unlock_irqrestore(&radio->command_lock, flags);
	}
	ret = rtl_ax_h2c_publish(radio, buffer_class, length);
	if (!ret)
		radio->command_sequence++;
	if (!ret && done_ack) {
		/* Native startup transaction deadline. Original requests done-ACK;
		 * TXBD consumption alone is never treated as command execution. */
		if (!wait_for_completion_timeout(&radio->command_done, msecs_to_jiffies(1000)))
			ret = -ETIMEDOUT;
		else if (radio->command_result)
			ret = -EREMOTEIO;
	}
	if (done_ack) {
		spin_lock_irqsave(&radio->command_lock, flags);
		radio->command_pending = false;
		spin_unlock_irqrestore(&radio->command_lock, flags);
	}
unlock:
	mutex_unlock(&radio->command_mutex);
	return ret;
}

static int rtl_ax_mac_features(struct rtl_ax_ap *radio)
{
	__le32 entries = cpu_to_le32(8);
	int ret;

	/* mac_feat_init→mac_bacam_init_upd(8,0,0): MAC/class12/function2,
	 * CMD buffer,4-byte payload and done-ACK requested. */
	radio->rx = rtl_ax_rx_prepare(radio->pdev, &radio->io, rtl_ax_receive, radio);
	if (IS_ERR(radio->rx)) {
		ret = PTR_ERR(radio->rx);
		radio->rx = NULL;
		return ret;
	}
	pci_set_master(radio->pdev);
	rtl_ax_h2c_start(&radio->io);
	rtl_ax_rx_open(radio->rx);
	ret = rtl_ax_command(radio, RTL_AX_H2C_CMD, 1, 12, 2, &entries, sizeof(entries), true);
	rtl_ax_rx_close(radio->rx);
	rtl_ax_hci_pause(&radio->io);
	pci_clear_master(radio->pdev);
	if (ret)
		dev_err(&radio->pdev->dev,
			"BA CAM initialization failed: %d, firmware return=%u done_acks=%llu\n",
			ret, radio->command_result, radio->done_acks);
	radio->features_ready = !ret;
	return ret;
}

static int rtl_ax_wait_firmware(void *ctx, unsigned int *control)
{
	struct rtl_ax_ap *radio = ctx;

	/* Linux elapsed-time polling retains the original 400ms bound even
	 * when the scheduler extends a requested polling sleep. */
	return readb_poll_timeout(radio->bar + RTL_AX_WCPU_CONTROL, *control,
				 rtl_ax_fw_result(*control) != -EINPROGRESS, 10, 400000);
}

static int rtl_ax_bb_base_start(struct rtl_ax_ap *radio)
{
	int ret;

	/* hal_start_C/XB: MAC init→BB/RF off/on→rtw_hal_init_bb_reg. */
	rtl_ax_bb_rf_disable(&radio->io);
	ret = rtl_ax_bb_rf_enable(&radio->io, radio->chip);
	if (ret)
		return ret;
	rtl_ax_phy_bb_apply(&radio->io, &radio->phy, RTL_AX_PHY_BASE);
	rtl_ax_phy_bb_offset(&radio->io, &radio->phy);
	rtl_ax_phy_bb_reset(&radio->io, radio->chip);
	radio->bb_base_ready = true;
	return 0;
}

struct rtl_ax_radio_stage {
	struct rtl_ax_ap *radio;
	enum rtl_ax_phy_stage stage;
};

static int rtl_ax_send_radio(void *ctx, unsigned int class, unsigned int page,
			     const __le32 *words, unsigned int bytes)
{
	struct rtl_ax_radio_stage *transaction = ctx;
	struct rtl_ax_ap *radio = transaction->radio;
	enum rtl_ax_phy_stage stage = transaction->stage;
	int ret;

	/* Actual halrf_fill_h2c_cmd→mac_outsrc_h2c_common: OUTSRC category2,
	 * LONG_DATA, radio class8/9, page function, no ACK requested. */
	ret = rtl_ax_command(radio, RTL_AX_H2C_LONG_DATA, 2, class, page, words, bytes, false);
	if (!ret) {
		radio->radio_pages[stage][class - 8]++;
		radio->radio_bytes[stage][class - 8] += bytes;
	}
	return ret;
}

static int rtl_ax_radio_stage_start(struct rtl_ax_ap *radio, enum rtl_ax_phy_stage stage)
{
	struct rtl_ax_radio_stage transaction = { radio, stage };
	const struct rtl_ax_phy_commands commands = {
		.ctx = &transaction, .send_radio = rtl_ax_send_radio,
	};
	int ret;

	/* Both original Radio passes use identical register/page execution. */
	pci_set_master(radio->pdev);
	rtl_ax_h2c_start(&radio->io);
	rtl_ax_rx_open(radio->rx);
	ret = rtl_ax_phy_radio_apply(&radio->io, &radio->phy, stage, &commands);
	rtl_ax_rx_close(radio->rx);
	rtl_ax_hci_pause(&radio->io);
	pci_clear_master(radio->pdev);
	if (ret)
		dev_err(&radio->pdev->dev,
			"radio stage%u failed: %d path=%u address=0x%x writes=%u,%u pages=%u,%u\n",
			stage, ret, radio->phy.radio_last_path, radio->phy.radio_last_address,
			radio->phy.radio_writes[stage][0], radio->phy.radio_writes[stage][1],
			radio->radio_pages[stage][0], radio->radio_pages[stage][1]);
	if (!ret)
		radio->radio_passes[stage]++;
	if (stage == RTL_AX_PHY_BASE)
		radio->radio_base_ready = !ret;
	return ret;
}

static void rtl_ax_power_stage_select(struct rtl_ax_ap *radio, enum rtl_ax_phy_stage stage)
{
	unsigned int path;

	/* RF parameter configuration selects ByRate/limit/RU/thermal host
	 * objects after its second Radio pass. XTAL track is empty for C/XB. */
	radio->power_stage = stage;
	if (radio->chip != RTL_AX_8192XB)
		return;
	/* Actual RFE54 XB EDPK configuration@357060 captures BB fields;
	 * it does not write hardware. RFE54 exceeds the original >50 gate. */
	for (path = 0; path < 2; path++) {
		radio->edpk_gain[path] = (rtl_ax_read32(radio, 0x181ac + path * 0x100) >> 18) & 63;
		radio->edpk_rxbb[path] = rtl_ax_read32(radio, 0x181f0 + path * 0x100) & 511;
	}
	radio->edpk_reference = 0xa00;
}

static int rtl_ax_phy_configuration_start(struct rtl_ax_ap *radio)
{
	int ret;

	/* hal_start→init_bb→init_rf: firstRadio→power inputs→secondRadio→
	 * power object configuration;then the complete RFE54 FEM re-init. */
	ret = rtl_ax_bb_base_start(radio);
	if (ret)
		return ret;
	ret = rtl_ax_radio_stage_start(radio, RTL_AX_PHY_BASE);
	if (ret)
		return ret;
	ret = rtl_ax_power_inputs_load(&radio->pdev->dev, radio->chip, &radio->power_inputs);
	if (ret)
		return ret;
	ret = rtl_ax_radio_stage_start(radio, RTL_AX_PHY_BASE);
	if (ret)
		return ret;
	rtl_ax_power_stage_select(radio, RTL_AX_PHY_BASE);
	rtl_ax_phy_bb_apply(&radio->io, &radio->phy, RTL_AX_PHY_FEM);
	rtl_ax_phy_bb_offset(&radio->io, &radio->phy);
	rtl_ax_phy_bb_reset(&radio->io, radio->chip);
	ret = rtl_ax_radio_stage_start(radio, RTL_AX_PHY_FEM);
	if (ret)
		return ret;
	/* FEM files have already been decoded into their own power objects. */
	ret = rtl_ax_radio_stage_start(radio, RTL_AX_PHY_FEM);
	if (ret)
		return ret;
	rtl_ax_power_stage_select(radio, RTL_AX_PHY_FEM);
	radio->fem_ready = true;
	return ret;
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
	ret = rtl_ax_mac_memory_start(&radio->io, radio->chip, &radio->secure);
	if (ret)
		return ret;
	radio->mac_memory_ready = true;
	radio->firmware_tx = txbf_mu || ofdma || force_fw_tx;
	ret = rtl_ax_mac_dmac_start(&radio->io, radio->firmware_tx);
	if (ret)
		return ret;
	radio->dmac_engines_ready = true;
	ret = rtl_ax_mac_cmac_start(&radio->io, radio->chip, radio->firmware_tx);
	if (ret)
		return ret;
	radio->cmac_registers_ready = true;
	rtl_ax_mac_trx_registers(&radio->io);
	radio->trx_registers_ready = true;
	ret = rtl_ax_phy_load(&radio->pdev->dev, radio->chip, &radio->phy);
	if (ret)
		return ret;
	radio->phy_inputs_ready = true;
	ret = rtl_ax_mac_features(radio);
	if (ret)
		return ret;
	return rtl_ax_phy_configuration_start(radio);
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
		"transport=channel12 packets=%u bytes=%u control=0x%02x "
		"mac_memory=%s secure=%u dmac_engines=%s firmware_tx=%u "
		"cmac_registers=%s trx_registers=%s phy_inputs=%s "
		"features=%s done_acks=%llu bb_base=%s radio_base=%s "
		"phy_configuration=%s radio_passes=%u,%u "
		"bb_rf=pending roles=pending mac80211=pending\n",
		radio->chip, radio->identity.hardware_cv, radio->identity.cv,
		radio->identity.factory, radio->identity.adie, radio->powered ? "on" : "off",
		radio->calibration.tag, radio->calibration.process,
		image->version[0], image->version[1], image->version[2], image->version[3],
		image->cut, image->section_count, radio->firmware_ready ? "ready" : "off",
		radio->download_prepared ? "ready" : "off", radio->transport.packets,
		radio->transport.transferred_bytes, radio->transport.last_control,
		radio->mac_memory_ready ? "SCC" : "off", radio->secure,
		radio->dmac_engines_ready ? "ready" : "off", radio->firmware_tx,
		radio->cmac_registers_ready ? "ready" : "off",
		radio->trx_registers_ready ? "ready" : "off",
		radio->phy_inputs_ready ? "RFE54-base-and-FEM" : "off",
		radio->features_ready ? "BA-CAM8-acknowledged" : "off", radio->done_acks,
		radio->bb_base_ready ? "applied" : "off", radio->radio_base_ready ? "applied" : "off",
		radio->fem_ready ? "base-and-FEM" : "pending",
		radio->radio_passes[0], radio->radio_passes[1]);
}
static DEVICE_ATTR_RO(radio_state);

static ssize_t radio_bb_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);

	return sysfs_emit(buffer,
		"base_rows=%u gain_rows=%u gain_offset=%d,%d gain_offset_tb=%d,%d "
		"sys_func=0x%02x rf_ctrl=0x%08x bb0704=0x%08x "
		"bb4df0=0x%08x bb6df0=0x%08x bb4e0c=0x%08x bb6e0c=0x%08x\n",
		radio->phy.table[RTL_AX_PHY_BASE][RTL_AX_PHY_REG].count,
		radio->phy.table[RTL_AX_PHY_BASE][RTL_AX_PHY_GAIN].count,
		radio->phy.gain_offset[0], radio->phy.gain_offset[1],
		radio->phy.gain_offset_tb[0], radio->phy.gain_offset_tb[1],
		rtl_ax_read8(radio, 0x0002), rtl_ax_read32(radio, 0x02f0),
		rtl_ax_read32(radio, 0x10704), rtl_ax_read32(radio, 0x14df0),
		rtl_ax_read32(radio, 0x16df0), rtl_ax_read32(radio, 0x14e0c),
		rtl_ax_read32(radio, 0x16e0c));
}
static DEVICE_ATTR_RO(radio_bb);

static ssize_t radio_rf_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);

	return sysfs_emit(buffer,
		"base_writes=%u,%u fem_writes=%u,%u firmware_pages_base=%u,%u "
		"firmware_pages_fem=%u,%u firmware_bytes_base=%u,%u firmware_bytes_fem=%u,%u "
		"last_path=%u last_address=0x%x sipi=0x%08x "
		"digital00=0x%05x,0x%05x digital18=0x%05x,0x%05x "
		"requested00=0x%05x,0x%05x immediate00=0x%05x,0x%05x "
		"requested18=0x%05x,0x%05x immediate18=0x%05x,0x%05x\n",
		radio->phy.radio_writes[0][0], radio->phy.radio_writes[0][1],
		radio->phy.radio_writes[1][0], radio->phy.radio_writes[1][1],
		radio->radio_pages[0][0], radio->radio_pages[0][1],
		radio->radio_pages[1][0], radio->radio_pages[1][1],
		radio->radio_bytes[0][0], radio->radio_bytes[0][1],
		radio->radio_bytes[1][0], radio->radio_bytes[1][1],
		radio->phy.radio_last_path, radio->phy.radio_last_address,
		rtl_ax_read32(radio, 0x1174c), rtl_ax_read32(radio, 0x1e000) & 0xfffff,
		rtl_ax_read32(radio, 0x1f000) & 0xfffff, rtl_ax_read32(radio, 0x1e060) & 0xfffff,
		rtl_ax_read32(radio, 0x1f060) & 0xfffff,
		radio->phy.digital_requested[0][0], radio->phy.digital_requested[1][0],
		radio->phy.digital_immediate[0][0], radio->phy.digital_immediate[1][0],
		radio->phy.digital_requested[0][1], radio->phy.digital_requested[1][1],
		radio->phy.digital_immediate[0][1], radio->phy.digital_immediate[1][1]);
}
static DEVICE_ATTR_RO(radio_rf);

static ssize_t radio_rf_bank_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	unsigned int path, reg;
	ssize_t length = 0;

	/* Original read_rf_reg_d uses these same direct windows. Read-only
	 * register evidence for comparing the actual board with its RF table. */
	for (path = 0; path < 2; path++) {
		length += sysfs_emit_at(buffer, length, "path=%u\n", path);
		for (reg = 0; reg < 256; reg++) {
			if (!(reg & 15))
				length += sysfs_emit_at(buffer, length, "%02x:", reg);
			length += sysfs_emit_at(buffer, length, " %05x%s",
				rtl_ax_read32(radio, (path ? 0x1f000 : 0x1e000) + reg * 4) & 0xfffff,
				(reg & 15) == 15 ? "\n" : "");
		}
	}
	return length;
}
static DEVICE_ATTR_RO(radio_rf_bank);

static ssize_t radio_rf_analog_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	const unsigned int regs[] = {0x00, 0x18, 0x30};
	unsigned int path, i;
	ssize_t length = 0;

	/* Serializes the original SIPI read request/result transaction. */
	mutex_lock(&radio->command_mutex);
	for (path = 0; path < 2; path++)
		for (i = 0; i < ARRAY_SIZE(regs); i++) {
			u32 value = 0;
			int ret = rtl_ax_phy_rf_read(&radio->io, path, regs[i], &value);

			length += sysfs_emit_at(buffer, length,
				"path=%u address=%02x result=%d value=%05x\n", path, regs[i], ret, value);
		}
	mutex_unlock(&radio->command_mutex);
	return length;
}
static DEVICE_ATTR_RO(radio_rf_analog);

static ssize_t radio_power_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_power_stage *base = &radio->power_inputs.stage[RTL_AX_PHY_BASE];
	struct rtl_ax_power_stage *fem = &radio->power_inputs.stage[RTL_AX_PHY_FEM];

	return sysfs_emit(buffer,
		"inputs=%s active_stage=%u units=quarter-dBm rate_rows=%u,%u track_rows=%u,%u "
		"base_cck1=%d base_ofdm6=%d,%d,%d base_he1_mcs11=%d,%d,%d "
		"fem_cck1=%d fem_ofdm6=%d,%d,%d fem_he1_mcs11=%d,%d,%d "
		"base_track2g_A_29=%d,%d fem_track2g_A_29=%d,%d "
		"limit=%s RU=%s edpk_gain=%u,%u edpk_rxbb=%u,%u edpk_ref=%u "
		"hardware_power=pending calibration=pending\n",
		radio->power_inputs.ready ? "decoded" : "off",
		radio->power_stage,
		base->rate_rows, fem->rate_rows, base->track_rows, fem->track_rows,
		base->rate[0][0], base->rate[0][4], base->rate[1][4], base->rate[2][4],
		base->rate[0][95], base->rate[1][95], base->rate[2][95],
		fem->rate[0][0], fem->rate[0][4], fem->rate[1][4], fem->rate[2][4],
		fem->rate[0][95], fem->rate[1][95], fem->rate[2][95],
		base->track_2g[0][0][29], base->track_2g[0][1][29],
		fem->track_2g[0][0][29], fem->track_2g[0][1][29],
		radio->power_inputs.ready ? "decoded" : "off",
		radio->power_inputs.ready ? "decoded" : "off", radio->edpk_gain[0], radio->edpk_gain[1],
		radio->edpk_rxbb[0], radio->edpk_rxbb[1], radio->edpk_reference);
}
static DEVICE_ATTR_RO(radio_power);

static ssize_t radio_limits_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	unsigned int stage, ru;
	ssize_t length = 0;

	for (stage = 0; stage < RTL_AX_PHY_STAGES; stage++) {
		struct rtl_ax_limits *limits = &radio->power_inputs.limits[stage];

		length += sysfs_emit_at(buffer, length,
			"stage=%u files=%u ordinary_records=%u ru_records=%u regulations=%u exceptions=%u\n",
			stage, limits->files, limits->ordinary_count, limits->ru_count,
			limits->regulations, limits->exceptions);
		for (ru = 0; ru < 2; ru++) {
			struct rtl_ax_limit_key key = {
				.band = radio->chip == RTL_AX_8852C,
				.channel = radio->chip == RTL_AX_8852C ? 36 : 1,
				.rate = ru ? 4 : radio->chip == RTL_AX_8852C ? 1 : 0,
				.ru = ru,
			};
			s8 power = 0;
			u8 shape = 0;
			int ret = rtl_ax_limit_get(limits, &key, "CN", &power, &shape);

			length += sysfs_emit_at(buffer, length,
				"stage=%u query=CN band=%u channel=%u bw_index=0 ntx=1 rate=%u ru=%u "
				"result=%d quarter_dBm=%d shape=%u\n", stage, key.band,
				key.channel, key.rate, ru, ret, power, shape);
		}
	}
	return length;
}
static DEVICE_ATTR_RO(radio_limits);

static ssize_t radio_efuse_read(struct file *file, struct kobject *kobject,
	const struct bin_attribute *attr, char *buffer, loff_t offset, size_t count)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(kobj_to_dev(kobject));
	size_t i;
	int ret = 0;

	/* Actual DDV1536-byte Wi-Fi bank,using the same stock physical read
	 * sequence as identity. Preserve factory trim for native RF calibration. */
	mutex_lock(&radio->command_mutex);
	for (i = 0; i < count; i++) {
		ret = rtl_ax_efuse_read(&radio->io, offset + i, (unsigned char *)buffer + i);
		if (ret)
			break;
	}
	mutex_unlock(&radio->command_mutex);
	return i ? i : ret;
}
static const struct bin_attribute bin_attr_radio_efuse = {
	.attr = { .name = "radio_efuse", .mode = 0400 },
	.size = 1536, .read = radio_efuse_read,
};
static const struct bin_attribute *rtl_ax_bin_attrs[] = {
	&bin_attr_radio_efuse, NULL,
};

static ssize_t radio_rx_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);

	return rtl_ax_rx_status(radio->rx, buffer, PAGE_SIZE);
}
static DEVICE_ATTR_RO(radio_rx);
static struct attribute *rtl_ax_attrs[] = {
	&dev_attr_radio_state.attr, &dev_attr_radio_rx.attr, &dev_attr_radio_bb.attr,
	&dev_attr_radio_rf.attr, &dev_attr_radio_rf_bank.attr, &dev_attr_radio_rf_analog.attr,
	&dev_attr_radio_power.attr, &dev_attr_radio_limits.attr, NULL,
};
static const struct attribute_group rtl_ax_group = {
	.attrs = rtl_ax_attrs, .bin_attrs = rtl_ax_bin_attrs,
};
static const struct attribute_group *rtl_ax_groups[] = { &rtl_ax_group, NULL };

static void rtl_ax_stop(struct rtl_ax_ap *radio)
{
	int ret;

	if (!radio->powered)
		return;
	pci_clear_master(radio->pdev);
	if (radio->rx)
		rtl_ax_rx_close(radio->rx);
	if (radio->download_prepared) {
		rtl_ax_download_stop(&radio->io);
		rtl_ax_wcpu_stop(&radio->io);
		radio->firmware_ready = false;
		radio->mac_memory_ready = false;
		radio->dmac_engines_ready = false;
		radio->cmac_registers_ready = false;
		radio->trx_registers_ready = false;
		radio->phy_inputs_ready = false;
		radio->features_ready = false;
		radio->bb_base_ready = false;
		radio->radio_base_ready = false;
		radio->fem_ready = false;
		radio->power_inputs.ready = false;
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
	rtl_ax_phy_release(&radio->phy);
	rtl_ax_power_inputs_release(&radio->power_inputs);
	if (radio->rx) {
		rtl_ax_rx_free(radio->rx);
		radio->rx = NULL;
	}
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
	mutex_init(&radio->command_mutex);
	spin_lock_init(&radio->command_lock);
	init_completion(&radio->command_done);
	/* The original driver chooses the first memory BAR, not a fixed index. */
	bar = pci_select_bars(pdev, IORESOURCE_MEM);
	if (!bar)
		return -ENODEV;
	bar = __ffs(bar);
	if (pci_resource_len(pdev, bar) < 0xda4c)
		return -ENODEV;
	radio->bar = pcim_iomap_region(pdev, bar, "rtl_ax_ap");
	if (IS_ERR(radio->bar))
		return PTR_ERR(radio->bar);
	radio->io = (struct rtl_ax_io) {
		.ctx = radio,
		.read8 = rtl_ax_read8, .read16 = rtl_ax_read16, .read32 = rtl_ax_read32,
		.write8 = rtl_ax_write8, .write16 = rtl_ax_write16, .write32 = rtl_ax_write32,
		.delay_us = rtl_ax_delay_us, .poll16 = rtl_ax_poll16, .poll32 = rtl_ax_poll32,
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
