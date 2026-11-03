// SPDX-License-Identifier: GPL-2.0-only
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <json-c/json.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "omci-socket.h"
#include "sync-wire.h"
#include "sync-peer.h"

static int64_t now_ms(void)
{
	struct timespec time;

	clock_gettime(CLOCK_MONOTONIC, &time);
	return (int64_t)time.tv_sec * 1000 + time.tv_nsec / 1000000;
}

static int mac_parse(const char *text, uint8_t mac[6])
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

static int reply_decode(const struct fttr_sync_message *message, const uint8_t peer[6],
			bool settings, bool *ack_seen, struct json_object **summary)
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
	if (mac_parse(mac, address) || memcmp(address, peer, 6)) {
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

static int discovery(int fd, const uint8_t source[6], uint16_t ethertype,
		     const uint8_t *target, struct json_object **summary)
{
	struct fttr_sync_peer peers[FTTR_SYNC_PEERS] = { 0 };
	struct pollfd descriptor = { .fd = fd, .events = POLLIN };
	uint8_t frame[FTTR_SYNC_FRAME_MAX], sequence = 0;
	int64_t deadline = now_ms() + 6500, next = 0;
	int ret, slot;

	while (now_ms() < deadline) {
		int64_t now = now_ms(), wake;
		ssize_t received;

		if (now >= next) {
			fttr_sync_heartbeat(source, ethertype, sequence++, frame);
			received = send(fd, frame, 16, 0);
			if (received != 16)
				return received < 0 ? -errno : -EIO;
			fttr_sync_peer_age(peers);
			next = now + FTTR_SYNC_HEARTBEAT_MS;
		}
		wake = next < deadline ? next : deadline;
		ret = poll(&descriptor, 1, wake > now ? wake - now : 0);
		if (ret < 0) { if (errno == EINTR) continue; return -errno; }
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))
			return -EIO;
		if (!ret)
			continue;
		received = recv(fd, frame, sizeof(frame), MSG_TRUNC);
		if (received < 0) { if (errno == EAGAIN) continue; return -errno; }
		slot = fttr_sync_peer_receive(peers, source, ethertype, frame, received);
		if (slot == -ENOSPC)
			return slot;
		if (slot >= 0 && target && !memcmp(peers[slot].mac, target, 6))
			return slot;
	}
	if (target)
		return -ETIMEDOUT;
	*summary = json_object_new_object();
	{
		struct json_object *list = json_object_new_array();

		json_object_object_add(*summary, "event", json_object_new_string("sync_discovery"));
		json_object_object_add(*summary, "identity_authenticated", json_object_new_boolean(0));
		for (slot = 0; slot < FTTR_SYNC_PEERS; slot++) {
			struct json_object *entry;
			char mac[18];

			if (!peers[slot].online)
				continue;
			entry = json_object_new_object();
			snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
				peers[slot].mac[0], peers[slot].mac[1], peers[slot].mac[2],
				peers[slot].mac[3], peers[slot].mac[4], peers[slot].mac[5]);
			json_object_object_add(entry, "mac", json_object_new_string(mac));
			json_object_object_add(entry, "slot", json_object_new_int(slot));
			json_object_object_add(entry, "missed", json_object_new_int(peers[slot].missed));
			json_object_array_add(list, entry);
		}
		json_object_object_add(*summary, "peers", list);
	}
	return 0;
}

