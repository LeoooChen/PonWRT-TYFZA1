// SPDX-License-Identifier: GPL-2.0-only
/* TYFZA1 original ByRate and thermal tracking host objects. Hardware power
 * uses the original SCC TPU fields. */
#include <linux/ctype.h>
#include <linux/device.h>
#include <linux/firmware.h>
#include <linux/slab.h>
#include <linux/string.h>
#include "power-tables.h"
#include "tx-shape.h"

static void rtl_ax_rate_copy(s8 *out, const s8 *values, unsigned int count)
{
	memcpy(out, values, count);
}

static int rtl_ax_rate_row(struct rtl_ax_power_stage *power, unsigned int band,
			   const char *stream, const char *group, const s8 values[4])
{
	s8 *out = power->rate[band];
	unsigned int nss, start;

	/* Exact destinations from halrf_power_by_rate_store_to_array@305790.
	 * Both endpoints have the original two-stream layout. */
	if (!strcmp(stream, "[Offset]")) {
		if (!band && !strcmp(group, "AllRate2_1"))
			rtl_ax_rate_copy(out + 141, values, 4);
		else if (!band && !strcmp(group, "AllRate2_2"))
			out[140] = values[3];
		else if ((band == 1 && !strcmp(group, "AllRate5_1")) ||
			 (band == 2 && !strcmp(group, "AllRate6_1")))
			rtl_ax_rate_copy(out + 140, values, 4);
		else
			return -EINVAL;
		return 0;
	}
	if (!strcmp(stream, "[Nss1]"))
		nss = 0;
	else if (!strcmp(stream, "[Nss2]"))
		nss = 1;
	else
		return -EINVAL;
	if (!nss && !band && !strcmp(group, "11M_1M"))
		rtl_ax_rate_copy(out, values, 4);
	else if (!nss && !strcmp(group, "18M_6M"))
		rtl_ax_rate_copy(out + 4, values, 4);
	else if (!nss && !strcmp(group, "54M_24M"))
		rtl_ax_rate_copy(out + 8, values, 4);
	else if (!strcmp(group, "DCM4_0"))
		rtl_ax_rate_copy(out + 132 + nss * 4, values, 4);
	else if (!strcmp(group, "MCS11_8")) {
		rtl_ax_rate_copy(out + 52 + nss * 10, values, 2);
		rtl_ax_rate_copy(out + 92 + nss * 12, values, 4);
	} else if (!strcmp(group, "MCS3_0") || !strcmp(group, "MCS7_4")) {
		start = !strcmp(group, "MCS3_0") ? 0 : 4;
		rtl_ax_rate_copy(out + 12 + nss * 8 + start, values, 4);
		rtl_ax_rate_copy(out + 44 + nss * 10 + start, values, 4);
		rtl_ax_rate_copy(out + 84 + nss * 12 + start, values, 4);
	} else {
		return -EINVAL;
	}
	return 0;
}

static int rtl_ax_rate_parse(char *text, struct rtl_ax_power_stage *power)
{
	char *line;
	unsigned int band = 0, i;
	bool header = false;

	while ((line = strsep(&text, "\n"))) {
		char stream[12], group[20], token[4][16];
		s8 values[4];
		int value, ret;

		line = skip_spaces(line);
		if (!*line || !strncmp(line, "//", 2))
			continue;
		if (!header) {
			if (strncmp(line, "#[v3][Exact]#", 13))
				return -EINVAL;
			header = true;
			continue;
		}
		if (!strncmp(line, "#[2.4G]#", 8)) { band = 0; continue; }
		if (!strncmp(line, "#[5G]#", 6)) { band = 1; continue; }
		if (!strncmp(line, "#[6G]#", 6)) { band = 2; continue; }
		if (!strncmp(line, "#[START]#", 9) || !strncmp(line, "#[END]#", 7))
			continue;
		if (sscanf(line, "%11s %19s %15s %15s %15s %15s", stream, group,
			   token[0], token[1], token[2], token[3]) != 6)
			return -EINVAL;
		for (i = 0; i < 4; i++) {
			char *fraction = strchr(token[i], '.');

			/* phypg_combvalue_fromstring@1d2188 multiplies the signed
			 * integer byte by4;its fractional output is not added. */
			if (fraction)
				*fraction = '\0';
			ret = kstrtoint(token[i], 10, &value);
			if (ret)
				return ret;
			/* Input runs high-rate/high-byte first;object stores low first. */
			values[3 - i] = (s8)((u8)value * 4);
		}
		ret = rtl_ax_rate_row(power, band, stream, group, values);
		if (ret)
			return ret;
		power->rate_rows++;
	}
	return header && power->rate_rows ? 0 : -EINVAL;
}

