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
#include <net/mac80211.h>
#include "fw.h"
#include "power.h"
#include "download.h"
#include "transport.h"
#include "mac-runtime.h"
#include "phy.h"
#include "receive.h"
#include "rx.h"
#include "power-tables.h"
#include "bb-dm.h"
#include "dfs.h"
#include "dig.h"
#include "cfo.h"
#include "environment.h"
#include "rx-pipeline.h"
#include "channel.h"
#include "xb-channel.h"
#include "rf-start.h"
#include "dack.h"
#include "rxdck.h"
#include "c-rxdck.h"
#include "trim.h"
#include "ref-power.h"
#include "tssi-de.h"

/* Original netdev-open policy in os_intfs.c: MU beamforming, OFDMA or
 * force_fw_tx selects firmware scheduling. Preserve all three inputs. */
static bool txbf_mu;
static bool ofdma;
static bool force_fw_tx;
static bool independent_xtal;
static bool antenna_diversity;
static unsigned int edcca_mode;
static unsigned int hw_rts_time, hw_rts_length;
static unsigned int dfs_region;
module_param(txbf_mu, bool, 0444);
module_param(ofdma, bool, 0444);
module_param(force_fw_tx, bool, 0444);
module_param(independent_xtal, bool, 0444);
module_param(antenna_diversity, bool, 0444);
module_param(edcca_mode, uint, 0444);
module_param(hw_rts_time, uint, 0444);
module_param(hw_rts_length, uint, 0444);
module_param(dfs_region, uint, 0444);
MODULE_PARM_DESC(txbf_mu, "Original AP MU beamforming scheduling input");
MODULE_PARM_DESC(ofdma, "Original AP OFDMA scheduling input");
MODULE_PARM_DESC(force_fw_tx, "Original AP force firmware TX input");
MODULE_PARM_DESC(independent_xtal, "Original dev_cap.is_indiv_xtal input");
MODULE_PARM_DESC(antenna_diversity, "Original RFE54 antenna diversity input");
MODULE_PARM_DESC(edcca_mode, "Original EDCCA operating mode input");
MODULE_PARM_DESC(hw_rts_time, "Original hardware RTS time threshold in microseconds");
MODULE_PARM_DESC(hw_rts_length, "Original hardware RTS length threshold in bytes");
MODULE_PARM_DESC(dfs_region, "Original DFS region0=unset,1=FCC,2=MKK,3=ETSI,4=KCC");

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
	struct rtl_ax_bb_dm bb_dm;
	struct rtl_ax_board_cal flash_calibration;
	struct rtl_ax_dfs dfs;
	struct rtl_ax_dig dig;
	struct rtl_ax_cfo cfo;
	struct rtl_ax_environment environment;
	struct rtl_ax_channel channel;
	struct rtl_ax_rf_start rf_start;
	struct rtl_ax_dack dack;
	struct rtl_ax_rxdck rxdck;
	struct rtl_ax_trim trim;
	struct rtl_ax_ref_power ref_power;
	struct rtl_ax_tssi_de tssi_de;
	struct rtl_ax_rx *rx;
	struct ieee80211_hw *hw;
	struct ieee80211_supported_band band;
	struct ieee80211_channel channels[13];
	struct ieee80211_rate rates[12];
	bool monitor_started;
	u64 mac80211_packets, tx_unimplemented;
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

static int rtl_ax_monitor_receive(struct rtl_ax_ap *radio,
	const struct rtl_ax_rx_packet *packet);