int main(int argc, char **argv)
{
	struct ifreq request = { 0 };
	struct fttr_sync_message message;
	struct fttr_sync_receiver receivers[2] = { 0 };
	struct json_object *summary = NULL;
	struct pollfd descriptor;
	struct sockaddr_un lock = { .sun_family = AF_UNIX };
	uint8_t peer[6], source[6], frame[FTTR_SYNC_FRAME_MAX];
	char payload[192], *end;
	unsigned int index;
	unsigned long slot, ethertype;
	int lease = -1, control = -1, fd = -1, ret = -EINVAL, length;
	int64_t deadline;
	bool discover = argc == 4 && !strcmp(argv[2], "discover");
	bool settings = argc == 6 && !strcmp(argv[5], "wifi-settings");
	bool automatic = (argc == 5 || settings) && !strcmp(argv[3], "auto");
	bool ack_seen = false;

	if ((!discover && !settings && argc != 5) || strlen(argv[1]) >= IFNAMSIZ || strchr(argv[1], '/') ||
	    (!discover && mac_parse(argv[2], peer))) {
		fprintf(stderr, "Usage: %s <interface> <peer-MAC> <sync-slot:0..15|auto> <ethertype> [wifi-settings]\n"
			"       %s <interface> discover <ethertype>\n", argv[0], argv[0]);
		return 2;
	}
	errno = 0;
	slot = 0;
	if (!discover && !automatic) {
		slot = strtoul(argv[3], &end, 0);
		if (errno || !*argv[3] || *end || slot > 15)
			goto out;
	}
	errno = 0;
	ethertype = strtoul(argv[discover ? 3 : 4], &end, 0);
	if (errno || !*argv[discover ? 3 : 4] || *end || ethertype < 0x600 || ethertype > 0xffff)
		goto out;
	index = if_nametoindex(argv[1]);
	if (!index) { ret = -errno; goto out; }
	/* Abstract socket ownership is scoped to the network namespace and
	 * automatically released on exit, unlike a persistent lock filename. */
	snprintf(lock.sun_path + 1, sizeof(lock.sun_path) - 1, "tyfza1-fttr-sync-%u", index);
	lease = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (lease < 0 || bind(lease, (struct sockaddr *)&lock,
	    offsetof(struct sockaddr_un, sun_path) + 1 + strlen(lock.sun_path + 1))) { ret = -errno; goto out; }
	control = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (control < 0) { ret = -errno; goto out; }
	strcpy(request.ifr_name, argv[1]);
	if (ioctl(control, SIOCGIFHWADDR, &request)) { ret = -errno; goto out; }
	memcpy(source, request.ifr_hwaddr.sa_data, 6);
	fd = fttr_omci_open(argv[1], ethertype);
	if (fd < 0) { ret = fd; goto out; }
	{
		struct packet_mreq membership = { .mr_ifindex = index, .mr_type = PACKET_MR_MULTICAST, .mr_alen = 6 };
		const uint8_t *groups[] = { fttr_sync_master_group, fttr_sync_slave_group };
		unsigned int i;

		for (i = 0; i < 2; i++) {
			memcpy(membership.mr_address, groups[i], 6);
			if (setsockopt(fd, SOL_PACKET, PACKET_ADD_MEMBERSHIP, &membership, sizeof(membership))) {
				ret = -errno;
				goto out;
			}
		}
	}
	if (discover || automatic) {
		ret = discovery(fd, source, ethertype, discover ? NULL : peer, &summary);
		if (ret < 0 || discover)
			goto out;
		slot = ret;
	}
	length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"GetWifi\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\"}",
		source[0], source[1], source[2], source[3], source[4], source[5]);
	if (settings)
		length = snprintf(payload, sizeof(payload), "{\"RPCMethod\":\"SetAllSetting\",\"MAC\":\"%02X%02X%02X%02X%02X%02X\","
			"\"DMAC\":\"%02X%02X%02X%02X%02X%02X\",\"Flag\":1,\"content\":{\"act\":\"get\"}}",
			source[0], source[1], source[2], source[3], source[4], source[5],
			peer[0], peer[1], peer[2], peer[3], peer[4], peer[5]);
	ret = fttr_sync_pack(source, fttr_sync_slave_group, ethertype, slot, FTTR_SYNC_REQUEST,
		payload, length, frame, sizeof(frame));
	if (ret < 0)
		goto out;
	length = ret;
	{
		ssize_t sent = send(fd, frame, length, 0);

		if (sent != length) { ret = sent < 0 ? -errno : -EIO; goto out; }
	}
	/* Diagnostic budget, not an asserted original retransmission policy. */
	deadline = now_ms() + 3000;
	descriptor = (struct pollfd) { .fd = fd, .events = POLLIN };
	for (;;) {
		int64_t remaining = deadline - now_ms();
		ssize_t received;

		if (remaining <= 0) { ret = -ETIMEDOUT; break; }
		ret = poll(&descriptor, 1, remaining);
		if (ret < 0) { if (errno == EINTR) continue; ret = -errno; break; }
		if (!ret) { ret = -ETIMEDOUT; break; }
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) { ret = -EIO; break; }
		received = recv(fd, frame, sizeof(frame), MSG_TRUNC);
		if (received < 0) { if (errno == EAGAIN) continue; ret = -errno; break; }
		if (received < 16 || !fttr_sync_destination(frame, source) || memcmp(frame + 6, peer, 6) ||
		    ((frame[14] & 15) != FTTR_SYNC_RESPONSE &&
		     (!settings || (frame[14] & 15) != FTTR_SYNC_REQUEST)))
			continue;
		/* Original JSON ingress accepts6 and7. Keep their partial messages
		 * separate; a standalone ACK must not discard a fragmented report. */
		if (settings && !(frame[15] & 0xfc))
			ret = fttr_sync_unpack(frame, received, ethertype, &message);
		else
			ret = fttr_sync_receive(&receivers[(frame[14] & 15) == FTTR_SYNC_RESPONSE],
				frame, received, ethertype, now_ms(), &message);
		if (ret == -EAGAIN)
			continue;
		if (ret)
			break;
		/* Original ingress resolves the local slot by Ethernet source MAC;
		 * the sender's header nibble is not our table index. */
		message.slot = slot;
		ret = reply_decode(&message, peer, settings, &ack_seen, &summary);
		if (ret == -EAGAIN)
			continue;
		break;
	}
out:
	if (!ret) {
		puts(json_object_to_json_string_ext(summary, JSON_C_TO_STRING_PLAIN));
		if (fflush(stdout))
			ret = -errno;
	} else {
		fprintf(stderr, "FTTR sync %s: %s (%d)\n", discover ? "discovery" : settings ? "wifi-settings" : "GetWifi", strerror(-ret), ret);
	}
	json_object_put(summary);
	if (fd >= 0) close(fd);
	if (control >= 0) close(control);
	if (lease >= 0) close(lease);
	return ret ? 1 : 0;
}
