// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <stdbool.h>
#include <string.h>

#include "sync-wifi.h"
#include "sync-json.h"

static const char *string(struct json_object *object, const char *key)
{
	struct json_object *value;
	const char *text;

	if (!json_object_is_type(object, json_type_object) ||
	    !json_object_object_get_ex(object, key, &value) || !json_object_is_type(value, json_type_string))
		return NULL;
	text = json_object_get_string(value);
	return strlen(text) == (size_t)json_object_get_string_len(value) ? text : NULL;
}

static int index_of(struct json_object *object)
{
	const char *text = string(object, "SSIDIndex");
	int index;

	if (!text || !*text || text[0] < '1' || text[0] > '9' || strlen(text) > 2)
		return -EINVAL;
	index = text[0] - '0';
	if (text[1]) {
		if (text[1] < '0' || text[1] > '9')
			return -EINVAL;
		index = index * 10 + text[1] - '0';
	}
	return index <= 16 ? index : -EINVAL;
}

static bool standard_valid(const char *text, int index)
{
	const char *low[] = { "11b", "11g", "11bg", "11gn", "11bgn", "11ax" };
	const char *high[] = { "11a", "11ac", "11na", "11ax" };
	const char **list = index <= 8 ? low : high;
	size_t i, count = index <= 8 ? sizeof(low) / sizeof(*low) : sizeof(high) / sizeof(*high);

	for (i = 0; text && i < count; i++)
		if (!strcmp(text, list[i]))
			return true;
	return false;
}

/* The original read producer uses UI codes, while writes use raw MIB codes. */
static int read_encryption(const char *text)
{
	const char *codes[] = { "1", "2", "3", "4", "5", "9", "10" };
	const int raw[] = { 0, 1, 2, 4, 6, 16, 20 };
	size_t i;

	for (i = 0; text && i < sizeof(raw) / sizeof(*raw); i++)
		if (!strcmp(text, codes[i]))
			return raw[i];
	return -EINVAL;
}

int fttr_sync_wifi_validate(struct json_object *desired)
{
	struct json_object *value;
	const char *ssid, *key, *advertisement, *action;
	int index = index_of(desired), encryption;
	size_t length;

	if (!json_object_is_type(desired, json_type_object) || json_object_object_length(desired) != 8 || index < 0)
		return -EINVAL;
	action = string(desired, "act");
	ssid = string(desired, "SSID");
	key = string(desired, "PWD");
	advertisement = string(desired, "SSIDAdvertisementEnabled");
	if (!action || strcmp(action, "config") || !ssid || !*ssid || strlen(ssid) > 32 || !key ||
	    !advertisement || (strcmp(advertisement, "true") && strcmp(advertisement, "false")) ||
	    !standard_valid(string(desired, "Standard"), index))
		return -EINVAL;
	if (!json_object_object_get_ex(desired, "Enable", &value) || !json_object_is_type(value, json_type_int) ||
	    (json_object_get_int64(value) != 0 && json_object_get_int64(value) != 1))
		return -EINVAL;
	if (!json_object_object_get_ex(desired, "ENCRYPT", &value) || !json_object_is_type(value, json_type_int))
		return -EINVAL;
	switch (json_object_get_int64(value)) {
	case 0: case 1: case 2: case 4: case 6: case 16: case 20:
		encryption = json_object_get_int(value);
		break;
	default:
		return -EINVAL;
	}
	length = strlen(key);
	if ((!encryption && length) || (encryption == 1 && length != 5 && length != 13) ||
	    (encryption > 1 && (length < 8 || length > 63)))
		return -EINVAL;
	return 0;
}

int fttr_sync_wifi_load(const char *path, struct json_object **desired)
{
	int ret = fttr_sync_json_load_private(path, desired);

	if (!ret)
		ret = fttr_sync_wifi_validate(*desired);
	if (ret) {
		json_object_put(*desired);
		*desired = NULL;
	}
	return ret;
}