static int rtl_ax_receive(void *ctx, const struct rtl_ax_rx_packet *packet)
{
	struct rtl_ax_ap *radio = ctx;
	struct rtl_ax_c2h message;
	unsigned int ack, sequence;
	unsigned long flags;
	int ret;

	if (packet->type == 0 && READ_ONCE(radio->monitor_started))
		return rtl_ax_monitor_receive(radio, packet);
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
	rtl_ax_hal_start_receive(&radio->io, hw_rts_time, hw_rts_length);
	ret = rtl_ax_phy_configuration_start(radio);
	if (ret)
		return ret;
	ret = rtl_ax_bb_dm_ic_start(&radio->io, radio->chip, &radio->bb_dm);
	if (ret)
		return ret;
	rtl_ax_bb_dm_gpio_start(&radio->io, radio->chip, &radio->bb_dm);
	ret = rtl_ax_board_cal_load(&radio->pdev->dev, radio->chip, &radio->flash_calibration);
	if (ret)
		return ret;
	ret = rtl_ax_bb_dm_gain_start(&radio->io, radio->chip,
				    &radio->flash_calibration, &radio->bb_dm);
	if (ret)
		return ret;
	rtl_ax_bb_dm_physts_start(&radio->io, radio->chip, independent_xtal, antenna_diversity, &radio->bb_dm);
	rtl_ax_bb_dm_reports_start(&radio->io, &radio->bb_dm);
	rtl_ax_bb_dm_statistics_start(&radio->io, &radio->bb_dm);
	rtl_ax_bb_dm_signal_start(&radio->io, radio->chip, edcca_mode, &radio->bb_dm);
	rtl_ax_dfs_initialize(&radio->dfs, dfs_region);
	rtl_ax_dig_initialize(&radio->io, &radio->dig);
	rtl_ax_cfo_initialize(&radio->io, radio->chip, radio->flash_calibration.xtal, &radio->cfo);
	rtl_ax_environment_initialize(&radio->io, radio->chip, &radio->environment);
	ret = rtl_ax_rf_crystal_start(&radio->io, radio->chip, independent_xtal,
				    radio->flash_calibration.xtal, &radio->rf_start);
	if (ret)
		return ret;
	rtl_ax_rf_gpio_start(&radio->io, radio->chip, &radio->rf_start);
	ret = rtl_ax_rf_nctl_start(&radio->pdev->dev, &radio->io, radio->chip,
				 radio->identity.hardware_cv, &radio->rf_start);
	if (ret)
		return ret;
	rtl_ax_ref_power_start(&radio->io, &radio->ref_power);
	ret = rtl_ax_rf_rck_start(&radio->io, &radio->rf_start);
	if (ret)
		return ret;
	ret = rtl_ax_dack_adc_start(&radio->io, radio->chip, &radio->dack);
	if (ret)
		return ret;
	ret = rtl_ax_dack_dac_start(&radio->io, radio->chip, &radio->dack);
	if (ret)
		return ret;
	ret = radio->chip == RTL_AX_8192XB ? rtl_ax_xb_rxdck_start(&radio->io, &radio->rxdck) :
		rtl_ax_c_rxdck_start(&radio->io, &radio->rxdck);
	if (ret)
		return ret;
	ret = rtl_ax_trim_start(&radio->io, radio->chip, &radio->trim);
	if (ret)
		return ret;
	/* Native RXQ/RPQ and IRQ are already prepared. Resume receive after
	 * the implemented PHY startup so ordinary 802.11 frames reach the
	 * native dispatcher, using the same path verified on this board. */
	pci_set_master(radio->pdev);
	rtl_ax_h2c_start(&radio->io);
	rtl_ax_rx_open(radio->rx);
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
		"firmware_state=%s download_memory=%s hci=%s "
		"transport=channel12 packets=%u bytes=%u control=0x%02x "
		"mac_memory=%s secure=%u dmac_engines=%s firmware_tx=%u "
		"cmac_registers=%s trx_registers=%s phy_inputs=%s "
		"features=%s done_acks=%llu bb_base=%s radio_base=%s "
		"phy_configuration=%s radio_passes=%u,%u "
		"bb_dm_ic=%s bb_rf=pending roles=pending mac80211=%s\n",
		radio->chip, radio->identity.hardware_cv, radio->identity.cv,
		radio->identity.factory, radio->identity.adie, radio->powered ? "on" : "off",
		radio->calibration.tag, radio->calibration.process,
		image->version[0], image->version[1], image->version[2], image->version[3],
		image->cut, image->section_count, radio->firmware_ready ? "ready" : "off",
		radio->download_prepared ? "ready" : "off",
		rtl_ax_read32(radio, RTL_AX_HAXI_CONFIG) & (BIT(7) | BIT(15)) ? "active" : "paused",
		radio->transport.packets,
		radio->transport.transferred_bytes, radio->transport.last_control,
		radio->mac_memory_ready ? "SCC" : "off", radio->secure,
		radio->dmac_engines_ready ? "ready" : "off", radio->firmware_tx,
		radio->cmac_registers_ready ? "ready" : "off",
		radio->trx_registers_ready ? "ready" : "off",
		radio->phy_inputs_ready ? "RFE54-base-and-FEM" : "off",
		radio->features_ready ? "BA-CAM8-acknowledged" : "off", radio->done_acks,
		radio->bb_base_ready ? "applied" : "off", radio->radio_base_ready ? "applied" : "off",
		radio->fem_ready ? "base-and-FEM" : "pending",
		radio->radio_passes[0], radio->radio_passes[1],
		radio->bb_dm.ic_ready ? "applied" : "pending", radio->hw ? "monitor" : "pending");
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

