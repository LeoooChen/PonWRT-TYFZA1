// SPDX-License-Identifier: GPL-2.0-only
/* Native RFE54 table execution follows the original TYFZA1 module's BB/RF
 * callbacks and radio-to-firmware page format. AP start supplies H2C/roles. */
#include <linux/ctype.h>
#include <linux/device.h>
#include <linux/firmware.h>
#include <linux/slab.h>
#include <linux/string.h>
#include "phy.h"

static void rtl_ax_gain_bytes(s8 *out, unsigned int count, u32 value)
{
	unsigned int i;

	for (i = 0; i < count; i++)
		out[i] = (s8)(value >> (8 * i));
}

static void rtl_ax_gain_rpl(struct rtl_ax_phy_gain *gain, unsigned int band,
			    unsigned int path, unsigned int selector, u32 value)
{
	unsigned int bandwidth = selector >> 4, first = selector & 15, count = 0;
	s8 *out;

	/* halbb_cfg_bb_rpl_ofst: exact full/20/40/80 MHz RXSC groups. */
	if (!bandwidth) {
		gain->rpl20[band][path] = (s8)value;
		return;
	}
	switch (bandwidth) {
	case 1:
		out = gain->rpl40[band][path];
		count = first == 0 ? 1 : first == 1 ? 2 : 0;
		break;
	case 2:
		out = gain->rpl80[band][path];
		count = first == 0 ? 1 : first == 1 ? 4 : first == 9 ? 2 : 0;
		break;
	case 3:
		out = gain->rpl160[band][path];
		count = first == 0 ? 1 : first == 1 || first == 5 || first == 9 ? 4 :
			first == 13 ? 2 : 0;
		break;
	default:
		return;
	}
	if (count)
		rtl_ax_gain_bytes(out + first, count, value);
}

static void rtl_ax_gain_decode(struct rtl_ax_phy_gain *gain,
			       const struct rtl_ax_phy_table *table)
{
	unsigned int entry, i;

	for (entry = 0; entry < table->count; entry++) {
		u32 address = table->pairs[entry].address, value = table->pairs[entry].value;
		unsigned int category = address >> 24, band = (address >> 16) & 255;
		unsigned int path = (address >> 8) & 255, type = address & 255;
		s8 *out;

		/* Original halbb_cfg_bb_gain_C/XB bounds precede all stores. */
		if (band >= 8 || path >= 2)
			continue;
		switch (category) {
		case 0:
			if (type == 2) {
				rtl_ax_gain_bytes(gain->tia[band][path], 2, value);
				break;
			}
			out = gain->lna[band][path];
			if (type <= 1)
				rtl_ax_gain_bytes(out + type * 4, type ? 3 : 4, value);
			break;
		case 1:
			rtl_ax_gain_rpl(gain, band, path, type, value);
			break;
		case 2:
			out = gain->lna_bypass[band][path];
			if (type <= 1)
				rtl_ax_gain_bytes(out + type * 4, type ? 3 : 4, value);
			break;
		case 3:
			if (type <= 1)
				rtl_ax_gain_bytes(gain->lna_op1db[band][path] + type * 4,
						  type ? 3 : 4, value);
			else if (type <= 3)
				rtl_ax_gain_bytes(gain->tia_lna_op1db[band][path] + (type - 2) * 4,
						  4, value);
			break;
		case 4:
			switch (type) {
			case 0:
				gain->wb_elna[band][path] = value;
				break;
			case 1:
			case 2:
				for (i = 0; i < 8; i++)
					gain->wb_lna_tia[band][path][(type - 1) * 8 + i] =
						(value >> (4 * i)) & 7;
				break;
			case 3:
			case 4:
				gain->gs[band][path][type - 3] = value;
				break;
			case 5:
				for (i = 0; i < 2; i++)
					gain->elna[band][path][i] = value >> (8 * i);
				break;
			}
			break;
		}
	}
}

static int rtl_ax_phy_read_table(struct device *dev, const char *name,
				 struct rtl_ax_phy_table *table)
{
	const struct firmware *fw;
	char *text, *cursor, *line;
	unsigned int rows = 1, i;
	int ret;

