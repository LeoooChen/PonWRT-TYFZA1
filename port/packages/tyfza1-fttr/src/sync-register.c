// SPDX-License-Identifier: GPL-2.0-only
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "sync-register.h"

static const char *text(struct json_object *object, const char *key, size_t max)
{
	struct json_object *value;
	const char *s;
	size_t i, length;

	if (!json_object_object_get_ex(object, key, &value) || !json_object_is_type(value, json_type_string))
		return NULL;
	s = json_object_get_string(value);
	length = json_object_get_string_len(value);
	if (!length || length > max || strlen(s) != length)
		return NULL;
	for (i = 0; i < length; i++)
		if ((unsigned char)s[i] < 32 || (unsigned char)s[i] == 127)
			return NULL;
	return s;
}

int fttr_sync_register_policy(struct json_object *policy)
{
	struct json_object *ready, *optional;
	const char *oui;
	size_t i, expected = 4;

	if (!json_object_is_type(policy, json_type_object))
		return -EINVAL;
	oui = text(policy, "OUI", 6);
	if (!oui || strlen(oui) != 6 || !text(policy, "SN", 63) || !text(policy, "ExpectedSN", 63) ||
	    !json_object_object_get_ex(policy, "Ready", &ready) || !json_object_is_type(ready, json_type_int) ||
	    (json_object_get_int64(ready) != 0 && json_object_get_int64(ready) != 1))
		return -EINVAL;
	for (i = 0; i < 6; i++)
		if (!isxdigit((unsigned char)oui[i]))
			return -EINVAL;
	if (json_object_object_get_ex(policy, "ExpectedGPONSN", &optional)) {
		expected++;
		if (!text(policy, "ExpectedGPONSN", 16))
			return -EINVAL;
	}
	return (size_t)json_object_object_length(policy) == expected ? 0 : -EINVAL;
}

int fttr_sync_register_reply(const struct fttr_sync_message *message,
	struct json_object *policy, const uint8_t source[6], struct json_object **reply)
{
	const char *names[] = { "DevName", "LOID", "Manufacturer", "ModelName", "OUI", "SN", "GPONSN" };
	const size_t limits[] = { 127, 63, 63, 63, 11, 63, 16 };
	struct json_tokener *parser;
	struct json_object *value = NULL, *field, *result;
	char mac[13];
	const char *s, *pin;
	size_t i;
	int ret = -EBADMSG;

	*reply = NULL;
	if (fttr_sync_register_policy(policy))
		return -EINVAL;
	parser = json_tokener_new();
	if (!parser)
		return -ENOMEM;
	json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
	value = json_tokener_parse_ex(parser, (const char *)message->payload, message->length);
	if (json_tokener_get_error(parser) != json_tokener_success || json_tokener_get_parse_end(parser) != message->length ||
	    !json_object_is_type(value, json_type_object))
		goto out;
	s = text(value, "RPCMethod", 32);
	if (!s || strcmp(s, "Register")) { ret = -EAGAIN; goto out; }
	for (i = 0; i < sizeof(names) / sizeof(*names); i++)
		if (json_object_object_get_ex(value, names[i], &field)) {
			if (!json_object_is_type(field, json_type_string))
				goto out;
			if (json_object_get_string_len(field) && !text(value, names[i], limits[i]))
				goto out;
		}
	s = text(value, "SN", 63);
	if (!s)
		goto out;
	if (strcmp(s, text(policy, "ExpectedSN", 63))) { ret = -ESTALE; goto out; }
	pin = text(policy, "ExpectedGPONSN", 16);
	if (pin) {
		s = text(value, "GPONSN", 16);
		if (!s || strcmp(s, pin)) { ret = -ESTALE; goto out; }
	}
	snprintf(mac, sizeof(mac), "%02X%02X%02X%02X%02X%02X",
		source[0], source[1], source[2], source[3], source[4], source[5]);
	result = json_object_new_object();
	json_object_object_add(result, "Result", json_object_new_int(1));
	json_object_object_add(result, "MAC", json_object_new_string(mac));
	json_object_object_add(result, "OUI", json_object_new_string(text(policy, "OUI", 6)));
	json_object_object_add(result, "SN", json_object_new_string(text(policy, "SN", 63)));
	json_object_object_get_ex(policy, "Ready", &field);
	json_object_object_add(result, "Ready", json_object_get(field));
	*reply = result;
	ret = 0;
out:
	json_object_put(value);
	json_tokener_free(parser);
	return ret;
}