static ssize_t radio_bb_dm_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_bb_dm *dm = &radio->bb_dm;

	return sysfs_emit(buffer,
		"ic=%s tdma_period=%u,%u dynamic_csi=%u csi_response=%u "
		"cck_backup=%u,%u,%u factory_trim=%02x adc_trim=%02x "
		"a10=%08x,2a10=%08x t2r0624=%08x t2r0608=%08x "
		"GPIO=%s TRSW=%08x,%08x RFM=%08x,%08x "
		"gain_efuse=%s normal_valid=%u normal_A=%d,%d,%d,%d,%d "
		"normal_B=%d,%d,%d,%d,%d hidden_valid=%u hidden_groups=%u "
		"hidden_2G_CS=%d,%d hidden_2G_CG=%d,%d thermal=%u,%u xtal=%u tracking=pending\n",
		dm->ic_ready ? "applied" : "pending", dm->tdma_cr_period[0], dm->tdma_cr_period[1],
		dm->dynamic_csi_response, dm->csi_response, dm->cck_packet_threshold,
		dm->cck_rssi_offset, dm->cck_sbd_threshold, dm->factory_trim, dm->adc_timing_trim,
		rtl_ax_read32(radio, 0x10a10), rtl_ax_read32(radio, 0x12a10),
		rtl_ax_read32(radio, 0x10624), rtl_ax_read32(radio, 0x10608),
		dm->gpio_ready ? "applied" : "pending",
		rtl_ax_read32(radio, 0x15868), rtl_ax_read32(radio, 0x17868),
		rtl_ax_read32(radio, 0x15894), rtl_ax_read32(radio, 0x17894),
		dm->gain_ready ? "decoded" : "pending", dm->normal_gain_valid,
		dm->normal_gain[0][0], dm->normal_gain[0][1], dm->normal_gain[0][2],
		dm->normal_gain[0][3], dm->normal_gain[0][4], dm->normal_gain[1][0],
		dm->normal_gain[1][1], dm->normal_gain[1][2], dm->normal_gain[1][3], dm->normal_gain[1][4],
		dm->hidden_gain_valid, dm->hidden_bands,
		dm->hidden_cs[0][0], dm->hidden_cs[1][0], dm->hidden_cg[0][0], dm->hidden_cg[1][0],
		radio->flash_calibration.thermal[0], radio->flash_calibration.thermal[1],
		radio->flash_calibration.xtal);
}
static DEVICE_ATTR_RO(radio_bb_dm);

static ssize_t radio_physts_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_bb_dm *dm = &radio->bb_dm;
	unsigned int page;
	ssize_t length;

	length = sysfs_emit(buffer, "configuration=%s BB_CR=client supportability=%llx "
		"independent_xtal=%u antenna_diversity=%u runtime_PPDU_parser=pending\n",
		dm->physts_ready ? "applied" : "pending", dm->supportability, independent_xtal, antenna_diversity);
	for (page = 0; page < 16; page++)
		length += sysfs_emit_at(buffer, length, "page=%u bitmap=%08x readback=%08x\n",
			page, dm->physts_bitmap[page], rtl_ax_bb_physts_get(&radio->io, page));
	return length;
}
static DEVICE_ATTR_RO(radio_physts);

static ssize_t radio_txdiff_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_bb_dm *dm = &radio->bb_dm;
	unsigned int i, nonzero = 0;
	ssize_t length = sysfs_emit(buffer, "reports=%s txdiff=%s macid_power=%s "
		"debug_clock=%08x debug_port=%08x\n",
		dm->reports_ready ? "initialized" : "pending", dm->txdiff_ready ? "applied" : "pending",
		dm->macid_power_ready ? "initialized" : "pending", rtl_ax_read32(radio, 0x120f4),
		rtl_ax_read32(radio, 0x120f8));

	for (i = 0; i < ARRAY_SIZE(rtl_ax_txdiff_table); i++)
		length += sysfs_emit_at(buffer, length, "%04x expected=%08x readback=%08x\n",
		0xd56c + i * 4, rtl_ax_txdiff_table[i], rtl_ax_read32(radio, 0xd56c + i * 4));
	for (i = 0; i < 128; i++)
		nonzero += rtl_ax_read32(radio, 0xd36c + i * 4) != 0;
	length += sysfs_emit_at(buffer, length, "macid_power_words=128 nonzero=%u\n", nonzero);
	return length;
}
static DEVICE_ATTR_RO(radio_txdiff);

static ssize_t radio_phy_stats_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_bb_dm *dm = &radio->bb_dm;
	u32 selection = rtl_ax_read32(radio, 0x10900);

	return sysfs_emit(buffer, "initialization=%s counter_reset=%u rates=%u,%u,%u,%u "
		"selected=%08x OFDM_LSIG=%u HT_MCS=%u VHT_MCS=%u VHT_NSS=%u "
		"HE_MCS=%u HE_NSS=%u frame_type=%u "
		"periodic_collection=pending\n",
		dm->statistics_ready ? "applied" : "pending", dm->counter_reset,
		dm->crc_rate[0], dm->crc_rate[1], dm->crc_rate[2], dm->crc_rate[3], selection,
		selection & 15, (selection >> 4) & 127, (selection >> 11) & 15,
		((selection >> 19) & 3) + 1, (selection >> 15) & 15,
		((selection >> 21) & 3) + 1, (selection >> 23) & 63);
}
static DEVICE_ATTR_RO(radio_phy_stats);