int fttr_sync_wifi_check(struct json_object *entries, struct json_object *desired, bool compare)
{
	struct json_object *row = NULL, *value;
	unsigned int seen = 0;
	size_t i;
	int target = index_of(desired), encryption;
	const char *ssid, *enable, *radio, *advertisement, *standard;

	if (fttr_sync_wifi_validate(desired) || !json_object_is_type(entries, json_type_array) ||
	    json_object_array_length(entries) > 16)
		return -EINVAL;
	for (i = 0; i < json_object_array_length(entries); i++) {
		struct json_object *entry = json_object_array_get_idx(entries, i);
		int index = index_of(entry);

		if (index < 0 || (seen & (1u << index)))
			return -EBADMSG;
		seen |= 1u << index;
		if (index == target)
			row = entry;
	}
	if (!row)
		return -ENOENT;
	ssid = string(row, "SSID");
	enable = string(row, "Enable");
	radio = string(row, "RadioType");
	advertisement = string(row, "SSIDAdvertisementEnabled");
	standard = string(row, "Standard");
	encryption = read_encryption(string(row, "ENCRYPT"));
	if (!ssid || strlen(ssid) > 32 || !enable || (strcmp(enable, "0") && strcmp(enable, "1")) ||
	    !radio || strcmp(radio, target <= 8 ? "2.4G" : "5G") || encryption < 0 ||
	    !advertisement || (strcmp(advertisement, "true") && strcmp(advertisement, "false")) ||
	    !standard_valid(standard, target))
		return -EBADMSG;
	if (!compare)
		return 0;
	json_object_object_get_ex(desired, "ENCRYPT", &value);
	if (json_object_get_int(value) != encryption)
		return -EUCLEAN;
	json_object_object_get_ex(desired, "Enable", &value);
	return strcmp(ssid, string(desired, "SSID")) || enable[0] - '0' != json_object_get_int(value) ||
		strcmp(standard, string(desired, "Standard")) ||
		strcmp(advertisement, string(desired, "SSIDAdvertisementEnabled")) ? -EUCLEAN : 0;
}

int fttr_sync_radio_validate(struct json_object *desired)
{
	const char *radio=string(desired,"RadioType"), *enable=string(desired,"Enable");
	const char *power=string(desired,"TransmitPower"), *width=string(desired,"FrequencyWidth");
	const char *channel=string(desired,"Channel");
	unsigned int chan=0;
	bool low=radio && !strcmp(radio,"2.4G");
	if (!json_object_is_type(desired,json_type_object) || json_object_object_length(desired)!=6 ||
	    !radio || (!low && strcmp(radio,"5G")) || !enable || (strcmp(enable,"0") && strcmp(enable,"1")) ||
	    !power || (strcmp(power,"100") && strcmp(power,"80") && strcmp(power,"60") && strcmp(power,"40") && strcmp(power,"20")) ||
	    !standard_valid(string(desired,"Standard"),low?1:9) || !width ||
	    (strcmp(width,"20M") && strcmp(width,"40M") && strcmp(width,"Auto20M40M") &&
	     (low || (strcmp(width,"Auto20M40M80M") && strcmp(width,"Auto20M40M80M160M")))) ||
	    !channel || !*channel || strlen(channel)>3 || (channel[0]=='0' && channel[1])) return -EINVAL;
	for (unsigned int i=0;channel[i];i++) {
		if (channel[i]<'0' || channel[i]>'9') return -EINVAL;
		chan=chan*10+channel[i]-'0';
	}
	/* Zero selects automatic channel. Actual regulatory/hardware acceptance
	 * belongs to the remote radio; these are only the original wire bounds. */
	return chan>255 || (chan && (low?chan>14:chan<32)) ? -EINVAL : 0;
}

int fttr_sync_radio_check(struct json_object *entries, struct json_object *desired, bool compare)
{
	struct json_object *selected=NULL;
	unsigned int seen=0;
	const char *band=string(desired,"RadioType");
	if (fttr_sync_radio_validate(desired) || !json_object_is_type(entries,json_type_array)) return -EINVAL;
	for (size_t i=0;i<json_object_array_length(entries);i++) {
		struct json_object *row=json_object_array_get_idx(entries,i);
		const char *type=string(row,"RadioType");
		unsigned bit=type&&!strcmp(type,"2.4G")?1:type&&!strcmp(type,"5G")?2:0;
		if (!bit || (seen&bit)) return -EBADMSG;
		seen|=bit;
		if (!strcmp(type,band)) selected=row;
	}
	if (!selected) return -ENODEV;
	json_object_object_foreach(desired,key,want) {
		const char *have=string(selected,key);
		if (!have) return -ENODATA;
		if (!compare) continue;
		if (!strcmp(key,"Channel") && !strcmp(json_object_get_string(want),"0")) {
			const char *automatic=string(selected,"AutoChannelEnable");
			if (!automatic) return -ENODATA;
			if (strcmp(automatic,"1")) return -EUCLEAN;
		} else {
			if (strcmp(have,json_object_get_string(want))) return -EUCLEAN;
			if (!strcmp(key,"Channel")) {
				const char *automatic=string(selected,"AutoChannelEnable");
				if (!automatic) return -ENODATA;
				if (strcmp(automatic,"0")) return -EUCLEAN;
			}
		}
	}
	return 0;
}