	ret = request_firmware(&fw, name, dev);
	if (ret)
		return ret;
	for (i = 0; i < fw->size; i++)
		rows += fw->data[i] == '\n';
	text = kmemdup_nul(fw->data, fw->size, GFP_KERNEL);
	table->pairs = kcalloc(rows, sizeof(*table->pairs), GFP_KERNEL);
	if (!text || !table->pairs) {
		ret = -ENOMEM;
		goto release;
	}
	cursor = text;
	while ((line = strsep(&cursor, "\n"))) {
		unsigned int address, value;
		int fields;

		line = skip_spaces(line);
		if (!*line || !strncmp(line, "//", 2))
			continue;
		fields = sscanf(line, "%x %x", &address, &value);
		if (fields >= 1 && address == 0xffff)
			break; /* _hal_parse_phyreg/_hal_parse_radio terminator. */
		if (fields != 2) {
			ret = -EINVAL;
			goto release;
		}
		/* These board files contain headless flat tables. Conditional image
		 * opcodes need their own stock headline selection,not MMIO writes. */
		if (address >> 28) {
			ret = -EOPNOTSUPP;
			goto release;
		}
		table->pairs[table->count++] = (struct rtl_ax_phy_pair) { address, value };
	}
release:
	kfree(text);
	release_firmware(fw);
	if (ret) {
		kfree(table->pairs);
		table->pairs = NULL;
		table->count = 0;
	}
	return ret;
}

void rtl_ax_phy_release(struct rtl_ax_phy_inputs *inputs)
{
	unsigned int stage, id;

	for (stage = 0; stage < RTL_AX_PHY_STAGES; stage++)
		for (id = 0; id < RTL_AX_PHY_TABLES; id++) {
			kfree(inputs->table[stage][id].pairs);
			inputs->table[stage][id] = (struct rtl_ax_phy_table) { };
		}
}

int rtl_ax_phy_load(struct device *dev, unsigned int chip, struct rtl_ax_phy_inputs *inputs)
{
	const char *family = chip == RTL_AX_8852C ? "rtl8852ce" : "rtl8192xbe";
	const char *fem = chip == RTL_AX_8852C ? "RTK66587_BPF" : "RTK66287";
	const char *names[] = { "PHY_REG.txt", "PHY_REG_GAIN.txt", "RadioA.txt", "RadioB.txt" };
	unsigned int stage, id;
	char name[128];
	int ret;

	inputs->chip = chip;
	for (stage = 0; stage < RTL_AX_PHY_STAGES; stage++)
		for (id = 0; id < RTL_AX_PHY_TABLES; id++) {
			snprintf(name, sizeof(name), "realtek/tyfza1/%s/RFE54/%s%s%s",
				 family, stage ? fem : "", stage ? "/" : "", names[id]);
			ret = rtl_ax_phy_read_table(dev, name, &inputs->table[stage][id]);
			if (ret) {
				rtl_ax_phy_release(inputs);
				return ret;
			}
		}
	/* Stock FEM re-init overlays the existing gain object;unmentioned
	 * RXSC/band fields survive from the base table. Keep both stages. */
	rtl_ax_gain_decode(&inputs->gain[RTL_AX_PHY_BASE], &inputs->table[RTL_AX_PHY_BASE][RTL_AX_PHY_GAIN]);
	inputs->gain[RTL_AX_PHY_FEM] = inputs->gain[RTL_AX_PHY_BASE];
	rtl_ax_gain_decode(&inputs->gain[RTL_AX_PHY_FEM], &inputs->table[RTL_AX_PHY_FEM][RTL_AX_PHY_GAIN]);
	return 0;
}

void rtl_ax_phy_bb_apply(const struct rtl_ax_io *io, const struct rtl_ax_phy_inputs *inputs,
			enum rtl_ax_phy_stage stage)
{
	const struct rtl_ax_phy_table *table = &inputs->table[stage][RTL_AX_PHY_REG];
	const unsigned int delays[] = { 1, 5, 50, 1000, 5000, 50000 };
	unsigned int i;

	/* halbb_cfg_bb_phy_C/XB and halbb_set_cr: BB window OR0x10000.
	 * Gain table keys address host gain objects,not this MMIO window. */
	for (i = 0; i < table->count; i++) {
		unsigned int address = table->pairs[i].address;

		if (address >= 0xf9 && address <= 0xfe)
			io->delay_us(io->ctx, delays[address - 0xf9]);
		else
			io->write32(io->ctx, address | 0x10000, table->pairs[i].value);
	}
}

void rtl_ax_phy_bb_offset(const struct rtl_ax_io *io, struct rtl_ax_phy_inputs *inputs)
{
	/* halbb_init_reg captures these post-table signed offsets. XB has one
	 * shared PHY0 pair;C retains independent path A/B pairs. */
	inputs->gain_offset[0] = (s8)io->read32(io->ctx, 0x14df0);
	if (inputs->chip == RTL_AX_8852C)
		inputs->gain_offset[1] = (s8)io->read32(io->ctx, 0x16df0);
	inputs->gain_offset_tb[0] = (s8)(io->read32(io->ctx, 0x14e0c) >> 24);
	if (inputs->chip == RTL_AX_8852C)
		inputs->gain_offset_tb[1] = (s8)(io->read32(io->ctx, 0x16e0c) >> 24);
}