static ssize_t radio_signal_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_bb_dm *dm = &radio->bb_dm;
	u32 psd = rtl_ax_read32(radio, 0x1422c), la = rtl_ax_read32(radio, 0x9660);
	u32 collision = rtl_ax_read32(radio, 0x10c70);

	return sysfs_emit(buffer, "LA=%s buffer=%u samples=%u trigger_us=%u cfg=%08x "
		"PSD=%s fft=%u IQ=%u average=%u path=%u input=%u cfg=%08x "
		"EDCCA=%s mode=%u power_offset=%u capabilities=%u,%u,%u,%u "
		"collision_state=%u cfg=%08x dynamic_policy=pending DFS_runtime=pending DIG_periodic=pending\n",
		dm->la_ready ? "initialized" : "pending", dm->la_buffer_bytes, dm->la_samples, dm->la_trigger_time, la,
		dm->psd_ready ? "initialized" : "pending", dm->psd_fft_points,
		(psd >> 11) & 3, (psd >> 13) & 3, (psd >> 17) & 3, (psd >> 19) & 3, psd,
		dm->edcca_ready ? "initialized" : "pending", dm->edcca_mode, dm->edcca_power_offset,
		dm->edcca_cap_2g, dm->edcca_cap_5g, dm->edcca_cap_6g, dm->edcca_cap_carrier,
		(collision >> 20) & 63, collision);
}
static DEVICE_ATTR_RO(radio_signal);

static ssize_t radio_filter_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	u32 options = rtl_ax_read32(radio, 0xce20);

	return sysfs_emit(buffer, "startup=AP_NORMAL4 options=%08x MPDU_max_bytes=%u "
		"MGMT=%08x CTRL=%08x DATA=%08x RX_INFO=%04x RTS=%04x\n",
		options, ((options >> 16) & 63) * 512, rtl_ax_read32(radio, 0xce2c),
		rtl_ax_read32(radio, 0xce28), rtl_ax_read32(radio, 0xce24), rtl_ax_read16(radio, 0xce04),
		rtl_ax_read16(radio, 0xc614));
}
static DEVICE_ATTR_RO(radio_filter);

static ssize_t radio_rx_mode_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	u32 options = rtl_ax_read32(radio, 0xce20);

	return sysfs_emit(buffer, "options=%08x sniffer=%u modes=0:SNIFFER,1:SCAN,4:AP_NORMAL "
		"MPDU_max_bytes=%u\n", options, options & 1, ((options >> 16) & 63) * 512);
}

static ssize_t radio_rx_mode_store(struct device *dev,
	struct device_attribute *attr, const char *buffer, size_t length)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	unsigned int mode;
	int ret = kstrtouint(buffer, 0, &mode);

	if (ret)
		return ret;
	mutex_lock(&radio->command_mutex);
	ret = rtl_ax_receive_mode(&radio->io, mode);
	mutex_unlock(&radio->command_mutex);
	return ret ? ret : length;
}
static DEVICE_ATTR_RW(radio_rx_mode);

static ssize_t radio_dfs_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_dfs *dfs = &radio->dfs;
	unsigned int i;
	ssize_t length = sysfs_emit(buffer, "host=%s region=%u dynamic=%u "
		"units=PW200ns_PRI25us PPB_parameter=%u radar_enable=pending "
		"pulse_parser=pending CAC_lifecycle=pending MKK_channel_profile=pending\n",
		dfs->initialized ? "initialized" : "pending", dfs->region,
		dfs->dynamic_setting, dfs->ppb_parameter);

	for (i = 0; i < 8; i++)
		length += sysfs_emit_at(buffer, length, "type=%u PW=%u..%u PRI=%u..%u pulses=%u\n",
			i, dfs->profile.pw_min[i], dfs->profile.pw_max[i],
			dfs->profile.pri_min[i], dfs->profile.pri_max[i], dfs->profile.pulses[i]);
	return length;
}
static DEVICE_ATTR_RO(radio_dfs);

static ssize_t radio_dig_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_dig *dig = &radio->dig;
	u32 ofdm = rtl_ax_read32(radio, 0x1481c);
	u32 cck = rtl_ax_read32(radio, 0x14b64);
	u32 cck_control = rtl_ax_read32(radio, 0x14b74);

	return sysfs_emit(buffer, "initialization=%s mode=%u max_gaincode=%u,%u,%u "
		"IB_peak=%d IB_backoff=%u initial_RSSI=%u IGI=%u FA=%u,%u,%u,%u "
		"TDMA_ms=%u FAHM_ms=%u damping=%u/%u PD_requested=%u bandwidth_index=%u "
		"OFDM_enable=%u encoded=%u cfg=%08x CCK_enable=%u low=%d high=%u "
		"cfg=%08x/%08x follow_PAGC=%u periodic_DIG=pending\n",
		dig->initialized ? "applied" : "pending", dig->mode,
		dig->max_gaincode[0], dig->max_gaincode[1], dig->max_gaincode[2],
		dig->ib_peak_power, dig->ib_backoff, dig->igi_rssi, dig->unit[0].igi,
		dig->unit[0].fa_threshold[0], dig->unit[0].fa_threshold[1],
		dig->unit[0].fa_threshold[2], dig->unit[0].fa_threshold[3],
		dig->tdma_period, dig->fahm_time, dig->damping_enabled, dig->damping_count_limit,
		dig->pd_requested, dig->bandwidth, !!(ofdm & BIT(29)), (ofdm >> 6) & 31,
		ofdm, !!(cck_control & BIT(30)), (s8)(cck >> 24), (cck >> 16) & 255,
		cck, cck_control, dig->unit[0].follow_pagc);
}
static DEVICE_ATTR_RO(radio_dig);