static int rtl_ax_track_parse(char *text, struct rtl_ax_power_stage *power)
{
	char *line;

	while ((line = strsep(&text, "\n"))) {
		char band[4] = {}, path, sign, modulation[4] = {}, *cursor, *end;
		unsigned int group = 0, rfpath, direction, count = 0;
		s8 *out;

		line = skip_spaces(line);
		if (!*line || !strncmp(line, "//", 2))
			continue;
		if (sscanf(line, "[%2c][%c][%c][%3c]", band, &path, &sign, modulation) != 4)
			return -EINVAL;
		if ((path != 'A' && path != 'B') || (sign != '+' && sign != '-'))
			return -EINVAL;
		rfpath = path == 'B';
		direction = sign == '-';
		cursor = strchr(line, '{');
		end = strchr(line, '}');
		if (!cursor || !end)
			return -EINVAL;
		*end = '\0';
		if (!strcmp(band, "2G")) {
			if (!strcmp(modulation, "CCK"))
				out = power->track_cck[rfpath][direction];
			else if (!strcmp(modulation, "ALL"))
				out = power->track_2g[rfpath][direction];
			else
				return -EINVAL;
		} else {
			char *selector = strstr(line, "[ALL]");

			if (!selector || sscanf(selector + 5, "[%u]", &group) != 1)
				return -EINVAL;
			if (!strcmp(band, "5G") && group < 3)
				out = power->track_5g[rfpath][direction][group];
			else if (!strcmp(band, "6G") && group < 4)
				out = power->track_6g[rfpath][direction][group];
			else
				return -EINVAL;
		}
		cursor++;
		while (count < 30) {
			char *number = strsep(&cursor, ",");
			int value, ret;

			if (!number)
				return -EINVAL;
			ret = kstrtoint(strim(number), 10, &value);
			if (ret)
				return ret;
			out[count++] = (s8)value;
		}
		if (cursor)
			return -EINVAL;
		power->track_rows++;
	}
	return power->track_rows ? 0 : -EINVAL;
}

int rtl_ax_power_inputs_load(struct device *dev, unsigned int chip,
			     struct rtl_ax_power_inputs *inputs)
{
	const char *family = chip == RTL_AX_8852C ? "rtl8852ce" : "rtl8192xbe";
	const char *fem = chip == RTL_AX_8852C ? "RTK66587_BPF" : "RTK66287";
	const char *names[] = {"TXPWR_ByRate.txt", "TXPWR_TrackTSSI.txt"};
	unsigned int stage, table;
	char name[128];

	for (stage = 0; stage < RTL_AX_PHY_STAGES; stage++) {
		if (stage == RTL_AX_PHY_FEM) {
			inputs->stage[stage] = inputs->stage[RTL_AX_PHY_BASE];
			inputs->stage[stage].rate_rows = 0;
			inputs->stage[stage].track_rows = 0;
		}
		for (table = 0; table < ARRAY_SIZE(names); table++) {
			const struct firmware *fw;
			char *text;
			int ret;

			snprintf(name, sizeof(name), "realtek/tyfza1/%s/RFE54/%s%s%s",
				 family, stage ? fem : "", stage ? "/" : "", names[table]);
			ret = request_firmware(&fw, name, dev);
			if (ret)
				return ret;
			text = kmemdup_nul(fw->data, fw->size, GFP_KERNEL);
			release_firmware(fw);
			if (!text)
				return -ENOMEM;
			ret = table ? rtl_ax_track_parse(text, &inputs->stage[stage]) :
				rtl_ax_rate_parse(text, &inputs->stage[stage]);
			kfree(text);
			if (ret)
				return dev_err_probe(dev, ret, "invalid stock power input %s\n", name);
		}
		{
			int ret = rtl_ax_limits_load(dev, chip, stage, &inputs->limits[stage]);

			if (ret)
				return ret;
		}
	}
	inputs->ready = true;
	return 0;
}

void rtl_ax_power_inputs_release(struct rtl_ax_power_inputs *inputs)
{
	unsigned int stage;

	for (stage = 0; stage < RTL_AX_PHY_STAGES; stage++)
		rtl_ax_limits_release(&inputs->limits[stage]);
	inputs->ready = false;
}

static u8 rtl_ax_half_dbm(int quarter_dbm, int max_dbm)
{
	/* Same signed /2 toward zero and7-bit TPU encoding as the original;
	 * cap requested TX power using the Linux channel/user limit. */
	return (min(quarter_dbm, max_dbm * 4) / 2) & 0x7f;
}

