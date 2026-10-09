// SPDX-License-Identifier: GPL-2.0-only
/* Native storage of actual TYFZA1 power limit inputs. No RF write occurs here. */
#include <linux/ctype.h>
#include <linux/device.h>
#include <linux/firmware.h>
#include <linux/slab.h>
#include <linux/string.h>
#include "phy.h"
#include "limits.h"

static char *rtl_ax_limit_token(char **text)
{
	char *token;

	while ((token = strsep(text, " \t\r")))
		if (*token)
			return token;
	return NULL;
}

static int rtl_ax_limit_name(const char *name, const char *const *names, unsigned int count)
{
	unsigned int i;

	for (i = 0; i < count; i++)
		if (!strcmp(name, names[i]))
			return i;
	return -EINVAL;
}

static int rtl_ax_limit_regulation(struct rtl_ax_limits *limits, const char *name)
{
	unsigned int i;

	for (i = 0; i < limits->regulations; i++)
		if (!strcmp(name, limits->regulation[i]))
			return i;
	if (i == ARRAY_SIZE(limits->regulation) || strlen(name) >= sizeof(limits->regulation[0]))
		return -EINVAL;
	strscpy(limits->regulation[i], name, sizeof(limits->regulation[0]));
	limits->regulations++;
	return i;
}

static int rtl_ax_limit_decimal(char *text, s8 *power)
{
	char *fraction;
	unsigned int integer, hundredths = 0;
	bool negative = *text == '-';
	int ret;

	/* Actual _hal_parse_txpwrlmt: NA=63,WW=-63;numeric values use
	 * integer*4+floor(hundredths*4/100),then apply the sign. */
	if (!strcmp(text, "NA")) { *power = 63; return 0; }
	if (!strcmp(text, "WW")) { *power = -63; return 0; }
	if (*text == '+' || *text == '-')
		text++;
	fraction = strchr(text, '.');
	if (fraction) {
		*fraction++ = '\0';
		ret = kstrtouint(fraction, 10, &hundredths);
		if (ret)
			return ret;
		if (strlen(fraction) == 1)
			hundredths *= 10;
	}
	ret = kstrtouint(text, 10, &integer);
	if (ret)
		return ret;
	*power = (s8)((u8)integer * 4 + hundredths * 4 / 100);
	if (negative)
		*power = -*power;
	return 0;
}

static int rtl_ax_limit_header(char *text, bool ru, struct rtl_ax_limit_key *key)
{
	static const char *const bands[] = {"2.4G", "5G", "6G"};
	static const char *const bandwidths[] = {"20M", "40M", "80M", "160M"};
	static const char *const rus[] = {"RU26", "RU52", "RU106"};
	static const char *const rates[] = {"CCK", "OFDM", "HT", "VHT", "HE"};
	char *fields[5], *comment = strstr(text, "//");
	unsigned int i, ntx;
	int index;

	if (comment)
		*comment = '\0';
	for (i = 0; i < (ru ? 4 : 5); i++) {
		fields[i] = strsep(&text, ",");
		if (!fields[i])
			return -EINVAL;
		fields[i] = strim(fields[i]);
	}
	index = rtl_ax_limit_name(fields[0], bands, ARRAY_SIZE(bands));
	if (index < 0)
		return index;
	key->band = index;
	index = rtl_ax_limit_name(fields[1], ru ? rus : bandwidths, ru ? ARRAY_SIZE(rus) : ARRAY_SIZE(bandwidths));
	if (index < 0)
		return index;
	key->bandwidth = index;
	if (sscanf(fields[2], "%uT", &ntx) != 1 || ntx < 1 || ntx > 2)
		return -EINVAL;
	key->ntx = ntx - 1;
	index = rtl_ax_limit_name(fields[3], rates, ARRAY_SIZE(rates));
	if (index < 0)
		return index;
	key->rate = index;
	key->bf = !ru && !strcmp(fields[4], "BF");
	return 0;
}