static ssize_t radio_cfo_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_cfo *cfo = &radio->cfo;
	u32 valid = rtl_ax_read32(radio, 0x14494);
	u32 weight = rtl_ax_read32(radio, 0x14490);
	u32 power = rtl_ax_read32(radio, 0xd248);

	return sysfs_emit(buffer, "initialization=%s flash_xcap=%u bounds=%u..%u "
		"thresholds=%u,%u,%u,%u steps=%u,%u,%u,%u digital_shift=%u "
		"timer_ms=%u timer_enabled=%u dynamic_acceleration=%u "
		"manual_comp=%u valid=%u weight=%u MAC_comp=%u "
		"cfg=%08x/%08x/%08x periodic_CFO=pending crystal_adjustment=pending\n",
		cfo->initialized ? "applied" : "pending", cfo->crystal_default,
		cfo->crystal_lower, cfo->crystal_upper,
		cfo->threshold[0], cfo->threshold[1], cfo->threshold[2], cfo->threshold[3],
		cfo->step[0], cfo->step[1], cfo->step[2], cfo->step[3], cfo->digital_shift,
		cfo->timer_period, cfo->timer_enabled, cfo->dynamic_acceleration,
		rtl_ax_read32(radio, 0x14264) & 255, !!(valid & BIT(29)), (weight >> 24) & 15,
		power & 7, valid, weight, power);
}
static DEVICE_ATTR_RO(radio_cfo);

static ssize_t radio_environment_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_environment *environment = &radio->environment;
	unsigned int i;
	ssize_t length = sysfs_emit(buffer, "initialization=%s CCX_unit=%u CLM_input=%u "
		"NHM_include_CCA=%u threshold_last_NHM_FAHM=%u,%u "
		"measurement_trigger=pending report_collection=pending\n",
		environment->initialized ? "applied" : "pending", environment->ccx_unit,
		environment->clm_input, environment->nhm_include_cca,
		environment->nhm_threshold[10], environment->fahm_threshold[10]);

	for (i = 0; i < ARRAY_SIZE(rtl_ax_environment_start); i++) {
		const struct rtl_ax_environment_write *write = &rtl_ax_environment_start[i];

		length += sysfs_emit_at(buffer, length, "entry=%u register=%04x mask=%08x "
			"expected=%08x readback=%08x\n", i, write->address, write->mask,
			write->value, rtl_ax_read32(radio, 0x10000 | write->address) & write->mask);
	}
	return length;
}
static DEVICE_ATTR_RO(radio_environment);

static ssize_t radio_rx_pipeline_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	static const char * const phy_names[5] = { "CCK", "OFDM", "HT", "VHT", "HE" };
	static const char * const mac_names[8] = { "CCK", "OFDM", "HT", "VHTSU", "VHTMU", "HESU", "HEMU", "HETB" };
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_rx_pipeline snapshot;
	unsigned int i;
	ssize_t length;

	mutex_lock(&radio->command_mutex);
	rtl_ax_rx_pipeline_read(&radio->io, &snapshot);
	mutex_unlock(&radio->command_mutex);
	length = sysfs_emit(buffer, "CCK_CCA=%u OFDM_CCA=%u HAXI=%08x DMAC=%08x CMAC=%08x "
		"RXQ_index=%08x RPQ_index=%08x\n", snapshot.cca[0], snapshot.cca[1],
		snapshot.haxi, snapshot.dmac, snapshot.cmac, snapshot.rx_index[0], snapshot.rx_index[1]);
	length += sysfs_emit_at(buffer, length, "CCK_gate_0700_2320_0c80=%08x,%08x,%08x\n",
		snapshot.cck_gate[0], snapshot.cck_gate[1], snapshot.cck_gate[2]);
	length += sysfs_emit_at(buffer, length, "BB_center_frequency_MHz=%u SCO=%u channel_report=%u\n",
		snapshot.center_frequency, snapshot.sco, snapshot.channel_report);
	for (i = 0; i < 5; i++)
		length += sysfs_emit_at(buffer, length, "PHY=%s CRC_OK=%u CRC_error=%u\n",
			phy_names[i], snapshot.crc_ok[i], snapshot.crc_error[i]);
	for (i = 0; i < 8; i++)
		length += sysfs_emit_at(buffer, length, "MAC=%s CRC_OK=%u CRC_error=%u false_alarm=%u\n",
			mac_names[i], snapshot.mac_crc_ok[i], snapshot.mac_crc_error[i], snapshot.mac_false_alarm[i]);
	return length;
}
static DEVICE_ATTR_RO(radio_rx_pipeline);

