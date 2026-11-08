// SPDX-License-Identifier: GPL-2.0-only
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#include "sync-client.h"
#include "sync-peer.h"

int fttr_sync_mac_parse(const char *text, uint8_t mac[6])
{
	unsigned int i, nonzero = 0;

	if (strlen(text) != 17)
		return -EINVAL;
	for (i = 0; i < 6; i++) {
		char byte[3] = { text[i * 3], text[i * 3 + 1], 0 }, *end;
		unsigned long value = strtoul(byte, &end, 16);

		if (end != byte + 2 || value > 255 || (i < 5 && text[i * 3 + 2] != ':') ||
		    !isxdigit((unsigned char)byte[0]) || !isxdigit((unsigned char)byte[1]))
			return -EINVAL;
		mac[i] = value;
		nonzero |= value;
	}
	return !nonzero || (mac[0] & 1) ? -EINVAL : 0;
}

int fttr_sync_info_mesh(struct json_object *info, bool *enabled)
{
	struct json_object *field;
	if (!info || !json_object_object_get_ex(info, "easymeshCapability", &field)) return -ENODATA;
	if (!json_object_is_type(field, json_type_string) && !json_object_is_type(field, json_type_int)) return -EPROTO;
	const char *text = json_object_get_string(field);
	if (!text || (json_object_is_type(field, json_type_string) && json_object_get_string_len(field) != 1) ||
	    (strcmp(text, "0") && strcmp(text, "1"))) return -EPROTO;
	*enabled = text[0] == '1';
	return 0;
}

static int info_fields(struct json_object *value, struct json_object **observed)
{
	/* Retained GetONUInfo parser field names. Values remain as reported;
	 * no unproven enums or units are assigned by this reader. */
	static const char *const names[] = {
		"actualTime", "gatewayMac", "devType", "hdVersion", "swVersion", "sysUpTime",
		"wifiBand", "standardCapability", "supportedWorkingMode", "supportedRfBand",
		"supportedSsidNumber", "transmitPowerSupported", "supportedUplinkType", "supportedUniPorts",
		"deviceStatus", "workingMode", "uplinkType", "vxd_addr", "noise", "idlePercent",
		"InterferencePercent", "uplinkrate", "downlinkrate", "sendPackets", "sendDroppedPackets",
		"sendFailedPackets", "recvPackets", "recvDroppedPackets", "recvErrorPackets",
		"macFilterEnable", "macFilterPolicy", "macFilterEntries", "roamingSwitch", "ledStatus",
		"macAddress", "accessPoint", "internetAccessMode", "wlanConfigurationEntryNum", "band",
		"channel", "channelWidth", "standard", "antennaNumber2G4", "mimoCapability2G4",
		"mimoParameter2G4", "antennaNumber5G", "mimoCapability5G", "mimoParameter5G",
		"cpuUsage", "memUsage", "cpuTemp", "radarCounter", "lastRadarChannel", "lastRadarTime",
		"swTime", "easymeshCapability"
	};
	struct json_object *result = json_object_new_object(), *field;
	if (!result) return -ENOMEM;
	for (unsigned int i = 0; i < sizeof(names) / sizeof(*names); i++) {
		if (!json_object_object_get_ex(value, names[i], &field)) continue;
		if ((!json_object_is_type(field, json_type_string) && !json_object_is_type(field, json_type_int)) ||
		    (json_object_is_type(field, json_type_string) &&
		     (json_object_get_string_len(field) > 1024 || (size_t)json_object_get_string_len(field) != strlen(json_object_get_string(field))))) {
			json_object_put(result); return -EBADMSG;
		}
		json_object_object_add(result, names[i], json_object_get(field));
	}
	if (!json_object_object_length(result)) { json_object_put(result); return -EAGAIN; }
	*observed = result;
	return 0;
}

int fttr_sync_reply_decode(const struct fttr_sync_message *message, const uint8_t peer[6],
			unsigned int settings, bool writing, bool *ack_seen,
			struct json_object **summary, struct json_object **observed)
{
	struct json_tokener *parser = json_tokener_new();
	struct json_object *value = NULL, *field, *result = NULL, *entries = NULL;
	const char *method = settings ? "SetAllSetting" : "GetWifi";
	uint8_t address[6];
	char mac[18];
	int ret = -EBADMSG;
	size_t i;
	bool acknowledgement = false;