static int rtl_ax_limit_exception(char *text, struct rtl_ax_limits *limits)
{
	char country[3] = {}, *regulation = strstr(text, "Regulation=");
	unsigned int domain = 0, i;
	int index;

	if (!regulation)
		return -EINVAL;
	if (sscanf(text, "@@CountryCode=%2c", country) != 1 &&
	    sscanf(text, "@@DomainCode=%x", &domain) != 1)
		return -EINVAL;
	index = rtl_ax_limit_regulation(limits, strim(regulation + 11));
	if (index < 0)
		return index;
	for (i = 0; i < limits->exceptions; i++)
		if (!strcmp(country, limits->exception[i].country) && domain == limits->exception[i].domain)
			break;
	if (i == ARRAY_SIZE(limits->exception))
		return -EINVAL;
	strscpy(limits->exception[i].country, country, sizeof(country));
	limits->exception[i].domain = domain;
	limits->exception[i].regulation = index;
	if (i == limits->exceptions)
		limits->exceptions++;
	return 0;
}

static int rtl_ax_limit_parse(char *text, struct rtl_ax_limits *limits, bool ru,
			      unsigned int mode, struct rtl_ax_limit_entry *out)
{
	struct rtl_ax_limit_key key = { .mode = mode, .ru = ru };
	unsigned int column_count = 0, rows = 0, column, channel;
	u8 regulations[32], shapes[32] = {};
	char *line;
	bool header = false, labels = false;

	while ((line = strsep(&text, "\n"))) {
		char *cursor, *token;
		int ret;

		line = strim(line);
		if (!*line || !strncmp(line, "//", 2) || !strncmp(line, "@@Ver=", 6))
			continue;
		if (!strncmp(line, "@@", 2)) {
			ret = rtl_ax_limit_exception(line, limits);
			if (ret)
				return ret;
			continue;
		}
		if (!strncmp(line, "##", 2)) {
			cursor = (char *)skip_spaces(line + 2);
			if (strchr(cursor, ',')) {
				ret = rtl_ax_limit_header(cursor, ru, &key);
				if (ret)
					return ret;
				header = true;
				labels = false;
				column_count = 0;
			} else if (!strcmp(cursor, "START")) {
				continue;
			} else if (!strcmp(cursor, "END")) {
				header = false;
			} else if (sscanf(cursor, "#%u#", &column_count) == 1) {
				if (!column_count || column_count > ARRAY_SIZE(regulations))
					return -EINVAL;
			} else {
				for (column = 0; column < column_count; column++) {
					token = rtl_ax_limit_token(&cursor);
					if (!token)
						return -EINVAL;
					ret = rtl_ax_limit_regulation(limits, token);
					if (ret < 0)
						return ret;
					regulations[column] = ret;
				}
				labels = true;
			}
			continue;
		}
		if (!header || !labels)
			return -EINVAL;
		cursor = line;
		token = rtl_ax_limit_token(&cursor);
		if (!strcmp(token, "PShape")) {
			for (column = 0; column < column_count; column++) {
				token = rtl_ax_limit_token(&cursor);
				if (!token || kstrtou8(token, 10, &shapes[column]))
					return -EINVAL;
			}
			continue;
		}
		if (sscanf(token, "CH%u", &channel) != 1 || channel > 255)
			return -EINVAL;
		key.channel = channel;
		for (column = 0; column < column_count; column++) {
			s8 power;

			token = rtl_ax_limit_token(&cursor);
			if (!token)
				return -EINVAL;
			ret = rtl_ax_limit_decimal(token, &power);
			if (ret)
				return ret;
			if (out)
				out[rows] = (struct rtl_ax_limit_entry) {
					.key = key, .regulation = regulations[column],
					.shape = shapes[column], .power = power,
				};
			rows++;
		}
	}
	return header || !rows ? -EINVAL : rows;
}

static int rtl_ax_limit_file(struct device *dev, const char *name,
			     struct rtl_ax_limits *limits, bool ru, unsigned int mode)
{
	const struct firmware *fw;
	struct rtl_ax_limit_entry *entries;
	char *text;
	int ret, count;

