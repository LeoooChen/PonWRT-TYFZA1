// SPDX-License-Identifier: GPL-2.0-only
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
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
#include "sync-wifi.h"
#include "sync-json.h"
#include "sync-register.h"
#include "sync-client.h"
#include "sync-controller.h"

static int64_t now_ms(void)
{
	struct timespec time;

	clock_gettime(CLOCK_MONOTONIC, &time);
	return (int64_t)time.tv_sec * 1000 + time.tv_nsec / 1000000;
}

static int exchange(int fd, const uint8_t source[6], const uint8_t peer[6], uint16_t ethertype,
		    uint8_t slot, unsigned int settings, struct json_object *configuration, bool *ack_seen,
		    struct json_object **summary, struct json_object **observed)
{
	struct fttr_sync_receiver receivers[2] = { 0 };
	struct fttr_sync_message message;
	struct pollfd descriptor = { .fd = fd, .events = POLLIN };
	uint8_t frame[FTTR_SYNC_FRAME_MAX];
	int ret;
	int64_t deadline;

	*ack_seen = false;
	ret = fttr_sync_request(fd, source, peer, ethertype, slot, settings, configuration);
	if (ret)
		return ret;
	/* Fixed per-exchange budget, not an asserted original retry policy. */
	deadline = now_ms() + (settings == 2 ? 9000 : 3000);
	for (;;) {
		int64_t remaining = deadline - now_ms();
		ssize_t received;

		if (remaining <= 0)
			return -ETIMEDOUT;
		ret = poll(&descriptor, 1, remaining);
		if (ret < 0) { if (errno == EINTR) continue; return -errno; }
		if (!ret)
			return -ETIMEDOUT;
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))
			return -EIO;
		received = recv(fd, frame, sizeof(frame), MSG_TRUNC);
		if (received < 0) { if (errno == EAGAIN) continue; return -errno; }
		if (received < 16 || received > (ssize_t)sizeof(frame) ||
		    !fttr_sync_destination(frame, source) || memcmp(frame + 6, peer, 6) ||
		    ((frame[14] & 15) != FTTR_SYNC_RESPONSE &&
		     (!settings || (frame[14] & 15) != FTTR_SYNC_REQUEST)))
			continue;
		/* Independent partial6/7 reports; standalone ACKs cannot clear them. */
		if (settings && !(frame[15] & 0xfc))
			ret = fttr_sync_unpack(frame, received, ethertype, &message);
		else
			ret = fttr_sync_receive(&receivers[(frame[14] & 15) == FTTR_SYNC_RESPONSE],
				frame, received, ethertype, now_ms(), &message);
		if (ret == -EAGAIN)
			continue;
		if (ret)
			return ret;
		message.slot = slot;
		ret = fttr_sync_reply_decode(&message, peer, settings, configuration != NULL, ack_seen, summary, observed);
		if (ret != -EAGAIN)
			return ret;
	}
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

static int drain(int fd)
{
	uint8_t frame[FTTR_SYNC_FRAME_MAX];
	unsigned int i;
	ssize_t n;

	/* Retire queued replies between phases, with a hard bound under flood.
	 * A later stale identical reply remains indistinguishable on this wire. */
	for (i = 0; i < 256; i++) {
		n = recv(fd, frame, sizeof(frame), MSG_DONTWAIT | MSG_TRUNC);
		if (n < 0)
			return errno == EAGAIN || errno == EWOULDBLOCK ? 0 : -errno;
	}
	return -EBUSY;
}

