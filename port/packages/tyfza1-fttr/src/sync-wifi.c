// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "sync-wifi.h"

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
	struct json_tokener *parser = NULL;
	struct json_object *value = NULL;
	struct stat st;
	char data[1536];
	size_t used = 0;
	ssize_t n;
	int fd, ret = -EINVAL;

	*desired = NULL;
	fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0)
		return -errno;
	if (fstat(fd, &st)) { ret = -errno; goto out; }
	if (!S_ISREG(st.st_mode) || (st.st_mode & 077) || st.st_uid != geteuid()) { ret = -EACCES; goto out; }
	for (;;) {
		n = read(fd, data + used, sizeof(data) - used);
		if (n < 0) { if (errno == EINTR) continue; ret = -errno; goto out; }
		if (!n)
			break;
		used += n;
		if (used == sizeof(data)) { ret = -E2BIG; goto out; }
	}
	parser = json_tokener_new();
	if (!parser) { ret = -ENOMEM; goto out; }
	json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
	value = json_tokener_parse_ex(parser, data, used);
	if (json_tokener_get_error(parser) != json_tokener_success || json_tokener_get_parse_end(parser) != used)
		goto out;
	ret = fttr_sync_wifi_validate(value);
	if (!ret) { *desired = value; value = NULL; }
out:
	json_object_put(value);
	if (parser)
		json_tokener_free(parser);
	close(fd);
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