static ssize_t radio_channel_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_channel *channel = &radio->channel;
	u32 analog[2], digital[2];
	unsigned int path;
	int ret;

	mutex_lock(&radio->command_mutex);
	for (path = 0; path < 2; path++) {
		ret = rtl_ax_phy_rf_read(&radio->io, path, 0x18, &analog[path]);
		if (ret)
			goto unlock;
		ret = rtl_ax_phy_rf_read(&radio->io, path, 0x10018, &digital[path]);
		if (ret)
			goto unlock;
	}
	ret = sysfs_emit(buffer, "native_operation=%s/20MHz configured=%u band=%u primary=%u center=%u "
		"BB_frequency=%u BB_channel=%u RF18_analog=%05x,%05x digital=%05x,%05x "
		"external_loss=%d,%d RFK=pending mac80211_channel=%s\n",
		radio->chip == RTL_AX_8852C ? "5GHz" : "2.4GHz",
		channel->configured, channel->band, channel->primary, channel->center,
		rtl_ax_read32(radio, 0x14e30) & 0x1fff, (rtl_ax_read32(radio, 0x10734) >> 16) & 255,
		analog[0], analog[1], digital[0], digital[1], channel->external_loss[0], channel->external_loss[1],
		radio->hw ? "connected" : "pending");
unlock:
	mutex_unlock(&radio->command_mutex);
	return ret;
}

/* Caller holds command_mutex across channel and receive-mode ownership. */
static int rtl_ax_receive_set_channel(struct rtl_ax_ap *radio, unsigned int channel)
{
	int ret;

	rtl_ax_rx_close(radio->rx);
	rtl_ax_hci_pause(&radio->io);
	pci_clear_master(radio->pdev);
	ret = radio->chip == RTL_AX_8852C ?
		rtl_ax_c_receive_channel20(&radio->io, &radio->phy, &radio->bb_dm, channel, &radio->channel) :
		rtl_ax_xb_receive_channel20(&radio->io, &radio->phy, &radio->bb_dm, channel, &radio->channel);
	if (!ret) {
		if (radio->chip == RTL_AX_8192XB)
			rtl_ax_xb_tssi_de(&radio->io, &radio->flash_calibration, &radio->trim, channel, &radio->tssi_de);
		else
			rtl_ax_c_tssi_de20(&radio->io, &radio->flash_calibration, &radio->trim, channel, &radio->tssi_de);
	}
	pci_set_master(radio->pdev);
	rtl_ax_h2c_start(&radio->io);
	rtl_ax_rx_open(radio->rx);
	return ret;
}

static ssize_t radio_channel_store(struct device *dev,
	struct device_attribute *attr, const char *buffer, size_t length)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	unsigned int channel;
	int ret;

	ret = kstrtouint(buffer, 0, &channel);
	if (ret)
		return ret;
	/* Once nl80211 owns the channel, its chandef must describe every RX. */
	mutex_lock(&radio->command_mutex);
	ret = radio->monitor_started ? -EBUSY : rtl_ax_receive_set_channel(radio, channel);
	mutex_unlock(&radio->command_mutex);
	return ret ? ret : length;
}
static DEVICE_ATTR_RW(radio_channel);

#include "monitor.h"

static ssize_t radio_rf_start_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_rf_start *rf = &radio->rf_start;

	return sysfs_emit(buffer, "crystal=%s shared=%u requested=%u XSI4=%02x XSI5=%02x XSI8=%02x "
		"RF_GPIO=%s selector=%08x inverse=%08x,%08x "
		"source=%08x,%08x/%08x,%08x MAC_mux=%02x,%02x,%02x,%02x,%02x,%02x,%02x "
		"NCTL=%s pairs=%u reset_observations=%u reset_readback=%08x "
		"NCTL8000=%08x NCTL8080=%08x RCK=%s code=%u,%u done=%u,%u "
		"observations=%u,%u DACK=%s RX_DCK=%s calibration=pending\n",
		rf->crystal_ready ? "applied" : "pending", independent_xtal, rf->crystal_requested,
		rf->crystal_observed[0], rf->crystal_observed[1], rf->share_control,
		rf->gpio_ready ? "applied" : "pending", rtl_ax_read32(radio, 0x1033c),
		rtl_ax_read32(radio, 0x15890), rtl_ax_read32(radio, 0x17890),
		rtl_ax_read32(radio, 0x15880), rtl_ax_read32(radio, 0x15884),
		rtl_ax_read32(radio, 0x17880), rtl_ax_read32(radio, 0x17884),
		rtl_ax_read8(radio, 0x2d0), rtl_ax_read8(radio, 0x2d1), rtl_ax_read8(radio, 0x2d2),
		rtl_ax_read8(radio, 0x2d3), rtl_ax_read8(radio, 0x2d4), rtl_ax_read8(radio, 0x2d5), rtl_ax_read8(radio, 0x2d6),
		rf->nctl_ready ? "loaded" : "pending", rf->nctl_pairs, rf->nctl_wait_observations,
		rf->nctl_wait_value, rtl_ax_read32(radio, 0x18000), rtl_ax_read32(radio, 0x18080),
		rf->rck_ready ? "applied" : "pending", rf->rck_code[0], rf->rck_code[1],
		rf->rck_done[0], rf->rck_done[1], rf->rck_observations[0], rf->rck_observations[1],
		radio->dack.dac_ready ? "applied" : "pending", radio->rxdck.ready ? "applied" : "pending");
}
static DEVICE_ATTR_RO(radio_rf_start);