static int registration(int fd, const uint8_t source[6], const uint8_t peer[6],
		uint16_t ethertype, uint8_t slot, struct json_object *policy, struct json_object **summary)
{
	struct fttr_sync_receiver receivers[2] = { 0 };
	struct fttr_sync_message message;
	struct pollfd descriptor = { .fd = fd, .events = POLLIN };
	struct json_object *reply = NULL, *ready;
	uint8_t frame[FTTR_SYNC_FRAME_MAX], sequence = 0;
	int64_t deadline = now_ms() + 6500, next = 0;
	int ret;

	while (now_ms() < deadline) {
		int64_t now = now_ms(), wake;
		ssize_t n;
		const char *payload;

		if (now >= next) {
			fttr_sync_heartbeat(source, ethertype, sequence++, frame);
			n = send(fd, frame, 16, 0);
			if (n != 16)
				return n < 0 ? -errno : -EIO;
			next = now + FTTR_SYNC_HEARTBEAT_MS;
		}
		wake = next < deadline ? next : deadline;
		ret = poll(&descriptor, 1, wake > now ? wake - now : 0);
		if (ret < 0) { if (errno == EINTR) continue; return -errno; }
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL))
			return -EIO;
		if (!ret)
			continue;
		n = recv(fd, frame, sizeof(frame), MSG_TRUNC);
		if (n < 0) { if (errno == EAGAIN) continue; return -errno; }
		if (n < 16 || !fttr_sync_destination(frame, source) || memcmp(frame + 6, peer, 6) ||
		    ((frame[14] & 15) != FTTR_SYNC_REQUEST && (frame[14] & 15) != FTTR_SYNC_RESPONSE))
			continue;
		ret = fttr_sync_receive(&receivers[(frame[14] & 15) == FTTR_SYNC_RESPONSE], frame, n,
			ethertype, now_ms(), &message);
		if (ret == -EAGAIN)
			continue;
		if (ret)
			return ret;
		ret = fttr_sync_register_reply(&message, policy, source, &reply);
		if (ret == -EAGAIN)
			continue;
		if (ret)
			return ret;
		payload = json_object_to_json_string_ext(reply, JSON_C_TO_STRING_PLAIN);
		ret = fttr_sync_pack(source, fttr_sync_slave_group, ethertype, slot, FTTR_SYNC_RESPONSE,
			payload, strlen(payload), frame, sizeof(frame));
		json_object_put(reply);
		if (ret < 0)
			return ret;
		n = send(fd, frame, ret, 0);
		if (n != ret)
			return n < 0 ? -errno : -EIO;
		*summary = json_object_new_object();
		json_object_object_add(*summary, "event", json_object_new_string("sync_registration_reply"));
		json_object_object_add(*summary, "slot", json_object_new_int(slot));
		json_object_object_add(*summary, "identity_pin_match", json_object_new_boolean(1));
		json_object_object_add(*summary, "identity_authenticated", json_object_new_boolean(0));
		json_object_object_add(*summary, "peer_acceptance_verified", json_object_new_boolean(0));
		json_object_object_get_ex(policy, "Ready", &ready);
		json_object_object_add(*summary, "advertised_ready", json_object_get(ready));
		return 0;
	}
	return -ETIMEDOUT;
}