void rtl_ax_phy_bb_reset(const struct rtl_ax_io *io, unsigned int chip)
{
	const unsigned int hold[] = {0x158dc, 0x15818, 0x178dc, 0x17818};
	unsigned int i;

	/* The actual C implementation is logging-only. XB holds both paths'
	 * TSSI blocks,then performs BB0704 bit1=1→0→1 before releasing them. */
	if (chip == RTL_AX_8852C)
		return;
	for (i = 0; i < ARRAY_SIZE(hold); i++)
		rtl_ax_update32(io, hold[i], 0, RTL_AX_BIT(30));
	rtl_ax_update32(io, 0x10704, 0, RTL_AX_BIT(1));
	rtl_ax_update32(io, 0x10704, RTL_AX_BIT(1), 0);
	rtl_ax_update32(io, 0x10704, 0, RTL_AX_BIT(1));
	for (i = 0; i < ARRAY_SIZE(hold); i++)
		rtl_ax_update32(io, hold[i], RTL_AX_BIT(30), 0);
}

static int rtl_ax_phy_rf_write(const struct rtl_ax_io *io, unsigned int path,
			       unsigned int address, unsigned int value)
{
	unsigned int last;
	int ret;

	/* halbb_write_rf_reg_* selects the die using bit16 of the RF address.
	 * Original flat radio callbacks pass a full20-bit mask. */
	if (address & 0x10000) {
		unsigned int direct = (path ? 0xf000 : 0xe000) + ((address & 0xff) << 2);

		rtl_ax_update32(io, direct | 0x10000, 0xfffff, value & 0xfffff);
		io->delay_us(io->ctx, 1);
		return 0;
	}
	/* The board binary waits31 times for read/write busy to clear. Its
	 * analog write is a single SIPI command,without newer SDK retries. */
	ret = io->poll32(io->ctx, 0x1174c, 0x03000000, 0, 1, 31, &last);
	if (ret)
		return ret;
	/* The original delays1us even on its final idle observation. */
	io->delay_us(io->ctx, 1);
	io->write32(io->ctx, 0x10370,
		    (path << 28) | ((address & 0xff) << 20) | (value & 0xfffff));
	return 0;
}

static int rtl_ax_phy_radio_path(const struct rtl_ax_io *io,
				const struct rtl_ax_phy_table *table, unsigned int path,
				const struct rtl_ax_phy_commands *commands)
{
	__le32 *words;
	unsigned int count = 0, i, page, pages;
	int ret = 0;

	words = kcalloc(table->count, sizeof(*words), GFP_KERNEL);
	if (!words)
		return -ENOMEM;
	for (i = 0; i < table->count; i++) {
		unsigned int address = table->pairs[i].address;
		unsigned int value = table->pairs[i].value;

		/* Original halrf_cfg_rf_radio callbacks call halrf_wrf directly,
		 * includingf9..fe;they do not use BB table delay opcodes. */
		ret = rtl_ax_phy_rf_write(io, path, address, value);
		if (ret)
			goto release;
		/* Original store_radio_* excludes analog RF and delay addresses;
		 * firmware gets (8-bit register <<20)|value for digital RF only. */
		if (address > 0xff)
			words[count++] = cpu_to_le32(((address & 0xff) << 20) | value);
	}
	pages = count / 500;
	for (page = 0; page < pages; page++) {
		ret = commands->send_radio(commands->ctx, path ? 9 : 8, page,
					   words + page * 500, 2000);
		if (ret)
			goto release;
	}
	/* The original sends the final page even when its remaining length is0. */
	ret = commands->send_radio(commands->ctx, path ? 9 : 8, page,
				   words + page * 500, (count % 500) * 4);
release:
	kfree(words);
	return ret;
}

int rtl_ax_phy_radio_apply(const struct rtl_ax_io *io,
			   const struct rtl_ax_phy_inputs *inputs, enum rtl_ax_phy_stage stage,
			   const struct rtl_ax_phy_commands *commands)
{
	unsigned int first = inputs->chip == RTL_AX_8852C ? 1 : 0;
	int ret;

	/* Actual halrf_config_radio@3030e8: C runs B then A;XB runs A then B. */
	ret = rtl_ax_phy_radio_path(io, &inputs->table[stage][RTL_AX_RADIO_A + first],
				    first, commands);
	if (ret)
		return ret;
	return rtl_ax_phy_radio_path(io, &inputs->table[stage][RTL_AX_RADIO_A + !first],
				    !first, commands);
}