	if (!parser)
		return -ENOMEM;
	json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
	value = json_tokener_parse_ex(parser, (const char *)message->payload, message->length);
	if (json_tokener_get_error(parser) != json_tokener_success ||
	    json_tokener_get_parse_end(parser) != message->length ||
	    !json_object_is_type(value, json_type_object))
		goto out;
	if (settings == 3) {
		struct json_object *rpc, *fields = NULL;
		bool result_present = false;
		if (json_object_object_get_ex(value, "RPCMethod", &rpc)) {
			if (!json_object_is_type(rpc, json_type_string) ||
			    (size_t)json_object_get_string_len(rpc) != strlen(json_object_get_string(rpc))) goto out;
			if (!strcmp(json_object_get_string(rpc), "Register")) { ret = -ESTALE; goto out; }
			if (strcmp(json_object_get_string(rpc), "GetONUInfo")) { ret = -EAGAIN; goto out; }
		} else rpc = NULL;
		if (json_object_object_get_ex(value, "Result", &field)) {
			if (!json_object_is_type(field, json_type_int)) goto out;
			if (json_object_get_int64(field) != 1) { ret = -EREMOTEIO; goto out; }
			*ack_seen = true;
			result_present = true;
		}
		/* The original pending GetONUInfo response can omit RPCMethod.
		 * A bare ACK is insufficient: require this message's success plus
		 * target MAC and at least one understood data field. */
		if (!rpc && !result_present) { ret = -EAGAIN; goto out; }
		if (!rpc && !json_object_object_get_ex(value, "MAC", &field)) { ret = -EAGAIN; goto out; }
		if (!json_object_object_get_ex(value, "MAC", &field) || !json_object_is_type(field, json_type_string) ||
		    json_object_get_string_len(field) != 12) goto out;
		for (i = 0; i < 6; i++) {
			mac[3*i] = json_object_get_string(field)[2*i]; mac[3*i+1] = json_object_get_string(field)[2*i+1];
			mac[3*i+2] = i == 5 ? 0 : ':';
		}
		if (fttr_sync_mac_parse(mac, address) || memcmp(address, peer, 6)) { ret = -ESTALE; goto out; }
		snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
			peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
		ret = info_fields(value, &fields);
		if (ret) goto out;
		result = json_object_new_object();
		if (!result) { json_object_put(fields); ret = -ENOMEM; goto out; }
		json_object_object_add(result, "event", json_object_new_string("sync_onu_info"));
		json_object_object_add(result, "peer", json_object_new_string(mac));
		json_object_object_add(result, "slot", json_object_new_int(message->slot));
		json_object_object_add(result, "identity_authenticated", json_object_new_boolean(0));
		json_object_object_add(result, "reported", json_object_get(fields));
		if (observed) *observed = json_object_get(fields);
		json_object_put(fields); *summary = result; ret = 0; goto out;
	}
	if (settings == 2) {
		struct json_object *rpc, *flag, *status;
		bool has_rpc = json_object_object_get_ex(value, "RPCMethod", &rpc);
		bool has_flag = json_object_object_get_ex(value, "Flag", &flag);
		if (has_rpc && (!json_object_is_type(rpc, json_type_string) ||
		    json_object_get_string_len(rpc) != (int)strlen(json_object_get_string(rpc)))) goto out;
		if (has_rpc && !strcmp(json_object_get_string(rpc), "Register")) { ret = -ESTALE; goto out; }
		if (has_rpc && strcmp(json_object_get_string(rpc), "SetAllSetting")) { ret = -EAGAIN; goto out; }
		if (has_flag && !json_object_is_type(flag, json_type_int)) goto out;
		if (has_flag && json_object_get_int64(flag) != 2) { ret = -EAGAIN; goto out; }
		if (json_object_object_get_ex(value, "Result", &field)) {
			if (!json_object_is_type(field, json_type_int)) goto out;
			if (json_object_get_int64(field) != 1) { ret = -EREMOTEIO; goto out; }
			*ack_seen = true;
		}
		/* Result acknowledges transport. Only the separately identified
		 * Flag2 STATUS0 report completes the original setting operation. */
		if (!has_rpc || !has_flag || !json_object_object_get_ex(value, "STATUS", &status)) {
			ret = -EAGAIN; goto out;
		}
		if (!json_object_is_type(status, json_type_int)) goto out;
		ret = json_object_get_int64(status) == 0 ? 0 : -EAGAIN;
		goto out;
	}
	if (json_object_object_get_ex(value, "RPCMethod", &field) &&
	    (!json_object_is_type(field, json_type_string) ||
	     (size_t)json_object_get_string_len(field) != strlen(method) || strcmp(json_object_get_string(field), method))) {
		ret = -EAGAIN;
		goto out;
	}
	if (settings) {
		struct json_object *content, *settings_content, *status;
		bool report;

		if (json_object_object_get_ex(value, "Flag", &field)) {
			if (!json_object_is_type(field, json_type_int))
				goto out;
			if (json_object_get_int64(field) != 1) { ret = -EAGAIN; goto out; }
		}
		report = json_object_object_get_ex(value, "content", &content);
		if (json_object_object_get_ex(value, "Result", &field)) {
			if (!json_object_is_type(field, json_type_int))
				goto out;
			if (json_object_get_int64(field) != 1) { ret = -EREMOTEIO; goto out; }
			*ack_seen = true;
			if (writing) { ret = 0; goto out; }
		}
		if (writing && json_object_object_get_ex(value, "RPCMethod", &field) &&
		    json_object_object_get_ex(value, "Flag", &field) &&
		    json_object_object_get_ex(value, "STATUS", &status) && json_object_is_type(status, json_type_int)) {
			ret = json_object_get_int64(status) == 0 ? 0 : -EAGAIN;
			goto out;
		}
		if (!report) {
			if (json_object_object_get_ex(value, "STATUS", &status)) {
				if (!json_object_is_type(status, json_type_int))
					goto out;
				if (json_object_get_int64(status) != 0) { ret = -EAGAIN; goto out; }
				goto out;
			}
			ret = -EAGAIN;
			goto out;
		}
		/* Native completion requires the separately identified Flag1 report.
		 * A positive Result by itself is only an ACK, even with a method. */
		if (!json_object_object_get_ex(value, "RPCMethod", &field) ||
		    !json_object_object_get_ex(value, "Flag", &field) ||
		    !json_object_object_get_ex(value, "STATUS", &status) ||
		    !json_object_is_type(status, json_type_int))
			goto out;
		/* Original leaves the operation pending on nonzero STATUS. Its
		 * precise progress/error meanings are not established here. */
		if (json_object_get_int64(status) != 0) { ret = -EAGAIN; goto out; }
		if (!json_object_is_type(content, json_type_object) ||
		    !json_object_object_get_ex(content, "content", &settings_content) ||
		    !json_object_is_type(settings_content, json_type_object) ||
		    !json_object_object_get_ex(settings_content, "List", &entries) ||
		    !json_object_is_type(entries, json_type_array))
			goto out;
		for (i = 0; i < json_object_array_length(entries); i++)
			if (!json_object_is_type(json_object_array_get_idx(entries, i), json_type_object))
				goto out;
		/* Original report ownership is the Ethernet source resolved at ingress;
		 * it does not require a redundant JSON MAC or a remote slot echo. */
		snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
			peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
		goto summarize;
	}
	if (json_object_object_get_ex(value, "Result", &field)) {
		if (!json_object_is_type(field, json_type_int))
			goto out;
		if (json_object_get_int64(field) != 1) {
			ret = -EREMOTEIO;
			goto out;
		}
		acknowledgement = !json_object_object_get_ex(value, "RPCMethod", &field);
		if (acknowledgement) {
			snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
				peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
			goto summarize;
		}
	}
	/* No transaction ID exists in this original envelope. Restrict the
	 * read-only exchange to one peer, slot and method under an interface lease. */
	if (!json_object_object_get_ex(value, "RPCMethod", &field) ||
	    !json_object_is_type(field, json_type_string) ||
	    json_object_get_string_len(field) != 7 || strcmp(json_object_get_string(field), "GetWifi")) {
		ret = -EAGAIN;
		goto out;
	}
	if (!json_object_object_get_ex(value, "MAC", &field) ||
	    !json_object_is_type(field, json_type_string) || json_object_get_string_len(field) != 12)
		goto out;
	for (i = 0; i < 6; i++) {
		mac[i * 3] = json_object_get_string(field)[i * 2];
		mac[i * 3 + 1] = json_object_get_string(field)[i * 2 + 1];
		mac[i * 3 + 2] = i == 5 ? 0 : ':';
	}
	if (fttr_sync_mac_parse(mac, address) || memcmp(address, peer, 6)) {
		ret = -EAGAIN;
		goto out;
	}
summarize:
	result = json_object_new_object();
	json_object_object_add(result, "event", json_object_new_string(settings ? "sync_wifi_settings" :
		acknowledgement ? "sync_ack" : "sync_wifi_reply"));
	json_object_object_add(result, "configuration_verified", json_object_new_boolean(0));
	json_object_object_add(result, "peer", json_object_new_string(mac));
	json_object_object_add(result, "slot", json_object_new_int(message->slot));
	if (settings) {
		json_object_object_add(result, "ack_seen", json_object_new_boolean(*ack_seen));
		json_object_object_add(result, "report_complete", json_object_new_boolean(1));
		json_object_object_add(result, "settings_entries", json_object_new_int(json_object_array_length(entries)));
		if (observed)
			*observed = json_object_get(entries);
	}
	/* Summarize shape only. GetWifi can contain SSIDs and keys, including
	 * fields not implemented here. Never print raw JSON or arbitrary values. */
	json_object_object_add(result, "attributes", json_object_new_int(json_object_object_length(value)));
	if (json_object_object_get_ex(value, "SSID", &field) && json_object_is_type(field, json_type_array))
		json_object_object_add(result, "ssid_entries", json_object_new_int(json_object_array_length(field)));
	json_object_object_add(result, "values_redacted", json_object_new_boolean(1));
	*summary = result;
	ret = 0;
out:
	json_object_put(value);
	json_tokener_free(parser);
	return ret;
}