static int rtl_ax_channel_power(const struct rtl_ax_limits *limits,
	const struct rtl_ax_limit_key *key, const char *regulation, int *value)
{
	s8 limit;
	u8 shape;
	int ret = rtl_ax_limit_get(limits, key, regulation, &limit, &shape);

	if (ret == -ENOENT) {
		/* Original untouched limit slots use-128; its query maps to0. */
		*value = 0;
		return 0;
	}
	if (ret)
		return ret;
	*value = limit == -128 || limit == 127 ? 0 : limit;
	return 0;
}

int rtl_ax_power_apply20(const struct rtl_ax_io *io, unsigned int chip,
	const struct rtl_ax_power_inputs *inputs, unsigned int band, unsigned int channel,
	const char *country, int max_dbm, struct rtl_ax_power_hardware *state)
{
	const struct rtl_ax_power_stage *power = &inputs->stage[RTL_AX_PHY_FEM];
	const struct rtl_ax_limits *limits = &inputs->limits[RTL_AX_PHY_FEM];
	const char *regulation = "WW";
	unsigned int i, nss, bf;
	u32 offset_mode = 0;

	for (i = 0; i < limits->regulations; i++)
		if (!strncmp(limits->regulation[i], country, 2) && !limits->regulation[i][2])
			regulation = limits->regulation[i];
	strscpy(state->regulation, regulation, sizeof(state->regulation));
	for (i = 0; i < 12; i++)
		/* Actual get_power_by_rate fetches CCK from2G on a5G channel. */
		state->rate[i] = rtl_ax_half_dbm(power->rate[i < 4 ? 0 : band][i], max_dbm);
	for (nss = 0; nss < 2; nss++) {
		for (i = 0; i < 12; i++)
			state->rate[12 + nss * 16 + i] =
				rtl_ax_half_dbm(power->rate[band][84 + nss * 12 + i], max_dbm);
		for (i = 0; i < 4; i++)
			state->rate[24 + nss * 16 + i] =
				rtl_ax_half_dbm(power->rate[band][132 + nss * 4 + i], max_dbm);
	}
	for (i = 0; i < 5; i++)
		offset_mode |= ((power->rate[i == 4 ? 0 : band][140 + i] / 2) & 15) << (i * 4);
	memset(state->limit20, 0, sizeof(state->limit20));
	memset(state->ru20, 0, sizeof(state->ru20));
	for (nss = 0; nss < 2; nss++) {
		for (i = 0; i < 4; i++)
			for (bf = 0; bf < 2; bf++) {
				struct rtl_ax_limit_key key = { .band = band, .channel = channel,
					.ntx = nss, .bf = bf, .bandwidth = i == 1,
					.rate = i < 2 ? 0 : i == 2 ? 1 : 4 };
				int value, ret;

				ret = rtl_ax_channel_power(limits, &key, regulation, &value);
				if (ret)
					return ret;
				state->limit20[nss * 40 + i * 2 + bf] = rtl_ax_half_dbm(value, max_dbm);
			}
		for (i = 0; i < 3; i++) {
			struct rtl_ax_limit_key key = { .band = band, .channel = channel,
				.ntx = nss, .bandwidth = i, .rate = 4, .ru = 1 };
			int value, ret;

			ret = rtl_ax_channel_power(limits, &key, regulation, &value);
			if (ret)
				return ret;
			state->ru20[nss * 24 + i * 8] = rtl_ax_half_dbm(value, max_dbm);
		}
	}
	/* Actual TPU MAC register map:11 by-rate DWORDs,20 limits,12 RU.
	 * Both C andXB use SCC MAC band0 independent of physicalRF band. */
	for (i = 0; i < 43; i++) {
		const u8 *bytes;
		unsigned int address;
		u32 word;

		if (i < 11) { address = 0xd2c0 + i * 4; bytes = state->rate + i * 4; }
		else if (i < 31) { address = 0xd2ec + (i - 11) * 4; bytes = state->limit20 + (i - 11) * 4; }
		else { address = 0xd33c + (i - 31) * 4; bytes = state->ru20 + (i - 31) * 4; }
		word = bytes[0] | (u32)bytes[1] << 8 | (u32)bytes[2] << 16 | (u32)bytes[3] << 24;
		io->write32(io->ctx, address, word);
		state->readback[i] = io->read32(io->ctx, address);
	}
	rtl_ax_update32(io, 0xd204, 0x000fffff, offset_mode);
	rtl_ax_tx_shape_zero(io, chip);
	state->applied = true;
	return 0;
}