static ssize_t radio_dack_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_dack *dack = &radio->dack;

	return sysfs_emit(buffer, "DRCK=%s done=%u code=%u observations=%u c0c4=%08x c0c8=%08x "
		"ADC_DC=%s done=%u,%u I_Q_A=%u,%u I_Q_B=%u,%u observations=%u,%u "
		"reload=%08x,%08x DAC_DC=%s DACK=%s\n",
		dack->drck_ready ? "applied" : "pending", dack->drck_done, dack->drck_code,
		dack->drck_observations, rtl_ax_read32(radio, 0x1c0c4), rtl_ax_read32(radio, 0x1c0c8),
		dack->adc_ready ? "applied" : "pending", dack->adc_done[0], dack->adc_done[1],
		dack->adc_dc[0][0], dack->adc_dc[0][1], dack->adc_dc[1][0], dack->adc_dc[1][1],
		dack->adc_observations[0], dack->adc_observations[1], rtl_ax_read32(radio, 0x1c0f8),
		rtl_ax_read32(radio, 0x1c1f8), dack->dac_ready ? "applied" : "pending",
		dack->dac_ready ? "applied" : "pending");
}
static DEVICE_ATTR_RO(radio_dack);

static ssize_t radio_dac_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_dack *dack = &radio->dack;
	unsigned int path, index, group;
	ssize_t length = sysfs_emit(buffer, "DAC_DC=%s done=%u,%u observations=%u,%u bias=%u,%u/%u,%u "
		"DC=%u,%u/%u,%u\n", dack->dac_ready ? "applied" : "pending",
		dack->dac_done[0], dack->dac_done[1], dack->dac_observations[0], dack->dac_observations[1],
		dack->bias[0][0], dack->bias[0][1], dack->bias[1][0], dack->bias[1][1],
		dack->dac_dc[0][0], dack->dac_dc[0][1], dack->dac_dc[1][0], dack->dac_dc[1][1]);

	for (path = 0; path < 2; path++)
		for (index = 0; index < 2; index++)
			for (group = 0; group < 5; group++) {
				u32 address = 0x1c200 + path * 0x28 + index * 0x14 + group * 4;
				u32 expected = dack->reload[path][index][group] | (group == 4 ? 1 : 0);

				length += sysfs_emit_at(buffer, length, "path=%u IQ=%u reg=%05x expected=%08x readback=%08x\n",
					path, index, address, expected, rtl_ax_read32(radio, address));
			}
	return length;
}
static DEVICE_ATTR_RO(radio_dac);

static ssize_t radio_rx_dck_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_rxdck *state = &radio->rxdck;
	unsigned int path, i;
	ssize_t length = sysfs_emit(buffer, "RX_DCK=%s method=%s done=%u,%u/%u,%u observations=%u,%u/%u,%u\n",
		state->ready ? "applied" : "pending", radio->chip == RTL_AX_8192XB ? "AFE" : "RF",
		state->done[0][0], state->done[0][1], state->done[1][0], state->done[1][1],
		state->observations[0][0], state->observations[0][1], state->observations[1][0], state->observations[1][1]);

	for (path = 0; path < 2; path++) {
		if (radio->chip == RTL_AX_8192XB) {
			for (i = 0; i < 7; i++)
				length += sysfs_emit_at(buffer, length, "path=%u backup=%u saved=%08x restored=%08x\n",
					path, i, state->saved[path][i], state->restored[path][i]);
		} else {
			static const u8 gain[] = { 0, 1, 12, 13, 16, 17, 28, 29 };

			length += sysfs_emit_at(buffer, length, "path=%u thermal=%u reK_enabled=%u corrections=%u\n",
				path, state->thermal[path], state->rek_enabled, state->corrections[path]);
			for (i = 0; i < 8; i++)
				length += sysfs_emit_at(buffer, length, "path=%u gain=%u I=%u Q=%u\n",
					path, gain[i], state->offsets[path][i][0], state->offsets[path][i][1]);
		}
		length += sysfs_emit_at(buffer, length, "path=%u RF92=%05x RF93=%05x\n",
			path, state->rf_result[path][0], state->rf_result[path][1]);
	}
	return length;
}
static DEVICE_ATTR_RO(radio_rx_dck);

static ssize_t radio_trim_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_trim *trim = &radio->trim;
	unsigned int path, i;
	ssize_t length = sysfs_emit(buffer, "trim=%s physical_OTP=%03x+%u thermal_written=%u bias_written=%u "
		"TSSI_2G5G_erased=%u TSSI_6G_erased=%u hardware_TSSI=pending\n",
		trim->ready ? "decoded-and-applied" : "pending", trim->otp_start, trim->otp_bytes,
		trim->thermal_written, trim->bias_written, trim->tssi_erased, trim->tssi_6g_erased);

	for (path = 0; path < 2; path++) {
		length += sysfs_emit_at(buffer, length, "path=%u thermal_byte=%02x code=%u RF43=%05x bias_byte=%02x RF60=%05x\n",
			path, trim->thermal[path], trim->thermal_code[path], trim->rf43[path], trim->bias[path], trim->rf60[path]);
		for (i = 0; i < 8; i++)
			length += sysfs_emit_at(buffer, length, "path=%u group=%u TSSI_2G5G=%d TSSI_6G=%d\n",
				path, i, trim->tssi[path][i], trim->tssi_6g[path][i]);
	}
	return length;
}
static DEVICE_ATTR_RO(radio_trim);