	ret = request_firmware(&fw, name, dev);
	if (ret)
		return ret;
	text = kmemdup_nul(fw->data, fw->size, GFP_KERNEL);
	if (!text) { ret = -ENOMEM; goto release; }
	count = rtl_ax_limit_parse(text, limits, ru, mode, NULL);
	kfree(text);
	if (count < 0) { ret = count; goto release; }
	entries = kvmalloc_array(limits->count + count, sizeof(*entries), GFP_KERNEL);
	if (!entries) { ret = -ENOMEM; goto release; }
	if (limits->count)
		memcpy(entries, limits->entries, limits->count * sizeof(*entries));
	text = kmemdup_nul(fw->data, fw->size, GFP_KERNEL);
	if (!text) { kvfree(entries); ret = -ENOMEM; goto release; }
	ret = rtl_ax_limit_parse(text, limits, ru, mode, entries + limits->count);
	kfree(text);
	if (ret < 0) { kvfree(entries); goto release; }
	kvfree(limits->entries);
	limits->entries = entries;
	limits->count += count;
	if (ru)
		limits->ru_count += count;
	else
		limits->ordinary_count += count;
	limits->files++;
	ret = 0;
release:
	release_firmware(fw);
	if (ret)
		dev_err(dev, "power limit input %s failed: %d\n", name, ret);
	return ret;
}

void rtl_ax_limits_release(struct rtl_ax_limits *limits)
{
	kvfree(limits->entries);
	*limits = (struct rtl_ax_limits) {};
}

int rtl_ax_limits_load(struct device *dev, unsigned int chip, unsigned int stage,
		       struct rtl_ax_limits *limits)
{
	const char *family = chip == RTL_AX_8852C ? "rtl8852ce" : "rtl8192xbe";
	const char *fem = chip == RTL_AX_8852C ? "RTK66587_BPF" : "RTK66287";
	const char *const names[] = {"TXPWR_LMT.txt", "TXPWR_LMT_RU.txt",
		"TXPWR_LMT_6G_LPI.txt", "TXPWR_LMT_RU_6G_LPI.txt",
		"TXPWR_LMT_6G_STD.txt", "TXPWR_LMT_RU_6G_STD.txt",
		"TXPWR_LMT_6G_VLP.txt", "TXPWR_LMT_RU_6G_VLP.txt"};
	unsigned int table, count = chip == RTL_AX_8852C ? (stage ? 6 : 8) : 2;
	char name[160];
	int ret;

	for (table = 0; table < count; table++) {
		const char *file = stage && table == 2 ? "TXPWR_LMT_6G_LOW.txt" :
			stage && table == 3 ? "TXPWR_LMT_RU_6G_LOW.txt" : names[table];

		snprintf(name, sizeof(name), "realtek/tyfza1/%s/RFE54/%s%s%s",
			 family, stage ? fem : "", stage ? "/" : "", file);
		ret = rtl_ax_limit_file(dev, name, limits, table & 1, table / 2);
		if (ret)
			return ret;
	}
	return 0;
}

int rtl_ax_limit_get(const struct rtl_ax_limits *limits, const struct rtl_ax_limit_key *key,
		     const char *regulation, s8 *power, u8 *shape)
{
	unsigned int i;

	if (!strcmp(regulation, "WW")) {
		s8 lowest = 127;
		u32 seen = 0;

		/* Actual set_worldwide takes each region's final record minimum,
		 * skips default-128 and maps an entirely absent slot to0. */
		for (i = limits->count; i; i--) {
			const struct rtl_ax_limit_entry *entry = &limits->entries[i - 1];

			if (memcmp(&entry->key, key, sizeof(*key)) || (seen & BIT(entry->regulation)))
				continue;
			seen |= BIT(entry->regulation);
			if (entry->power != -128 && entry->power < lowest)
				lowest = entry->power;
		}
		*power = lowest == 127 ? 0 : lowest;
		*shape = 0;
		return 0;
	}
	/* Original store assigns the record value;the last matching row wins. */
	for (i = limits->count; i; i--) {
		const struct rtl_ax_limit_entry *entry = &limits->entries[i - 1];

		if (!memcmp(&entry->key, key, sizeof(*key)) &&
		    !strcmp(limits->regulation[entry->regulation], regulation)) {
			*power = entry->power;
			*shape = entry->shape;
			return 0;
		}
	}
	return -ENOENT;
}