int main(int argc, char **argv)
{
	struct ifreq request = { 0 };
	struct json_object *summary = NULL, *desired = NULL;
	struct sockaddr_un lock = { .sun_family = AF_UNIX };
	uint8_t peer[6], source[6];
	char *end;
	unsigned int index;
	unsigned long slot, ethertype;
	int lease = -1, control = -1, fd = -1, ret = -EINVAL;
	bool discover = argc == 4 && !strcmp(argv[2], "discover");
	bool resident = (argc == 5 || (argc == 7 && !strcmp(argv[5], "--state"))) && !strcmp(argv[2], "serve");
	bool apply = argc == 7 && !strcmp(argv[5], "wifi-apply");
	bool easymesh = argc == 7 && !strcmp(argv[5], "easymesh");
	bool info = argc == 6 && !strcmp(argv[5], "onu-info");
	bool radio_apply = argc == 7 && !strcmp(argv[5], "radio-apply");
	bool wan = argc == 7 && !strcmp(argv[5], "wan-ip");
	bool radio_info = argc == 6 && !strcmp(argv[5], "radio-info");
	bool registering = argc == 7 && !strcmp(argv[5], "register");
	unsigned int settings = wan?6:radio_apply?5:radio_info?4:info ? 3 : easymesh ? 2 : apply || (argc == 6 && !strcmp(argv[5], "wifi-settings"));
	bool automatic = !resident && (argc == 5 || settings || registering) && !strcmp(argv[3], "auto");
	bool ack_seen = false;

	if ((!discover && !resident && !settings && !registering && argc != 5) || strlen(argv[1]) >= IFNAMSIZ || strchr(argv[1], '/') ||
	    (!discover && !resident && fttr_sync_mac_parse(argv[2], peer))) {
		fprintf(stderr, "Usage: %s <interface> <peer-MAC> <sync-slot:0..15|auto> <ethertype> [wan-ip <IPv4-or-IPv6> | onu-info | radio-info | radio-apply <private-json-file> | wifi-settings | wifi-apply <private-json-file> | easymesh <0|1> | register <private-policy-file>]\n"
			"       %s <interface> discover <ethertype>\n"
			"       %s <interface> serve <ethertype> <private-policy-file> [--state <private-persistent-directory>]\n", argv[0], argv[0], argv[0]);
		return 2;
	}
	if (resident) {
		ret = fttr_sync_json_load_bounded(argv[4], 131072, &desired);
		if (!ret)
			ret = fttr_sync_controller_policy(desired);
		if (ret)
			goto out;
		ret = -EINVAL;
	}
	if (easymesh) {
		if (strcmp(argv[6], "0") && strcmp(argv[6], "1")) goto out;
		desired = json_object_new_object();
		if (!desired) { ret = -ENOMEM; goto out; }
		json_object_object_add(desired, "act", json_object_new_string("config"));
		json_object_object_add(desired, "EnableEasymesh", json_object_new_string(argv[6]));
	}
	if (apply) {
		ret = fttr_sync_wifi_load(argv[6], &desired);
		if (ret)
			goto out;
		ret = -EINVAL;
	}
	if (radio_apply) {
		ret=fttr_sync_json_load_private(argv[6],&desired);
		if (!ret) ret=fttr_sync_radio_validate(desired);
		if (ret) goto out;
		ret=-EINVAL;
	}
	if (wan) {
		if (fttr_sync_wan_address(argv[6]) < 0)
			goto out;
		desired = json_object_new_string(argv[6]);
		if (!desired) { ret = -ENOMEM; goto out; }
	}
	if (registering) {
		ret = fttr_sync_json_load_private(argv[6], &desired);
		if (!ret)
			ret = fttr_sync_register_policy(desired);
		if (ret)
			goto out;
		ret = -EINVAL;
	}
	errno = 0;
	slot = 0;
	if (!discover && !resident && !automatic) {
		slot = strtoul(argv[3], &end, 0);
		if (errno || !*argv[3] || *end || slot > 15)
			goto out;
	}
	errno = 0;
	ethertype = strtoul(argv[discover || resident ? 3 : 4], &end, 0);
	if (errno || !*argv[discover || resident ? 3 : 4] || *end || ethertype < 0x600 || ethertype > 0xffff)
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
	if (resident) {
		ret = fttr_sync_controller(fd, source, ethertype, desired, argc == 7 ? argv[6] : NULL);
		goto out;
	}
	if (discover || automatic) {
		ret = discovery(fd, source, ethertype, discover ? NULL : peer, &summary);
		if (ret < 0 || discover)
			goto out;
		slot = ret;
	}
	if (registering) {
		ret = registration(fd, source, peer, ethertype, slot, desired, &summary);
		goto out;
	}
	if (easymesh) {
		struct json_object *observed = NULL;
		bool enabled = false, write_ack = false;
		int write_result, read_result;
		ret = drain(fd);
		if (ret) goto out;
		ret = exchange(fd, source, peer, ethertype, slot, 3, NULL, &ack_seen, &summary, &observed);
		if (!ret) ret = fttr_sync_info_mesh(observed, &enabled);
		json_object_put(observed); observed = NULL; json_object_put(summary); summary = NULL;
		if (ret) goto out;
		ret = drain(fd);
		if (ret) goto out;
		write_result = exchange(fd, source, peer, ethertype, slot, 2, desired, &write_ack, &summary, NULL);
		read_result = write_result == -ESTALE ? -ESTALE : drain(fd);
		if (!read_result)
			read_result = exchange(fd, source, peer, ethertype, slot, 3, NULL, &ack_seen, &summary, &observed);
		if (!read_result) read_result = fttr_sync_info_mesh(observed, &enabled);
		if (!read_result && enabled != (argv[6][0] == '1')) read_result = -EUCLEAN;
		json_object_put(observed); json_object_put(summary);
		summary = json_object_new_object();
		json_object_object_add(summary, "event", json_object_new_string("sync_easymesh"));
		json_object_object_add(summary, "peer", json_object_new_string(argv[2]));
		json_object_object_add(summary, "slot", json_object_new_int(slot));
		json_object_object_add(summary, "requested_enabled", json_object_new_boolean(argv[6][0] == '1'));
		json_object_object_add(summary, "ack_seen", json_object_new_boolean(write_ack));
		json_object_object_add(summary, "completion_reported", json_object_new_boolean(!write_result));
		json_object_object_add(summary, "write_result", json_object_new_int(write_result));
		json_object_object_add(summary, "readback_result", json_object_new_int(read_result));
		json_object_object_add(summary, "reported_state_matches", json_object_new_boolean(!read_result));
		json_object_object_add(summary, "configuration_verified", json_object_new_boolean(0));
		ret = write_result ? write_result : read_result;
		json_object_object_add(summary, "error", json_object_new_int(ret));
		goto out;
	}
	if (wan) {
		ret = exchange(fd, source, peer, ethertype, slot, 6, desired, &ack_seen, &summary, NULL);
		summary = json_object_new_object();
		json_object_object_add(summary, "event", json_object_new_string("sync_wan_ip"));
		json_object_object_add(summary, "peer", json_object_new_string(argv[2]));
		json_object_object_add(summary, "slot", json_object_new_int(slot));
		json_object_object_add(summary, "address", json_object_get(desired));
		json_object_object_add(summary, "ack_seen", json_object_new_boolean(ack_seen));
		json_object_object_add(summary, "configuration_verified", json_object_new_boolean(0));
		json_object_object_add(summary, "error", json_object_new_int(ret));
		goto out;
	}
	if (apply || radio_apply) {
		struct json_object *observed = NULL;
		int write_result = 0, read_result = 0;

		ret = exchange(fd, source, peer, ethertype, slot, radio_apply?4:1, NULL, &ack_seen, &summary, &observed);
		if (!ret)
			ret = radio_apply?fttr_sync_radio_check(observed,desired,false):fttr_sync_wifi_check(observed, desired, false);
		json_object_put(observed);
		observed = NULL;
		json_object_put(summary);
		summary = NULL;
		if (ret)
			goto out;
		ret = drain(fd);
		if (ret)
			goto out;
		write_result = exchange(fd, source, peer, ethertype, slot, radio_apply?5:1, desired, &ack_seen, &summary, NULL);
		/* A lost ACK does not imply the remote write was not applied. Always
		 * attempt independent readback; never replay a password blindly. */
		read_result = write_result==-ESTALE?-ESTALE:drain(fd);
		if (!read_result)
			read_result = exchange(fd, source, peer, ethertype, slot, radio_apply?4:1, NULL, &ack_seen, &summary, &observed);
		if (!read_result)
			read_result = radio_apply?fttr_sync_radio_check(observed,desired,true):fttr_sync_wifi_check(observed, desired, true);
		json_object_put(observed);
		json_object_put(summary);
		summary = json_object_new_object();
		json_object_object_add(summary, "event", json_object_new_string(radio_apply?"sync_radio_apply":"sync_wifi_apply"));
		json_object_object_add(summary, "peer", json_object_new_string(argv[2]));
		json_object_object_add(summary, "slot", json_object_new_int(slot));
		{
			struct json_object *ssid_index;

			json_object_object_get_ex(desired, radio_apply?"RadioType":"SSIDIndex", &ssid_index);
			json_object_object_add(summary, radio_apply?"radio_type":"ssid_index", json_object_get(ssid_index));
		}
		json_object_object_add(summary, "write_attempted", json_object_new_boolean(1));
		json_object_object_add(summary, "write_result", json_object_new_int(write_result));
		json_object_object_add(summary, "readback_result", json_object_new_int(read_result));
		json_object_object_add(summary, "readable_fields_match", json_object_new_boolean(!read_result));
		json_object_object_add(summary, "credentials_verified", json_object_new_boolean(0));
		json_object_object_add(summary, "configuration_verified", json_object_new_boolean(0));
		json_object_object_add(summary, "values_redacted", json_object_new_boolean(1));
		ret = write_result ? write_result : read_result;
	} else {
		ret = exchange(fd, source, peer, ethertype, slot, settings, NULL, &ack_seen, &summary, NULL);
	}
out:
	if (summary) {
		puts(json_object_to_json_string_ext(summary, JSON_C_TO_STRING_PLAIN));
		if (fflush(stdout))
			ret = -errno;
	}
	if (ret)
		fprintf(stderr, "FTTR sync %s: %s (%d)\n", resident ? "serve" : discover ? "discovery" : registering ? "register" : wan ? "wan-ip" : easymesh ? "easymesh" : apply ? "wifi-apply" : radio_apply ? "radio-apply" : radio_info ? "radio-info" : info ? "onu-info" : settings ? "wifi-settings" : "GetWifi", strerror(-ret), ret);
	json_object_put(summary);
	json_object_put(desired);
	if (fd >= 0) close(fd);
	if (control >= 0) close(control);
	if (lease >= 0) close(lease);
	return ret ? 1 : 0;
}