static ssize_t radio_ref_power_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_ref_power *state = &radio->ref_power;

	return sysfs_emit(buffer, "reference_power=%s OFDM_CCK_ref=0,0 base_code=33 power_CW=%u TSSI_CW=%u "
		"expected_BB=%08x D200=%08x BB5804_5808_7804_7808=%08x,%08x,%08x,%08x\n",
		state->ready ? "applied" : "pending", state->power_cw, state->tssi_cw, state->bb_word,
		state->mac_readback, state->bb_readback[0], state->bb_readback[1],
		state->bb_readback[2], state->bb_readback[3]);
}
static DEVICE_ATTR_RO(radio_ref_power);

static ssize_t radio_tssi_de_show(struct device *dev,
	struct device_attribute *attr, char *buffer)
{
	struct rtl_ax_ap *radio = dev_get_drvdata(dev);
	struct rtl_ax_tssi_de *state = &radio->tssi_de;
	unsigned int path, i;
	ssize_t length = sysfs_emit(buffer, "TSSI_DE=%s channel=%u CCK_group=%u OFDM_group=%04x trim_group=%04x "
		"hardware_TSSI_tracking=pending C_BW_slope_inputs=flash-imported slope_no_pg=%u\n",
		state->ready ? "applied" : "pending", state->center, state->cck_group, state->ofdm_group, state->trim_group,
		state->slope_no_pg);

	for (path = 0; path < 2; path++) {
		length += sysfs_emit_at(buffer, length, "path=%u CCK_DE=%d OFDM_DE=%d trim=%d\n",
			path, state->cck[path], state->ofdm[path], state->trim[path]);
		length += sysfs_emit_at(buffer, length, "path=%u BW20_40_80_160_diff=%d,%d,%d,%d\n",
			path, state->bw_diff[path][0], state->bw_diff[path][1], state->bw_diff[path][2], state->bw_diff[path][3]);
		for (i = 0; i < 8; i++)
			length += sysfs_emit_at(buffer, length, "path=%u slot=%u expected=%u readback=%u\n",
				path, i, state->expected[path][i], state->readback[path][i]);
	}
	return length;
}
static DEVICE_ATTR_RO(radio_tssi_de);

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
	&dev_attr_radio_power.attr, &dev_attr_radio_limits.attr, &dev_attr_radio_bb_dm.attr,
	&dev_attr_radio_physts.attr, &dev_attr_radio_txdiff.attr, &dev_attr_radio_phy_stats.attr,
	&dev_attr_radio_signal.attr, &dev_attr_radio_filter.attr, &dev_attr_radio_dfs.attr,
	&dev_attr_radio_dig.attr, &dev_attr_radio_cfo.attr, &dev_attr_radio_environment.attr,
	&dev_attr_radio_rx_pipeline.attr, &dev_attr_radio_channel.attr, &dev_attr_radio_rx_mode.attr,
	&dev_attr_radio_rf_start.attr, &dev_attr_radio_dack.attr, &dev_attr_radio_dac.attr,
	&dev_attr_radio_mac80211.attr, &dev_attr_radio_rx_dck.attr, &dev_attr_radio_trim.attr,
	&dev_attr_radio_ref_power.attr, &dev_attr_radio_tssi_de.attr, NULL,
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
		radio->bb_dm.ic_ready = false;
		radio->bb_dm.gpio_ready = false;
		radio->bb_dm.gain_ready = false;
		radio->bb_dm.physts_ready = false;
		radio->bb_dm.reports_ready = false;
		radio->bb_dm.txdiff_ready = false;
		radio->bb_dm.macid_power_ready = false;
		radio->bb_dm.statistics_ready = false;
		radio->bb_dm.la_ready = false;
		radio->bb_dm.psd_ready = false;
		radio->bb_dm.edcca_ready = false;
		radio->dfs.initialized = false;
		radio->dig.initialized = false;
		radio->cfo.initialized = false;
		radio->environment.initialized = false;
		radio->rf_start.crystal_ready = false;
		radio->rf_start.gpio_ready = false;
		radio->rf_start.nctl_ready = false;
		radio->rf_start.rck_ready = false;
		radio->dack.drck_ready = false;
		radio->dack.adc_ready = false;
		radio->dack.dac_ready = false;
		radio->rxdck.ready = false;
		radio->trim.ready = false;
		radio->ref_power.ready = false;
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
	ret = rtl_ax_monitor_register(radio);
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
	struct rtl_ax_ap *radio = pci_get_drvdata(pdev);

	rtl_ax_monitor_unregister(radio);
	rtl_ax_stop(radio);
}

static void rtl_ax_shutdown(struct pci_dev *pdev)
{
	struct rtl_ax_ap *radio = pci_get_drvdata(pdev);

	rtl_ax_monitor_unregister(radio);
	rtl_ax_stop(radio);
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
