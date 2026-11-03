// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <uci.h>

#include "sync-controller.h"
#include "sync-json.h"
#include "sync-wifi.h"

static const char *text(struct json_object *object, const char *key)
{
	struct json_object *value;
	const char *s;

	if (!json_object_object_get_ex(object, key, &value) || !json_object_is_type(value, json_type_string))
		return NULL;
	s = json_object_get_string(value);
	return strlen(s) == (size_t)json_object_get_string_len(value) ? s : NULL;
}

static const char *option(struct uci_context *ctx, struct uci_section *section, const char *name)
{
	return uci_lookup_option_string(ctx, section, name);
}

static int boolean(struct uci_context *ctx, struct uci_section *section, const char *name)
{
	struct uci_option *value = uci_lookup_option(ctx, section, name);
	const char *s;

	if (!value)
		return 0;
	if (value->type != UCI_TYPE_STRING)
		return -EINVAL;
	s = value->v.string;
	if (!strcmp(s, "1") || !strcmp(s, "true") || !strcmp(s, "yes") || !strcmp(s, "on"))
		return 1;
	if (!strcmp(s, "0") || !strcmp(s, "false") || !strcmp(s, "no") || !strcmp(s, "off"))
		return 0;
	return -EINVAL;
}

static int row(struct uci_context *ctx, struct uci_package *wireless,
		struct json_object *binding, struct json_object **result)
{
	const char *section, *index, *standard, *device, *band, *ssid, *key, *encryption, *mode;
	const char *names[] = { "none", "psk", "psk2", "psk-mixed", "sae", "sae-mixed" };
	const int codes[] = { 0, 2, 4, 6, 16, 20 };
	/* Flag1 has no representation of these independent authentication fields.
	 * Do not silently translate them into a single shared password. */
	const char *unsupported[] = { "sae_password", "sae_password_file", "sae_pwe", "sae_groups",
		"wpa_psk_file", "wpa_psk_radius", "auth_server", "auth_secret", "auth_type",
		"owe_transition_ifname", "ieee80211w", "ieee80211r", "mobility_domain", "wps_pushbutton" };
	struct uci_section *vif, *radio;
	struct json_object *value;
	int disabled, radio_disabled, hidden, code = -1, target;
	size_t i;

	*result = NULL;
	if (!json_object_is_type(binding, json_type_object) || json_object_object_length(binding) != 3)
		return -EINVAL;
	section = text(binding, "Section");
	index = text(binding, "SSIDIndex");
	standard = text(binding, "Standard");
	if (!section || !*section || !index || !*index || !standard)
		return -EINVAL;
	/* Only stable named UCI sections, never positional @wifi-iface[n]. */
	vif = uci_lookup_section(ctx, wireless, section);
	if (!vif || vif->anonymous || strcmp(vif->type, "wifi-iface"))
		return -ENOENT;
	mode = option(ctx, vif, "mode");
	device = option(ctx, vif, "device");
	if (!mode || strcmp(mode, "ap") || !device)
		return -EINVAL;
	radio = uci_lookup_section(ctx, wireless, device);
	if (!radio || strcmp(radio->type, "wifi-device"))
		return -ENOENT;
	band = option(ctx, radio, "band");
	if (!band || (strcmp(band, "2g") && strcmp(band, "5g")))
		return -EOPNOTSUPP;
	if (strlen(index) > 2 || index[0] < '1' || index[0] > '9' ||
	    (index[1] && (index[1] < '0' || index[1] > '9')))
		return -EINVAL;
	target = index[0] - '0';
	if (index[1])
		target = target * 10 + index[1] - '0';
	if (target > 16 || strcmp(band, target <= 8 ? "2g" : "5g"))
		return -EINVAL;
	for (i = 0; i < sizeof(unsupported) / sizeof(*unsupported); i++)
		if (uci_lookup_option(ctx, vif, unsupported[i]))
			return -EOPNOTSUPP;
	encryption = option(ctx, vif, "encryption");
	for (i = 0; encryption && i < sizeof(codes) / sizeof(*codes); i++)
		if (!strcmp(encryption, names[i]))
			code = codes[i];
	if (code < 0)
		return -EOPNOTSUPP;
	ssid = option(ctx, vif, "ssid");
	key = option(ctx, vif, "key");
	if (!ssid || (!key && code) || (!code && uci_lookup_option(ctx, vif, "key")))
		return -EINVAL;
	disabled = boolean(ctx, vif, "disabled");
	radio_disabled = boolean(ctx, radio, "disabled");
	hidden = boolean(ctx, vif, "hidden");
	if (disabled < 0 || radio_disabled < 0 || hidden < 0)
		return -EINVAL;
	value = json_object_new_object();
	json_object_object_add(value, "act", json_object_new_string("config"));
	json_object_object_add(value, "SSIDIndex", json_object_new_string(index));
	json_object_object_add(value, "Standard", json_object_new_string(standard));
	json_object_object_add(value, "SSID", json_object_new_string(ssid));
	json_object_object_add(value, "PWD", json_object_new_string(key ? key : ""));
	json_object_object_add(value, "ENCRYPT", json_object_new_int(code));
	json_object_object_add(value, "Enable", json_object_new_int(!disabled && !radio_disabled));
	json_object_object_add(value, "SSIDAdvertisementEnabled", json_object_new_string(hidden ? "false" : "true"));
	if (fttr_sync_wifi_validate(value)) {
		json_object_put(value);
		return -EINVAL;
	}
	*result = value;
	return 0;
}