int fttr_sync_request(int fd, const uint8_t source[6], const uint8_t peer[6], uint16_t ethertype,
		uint8_t slot, unsigned int settings, struct json_object *configuration)
{
	uint8_t frame[FTTR_SYNC_FRAME_MAX];
	char payload[FTTR_SYNC_FRAME_MAX];
	const char *content = configuration ? json_object_to_json_string_ext(configuration, JSON_C_TO_STRING_PLAIN) : "{\"act\":\"get\"}";
	int ret, length;
	if (settings > 3 || (settings == 2 && !configuration)) return -EINVAL;

	length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"GetWifi\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\"}",
		source[0], source[1], source[2], source[3], source[4], source[5]);
	if (settings == 3)
		length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"GetONUInfo\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\"}",
			source[0], source[1], source[2], source[3], source[4], source[5]);
	else if (settings)
		length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"SetAllSetting\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\","
			"\"DMAC\":\"%02X%02X%02X%02X%02X%02X\",\"Flag\":%u,\"content\":%s}",
			source[0], source[1], source[2], source[3], source[4], source[5],
			peer[0], peer[1], peer[2], peer[3], peer[4], peer[5], settings, content);
	if (length < 0 || (size_t)length >= sizeof(payload))
		return -E2BIG;
	ret = fttr_sync_pack(source, fttr_sync_slave_group, ethertype, slot, FTTR_SYNC_REQUEST,
		payload, length, frame, sizeof(frame));
	if (ret < 0)
		return ret;
	length = ret;
	{
		ssize_t sent = send(fd, frame, length, 0);

		if (sent != length)
			return sent < 0 ? -errno : -EIO;
	}
	return 0;
}