static int convert(struct uci_context *ctx, struct uci_package *wireless, struct json_object *policy)
{
	struct json_object *peers, *peer, *bindings, *wifi, *value;
	size_t i, j;
	int ret;

	if (json_object_object_length(policy) != 1 || !json_object_object_get_ex(policy, "Peers", &peers) ||
	    !json_object_is_type(peers, json_type_array) || !json_object_array_length(peers) || json_object_array_length(peers) > 16)
		return -EINVAL;
	for (i = 0; i < json_object_array_length(peers); i++) {
		peer = json_object_array_get_idx(peers, i);
		if (!json_object_is_type(peer, json_type_object) || json_object_object_length(peer) != 3 ||
		    !json_object_object_get_ex(peer, "WiFiFromUCI", &bindings) || !json_object_is_type(bindings, json_type_array) ||
		    !json_object_array_length(bindings) || json_object_array_length(bindings) > 16 ||
		    json_object_object_get_ex(peer, "WiFi", &value))
			return -EINVAL;
		wifi = json_object_new_array();
		for (j = 0; j < json_object_array_length(bindings); j++) {
			ret = row(ctx, wireless, json_object_array_get_idx(bindings, j), &value);
			if (ret) { json_object_put(wifi); return ret; }
			json_object_array_add(wifi, value);
		}
		json_object_object_del(peer, "WiFiFromUCI");
		json_object_object_add(peer, "WiFi", wifi);
	}
	return fttr_sync_controller_policy(policy);
}

int main(int argc, char **argv)
{
	struct uci_context *ctx = NULL;
	struct uci_package *wireless = NULL;
	struct json_object *policy = NULL;
	struct json_object *roundtrip = NULL;
	struct json_tokener *parser = NULL;
	struct stat st;
	FILE *file = NULL;
	const char *data;
	char *temporary = NULL;
	size_t length, written = 0;
	ssize_t n;
	int fd = -1, output = -1, ret = -EINVAL;

	if (argc != 4) {
		fprintf(stderr, "Usage: %s <private-wireless-file> <private-binding-json> <new-private-output>\n", argv[0]);
		return 2;
	}
	ret = fttr_sync_json_load_bounded(argv[2], 131072, &policy);
	if (ret)
		goto out;
	fd = open(argv[1], O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0) { ret = -errno; goto out; }
	if (fstat(fd, &st)) { ret = -errno; goto out; }
	if (!S_ISREG(st.st_mode) || (st.st_mode & 077) || st.st_uid != geteuid()) { ret = -EACCES; goto out; }
	if (st.st_size > 131072) { ret = -E2BIG; goto out; }
	file = fdopen(fd, "r");
	if (!file) { ret = -errno; goto out; }
	fd = -1;
	ctx = uci_alloc_context();
	if (!ctx) { ret = -ENOMEM; goto out; }
	ctx->flags &= ~UCI_FLAG_PERROR;
	/* Import this file only. No /tmp/.uci deltas or implicit /etc/config reads. */
	if (uci_import(ctx, file, "wireless", &wireless, true)) { ret = -EINVAL; goto out; }
	ret = convert(ctx, wireless, policy);
	if (ret)
		goto out;
	data = json_object_to_json_string_ext(policy, JSON_C_TO_STRING_PLAIN);
	length = strlen(data);
	if (length >= 131072) { ret = -E2BIG; goto out; }
	/* UCI permits arbitrary bytes; the native syncserv reader requires UTF-8. */
	parser = json_tokener_new();
	if (!parser) { ret = -ENOMEM; goto out; }
	json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
	roundtrip = json_tokener_parse_ex(parser, data, length);
	if (json_tokener_get_error(parser) != json_tokener_success || json_tokener_get_parse_end(parser) != length) {
		ret = -EILSEQ;
		goto out;
	}
	/* Publish only a complete validated snapshot to an absent path. Never
	 * overwrite a previous policy or follow a symlink. No keys on stdout. */
	temporary = malloc(strlen(argv[3]) + 8);
	if (!temporary) { ret = -ENOMEM; goto out; }
	sprintf(temporary, "%s.XXXXXX", argv[3]);
	output = mkstemp(temporary);
	if (output < 0) { ret = -errno; goto out; }
	while (written < length) {
		n = write(output, data + written, length - written);
		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0) { ret = n < 0 ? -errno : -EIO; goto out; }
		written += n;
	}
	if (fsync(output)) { ret = -errno; goto out; }
	if (link(temporary, argv[3])) { ret = -errno; goto out; }
	ret = 0;
out:
	if (output >= 0) {
		close(output);
		unlink(temporary);
	}
	free(temporary);
	if (file) fclose(file);
	if (fd >= 0) close(fd);
	if (ctx) uci_free_context(ctx);
	if (parser) json_tokener_free(parser);
	json_object_put(roundtrip);
	json_object_put(policy);
	if (ret)
		fprintf(stderr, "FTTR Wi-Fi policy: %s (%d); no configuration values printed\n", strerror(-ret), ret);
	return ret ? 1 : 0;
}
