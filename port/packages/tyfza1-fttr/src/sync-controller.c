// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "sync-controller.h"
#include "sync-client.h"
#include "sync-peer.h"
#include "sync-register.h"
#include "sync-wifi.h"
#include "sync-store.h"

/* Pending sends only run after ingress reaches EAGAIN. No protocol transaction
 * ID exists: later identical same-source reports remain indistinguishable. */
enum phase { IDLE, PREFLIGHT_SEND, PREFLIGHT, WRITE_SEND, WRITE, READBACK_SEND, READBACK, RECOVER_SEND, RECOVER, MESH_SEND, MESH };
#define MESH_ATTEMPTED (1U << 16)

struct policy_peer {
	uint8_t mac[6];
	struct json_object *registration, *wifi, *mesh;
	struct json_object *input, *record;
	unsigned int attempted;
	bool blocked;
};

struct session {
	struct fttr_sync_receiver receivers[2];
	struct policy_peer *policy;
	enum phase phase;
	unsigned int generation, configuration;
	int protocol, write_result;
	int64_t deadline;
	bool registered, ack_seen;
};

static volatile sig_atomic_t stopping;

static void stop(int sig)
{
	(void)sig;
	stopping = 1;
}

static int64_t now_ms(void)
{
	struct timespec time;

	clock_gettime(CLOCK_MONOTONIC, &time);
	return (int64_t)time.tv_sec * 1000 + time.tv_nsec / 1000000;
}

static int policies(struct json_object *value, struct policy_peer peers[FTTR_SYNC_PEERS])
{
	struct json_object *list, *entry, *mac, *registration, *wifi, *revision, *mesh;
	size_t i, j, k, count;
	uint8_t address[6];

	if (!json_object_is_type(value, json_type_object) || json_object_object_length(value) != 1 ||
	    !json_object_object_get_ex(value, "Peers", &list) || !json_object_is_type(list, json_type_array))
		return -EINVAL;
	count = json_object_array_length(list);
	if (!count || count > FTTR_SYNC_PEERS)
		return -EINVAL;
	for (i = 0; i < count; i++) {
		entry = json_object_array_get_idx(list, i);
		if (!json_object_is_type(entry, json_type_object) ||
		    !json_object_object_get_ex(entry, "MAC", &mac) || !json_object_is_type(mac, json_type_string) ||
		    json_object_get_string_len(mac) != 17 || fttr_sync_mac_parse(json_object_get_string(mac), address) ||
		    !json_object_object_get_ex(entry, "Registration", &registration) || fttr_sync_register_policy(registration))
			return -EINVAL;
		wifi = NULL;
		if (json_object_object_get_ex(entry, "WiFi", &wifi)) {
			if (!json_object_is_type(wifi, json_type_array) || json_object_array_length(wifi) > 16)
				return -EINVAL;
			for (j = 0; j < json_object_array_length(wifi); j++) {
				struct json_object *desired = json_object_array_get_idx(wifi, j), *index, *previous;

				if (fttr_sync_wifi_validate(desired))
					return -EINVAL;
				json_object_object_get_ex(desired, "SSIDIndex", &index);
				for (k = 0; k < j; k++) {
					json_object_object_get_ex(json_object_array_get_idx(wifi, k), "SSIDIndex", &previous);
					if (json_object_equal(index, previous))
						return -EINVAL;
				}
			}
		}
		j = wifi ? 3 : 2;
		mesh = NULL;
		if (json_object_object_get_ex(entry, "EasyMesh", &mesh)) {
			if (!json_object_is_type(mesh, json_type_boolean)) return -EINVAL;
			j++;
		}
		if (json_object_object_get_ex(entry, "Revision", &revision)) {
			if (fttr_sync_store_revision(entry))
				return -EINVAL;
			j++;
		}
		if ((size_t)json_object_object_length(entry) != j)
			return -EINVAL;
		for (j = 0; j < i; j++)
			if (!memcmp(peers[j].mac, address, 6))
				return -EINVAL;
		memcpy(peers[i].mac, address, 6);
		peers[i].registration = registration;
		peers[i].wifi = wifi;
		peers[i].mesh = mesh;
		peers[i].input = entry;
	}
	return 0;
}

int fttr_sync_controller_policy(struct json_object *policy)
{
	struct policy_peer peers[FTTR_SYNC_PEERS] = { 0 };

	return policies(policy, peers);
}

static void event(const char *name, const struct session *session, const struct fttr_sync_peer *peer,
		int slot, int result, int readback)
{
	struct json_object *value = json_object_new_object();
	char mac[18];

	snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x",
		peer->mac[0], peer->mac[1], peer->mac[2], peer->mac[3], peer->mac[4], peer->mac[5]);
	json_object_object_add(value, "event", json_object_new_string(name));
	json_object_object_add(value, "peer", json_object_new_string(mac));
	json_object_object_add(value, "slot", json_object_new_int(slot));
	json_object_object_add(value, "generation", json_object_new_int64(session->generation));
	json_object_object_add(value, "protocol", json_object_new_int(session->protocol));
	json_object_object_add(value, "result", json_object_new_int(result));
	json_object_object_add(value, "identity_pin_match", json_object_new_boolean(session->registered));
	json_object_object_add(value, "identity_authenticated", json_object_new_boolean(0));
	json_object_object_add(value, "credentials_verified", json_object_new_boolean(0));
	json_object_object_add(value, "configuration_verified", json_object_new_boolean(0));
	json_object_object_add(value, "values_redacted", json_object_new_boolean(1));
	if (session->policy) {
		struct json_object *revision;

		if (json_object_object_get_ex(session->policy->input, "Revision", &revision))
			json_object_object_add(value, "revision", json_object_get(revision));
	}
	if (!strcmp(name, "sync_resident_registered")) {
		struct json_object *ready;

		json_object_object_get_ex(session->policy->registration, "Ready", &ready);
		json_object_object_add(value, "advertised_ready", json_object_get(ready));
		json_object_object_add(value, "peer_acceptance_verified", json_object_new_boolean(0));
	}
	if (!strcmp(name, "sync_resident_easymesh")) {
		json_object_object_add(value, "requested_enabled", json_object_get(session->policy->mesh));
		json_object_object_add(value, "ack_seen", json_object_new_boolean(session->ack_seen));
		json_object_object_add(value, "completion_reported", json_object_new_boolean(!result));
		json_object_object_add(value, "write_replayed", json_object_new_boolean(0));
	}
	if (!strcmp(name, "sync_resident_recovery")) {
		struct json_object *index;

		json_object_object_get_ex(json_object_array_get_idx(session->policy->wifi, session->configuration), "SSIDIndex", &index);
		json_object_object_add(value, "ssid_index", json_object_get(index));
		json_object_object_add(value, "prior_write_outcome_unknown", json_object_new_boolean(1));
		json_object_object_add(value, "write_replayed", json_object_new_boolean(0));
		json_object_object_add(value, "readable_fields_match", json_object_new_boolean(!result));
	}
	if (!strcmp(name, "sync_resident_apply")) {
		struct json_object *index;

		json_object_object_get_ex(json_object_array_get_idx(session->policy->wifi, session->configuration), "SSIDIndex", &index);
		json_object_object_add(value, "ssid_index", json_object_get(index));
		json_object_object_add(value, "write_attempted", json_object_new_boolean(1));
		json_object_object_add(value, "write_result", json_object_new_int(session->write_result));
		json_object_object_add(value, "readback_result", json_object_new_int(readback));
		json_object_object_add(value, "readable_fields_match", json_object_new_boolean(!readback));
	}
	puts(json_object_to_json_string_ext(value, JSON_C_TO_STRING_PLAIN));
	fflush(stdout);
	json_object_put(value);
}

static void phase(struct session *session, enum phase next)
{
	session->phase = next;
	session->ack_seen = false;
}

static void revoke(struct session *session, const struct fttr_sync_peer *peer, int slot, int reason)
{
	if (session->phase != IDLE) {
		event("sync_resident_cancel", session, peer, slot, reason, reason);
	}
	if (session->policy && session->policy->attempted && (session->registered || session->phase != IDLE))
		session->policy->blocked = true;
	phase(session, IDLE);
	memset(session->receivers, 0, sizeof(session->receivers));
	session->registered = false;
	session->protocol = 0;
	session->generation++;
}

static struct json_object *desired(struct session *session)
{
	if (!session->policy->wifi || session->configuration >= json_object_array_length(session->policy->wifi))
		return NULL;
	return json_object_array_get_idx(session->policy->wifi, session->configuration);
}

static void next_configuration(struct session *session)
{
	if (desired(session))
		phase(session, session->policy->attempted & (1u << session->configuration) ? RECOVER_SEND : PREFLIGHT_SEND);
	else {
		struct json_object *ready;
		json_object_object_get_ex(session->policy->registration, "Ready", &ready);
		phase(session, session->policy->mesh && json_object_get_int(ready) &&
			!(session->policy->attempted & MESH_ATTEMPTED) ? MESH_SEND : IDLE);
	}
}

static void completed(struct session *session, const struct fttr_sync_peer *peer, int slot, int result)
{
	struct json_object *ready;

	if (session->phase == MESH) {
		event("sync_resident_easymesh", session, peer, slot, result, result);
		if (result) session->policy->blocked = true;
		phase(session, IDLE);
	} else if (session->phase == RECOVER) {
		event("sync_resident_recovery", session, peer, slot, result, result);
		if (result) {
			session->policy->blocked = true;
			phase(session, IDLE);
		} else {
			session->configuration++;
			next_configuration(session);
		}
	} else if (session->phase == WRITE) {
		session->write_result = result;
		phase(session, READBACK_SEND);
	} else if (session->phase == READBACK) {
		event("sync_resident_apply", session, peer, slot, session->write_result ? session->write_result : result, result);
		if (result || session->write_result) {
			session->policy->blocked = true;
			phase(session, IDLE);
		} else {
			session->configuration++;
			next_configuration(session);
		}
	} else if (session->phase == PREFLIGHT) {
		json_object_object_get_ex(session->policy->registration, "Ready", &ready);
		if (!result && desired(session) && json_object_get_int(ready) && !session->policy->blocked &&
		    !(session->policy->attempted & (1u << session->configuration))) {
			phase(session, WRITE_SEND);
		} else {
			event("sync_resident_observed", session, peer, slot, result, result);
			phase(session, IDLE);
		}
	}
}

static int receive_message(int fd, const uint8_t source[6], uint16_t ethertype,
		struct session *session, const struct fttr_sync_peer *peer, int slot,
		const uint8_t *frame, size_t length)
{
	struct fttr_sync_message message;
	struct json_object *reply = NULL, *summary = NULL, *observed = NULL;
	uint8_t outgoing[FTTR_SYNC_FRAME_MAX];
	const char *payload;
	int ret;
	ssize_t sent;

	/* Standalone ACKs must not discard a partly received registration/report. */
	if (!(frame[15] & 0xfc))
		ret = fttr_sync_unpack(frame, length, ethertype, &message);
	else
		ret = fttr_sync_receive(&session->receivers[(frame[14] & 15) == FTTR_SYNC_RESPONSE],
			frame, length, ethertype, now_ms(), &message);
	if (ret == -EAGAIN)
		return 0;
	if (ret) {
		revoke(session, peer, slot, ret);
		return 0;
	}
	ret = fttr_sync_register_reply(&message, session->policy->registration, source, &reply);
	if (ret != -EAGAIN) {
		revoke(session, peer, slot, ret ? ret : -ECANCELED);
		if (ret) {
			event("sync_resident_registration_rejected", session, peer, slot, ret, ret);
			return 0;
		}
		payload = json_object_to_json_string_ext(reply, JSON_C_TO_STRING_PLAIN);
		ret = fttr_sync_pack(source, fttr_sync_slave_group, ethertype, slot, FTTR_SYNC_RESPONSE,
			payload, strlen(payload), outgoing, sizeof(outgoing));
		json_object_put(reply);
		if (ret < 0)
			return ret;
		sent = send(fd, outgoing, ret, 0);
		if (sent != ret)
			return sent < 0 ? -errno : -EIO;
		session->registered = true;
		session->protocol = 1;
		session->configuration = 0;
		event("sync_resident_registered", session, peer, slot, 0, 0);
		if (!session->policy->blocked) {
			if (session->policy->attempted & MESH_ATTEMPTED) {
				/* No original readback contract exists for Flag2. A durable
				 * send intent cannot be reclassified as success or replayed. */
				event("sync_resident_easymesh", session, peer, slot, -EINPROGRESS, -EINPROGRESS);
				session->policy->blocked = true;
			} else if (desired(session) || session->policy->mesh)
				next_configuration(session);
			else
				phase(session, PREFLIGHT_SEND);
		}
		return 0;
	}
	if (!session->registered || (session->phase != PREFLIGHT && session->phase != WRITE && session->phase != READBACK && session->phase != RECOVER && session->phase != MESH))
		return 0;
	message.slot = slot;
	ret = fttr_sync_reply_decode(&message, peer->mac, session->phase == MESH ? 2 : 1, session->phase == WRITE || session->phase == MESH,
		&session->ack_seen, &summary, &observed);
	json_object_put(summary);
	if (session->ack_seen)
		session->protocol = 2;
	if (!ret && session->phase != WRITE && session->phase != MESH && desired(session))
		ret = fttr_sync_wifi_check(observed, desired(session), session->phase == READBACK || session->phase == RECOVER);
	json_object_put(observed);
	if (ret != -EAGAIN)
		completed(session, peer, slot, ret);
	return 0;
}

int fttr_sync_controller(int fd, const uint8_t source[6], uint16_t ethertype, struct json_object *policy,
		const char *state_directory)
{
	struct policy_peer configured[FTTR_SYNC_PEERS] = { 0 };
	struct fttr_sync_peer peers[FTTR_SYNC_PEERS] = { 0 };
	struct session *sessions = calloc(FTTR_SYNC_PEERS, sizeof(*sessions));
	struct pollfd descriptor = { .fd = fd, .events = POLLIN };
	struct sigaction action = { .sa_handler = stop }, old_int, old_term;
	uint8_t frame[FTTR_SYNC_FRAME_MAX], sequence = 0;
	int64_t next = 0;
	int ret = -ENOMEM, slot, i, store = -1;

	if (!sessions)
		return ret;
	ret = policies(policy, configured);
	if (ret)
		goto out;
	if (state_directory) {
		store = fttr_sync_store_open(state_directory);
		if (store < 0) { ret = store; goto out; }
		for (slot = 0; slot < FTTR_SYNC_PEERS; slot++) {
			struct policy_peer *peer = &configured[slot];

			if ((!peer->wifi || !json_object_array_length(peer->wifi)) && !peer->mesh)
				continue;
			ret = fttr_sync_store_load(store, source, peer->mac, ethertype, peer->input, &peer->record, &peer->attempted);
			if (ret)
				goto out;
		}
	}
	stopping = 0;
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGINT, &action, &old_int)) { ret = -errno; goto out; }
	if (sigaction(SIGTERM, &action, &old_term)) { ret = -errno; goto restore_int; }
	while (!stopping) {
		int64_t now = now_ms(), wake;
		bool empty = false;
		ssize_t n;

		if (now >= next) {
			fttr_sync_heartbeat(source, ethertype, sequence++, frame);
			n = send(fd, frame, 16, 0);
			if (n != 16) { ret = n < 0 ? -errno : -EIO; break; }
			fttr_sync_peer_age(peers);
			for (slot = 0; slot < FTTR_SYNC_PEERS; slot++)
				if (!peers[slot].online && sessions[slot].policy) {
					revoke(&sessions[slot], &peers[slot], slot, -ENETDOWN);
					event("sync_resident_offline", &sessions[slot], &peers[slot], slot, -ENETDOWN, -ENETDOWN);
					sessions[slot].policy = NULL;
				}
			next = now + FTTR_SYNC_HEARTBEAT_MS;
		}
		for (slot = 0; slot < FTTR_SYNC_PEERS; slot++) {
			struct session *session = &sessions[slot];

			if ((session->receivers[0].count && now >= session->receivers[0].deadline) ||
			    (session->receivers[1].count && now >= session->receivers[1].deadline)) {
				revoke(session, &peers[slot], slot, -ETIMEDOUT);
				continue;
			}
			if ((session->phase == PREFLIGHT || session->phase == WRITE || session->phase == READBACK || session->phase == RECOVER || session->phase == MESH) && now >= session->deadline)
				completed(session, &peers[slot], slot, -ETIMEDOUT);
		}
		/* Bound flood work so heartbeats, expiry and termination still run. */
		for (i = 0; i < 256 && !stopping; i++) {
			struct session *session;
			int match;

			n = recv(fd, frame, sizeof(frame), MSG_DONTWAIT | MSG_TRUNC);
			if (n < 0) {
				if (errno == EAGAIN || errno == EWOULDBLOCK) { empty = true; break; }
				if (errno == EINTR) continue;
				ret = -errno;
				goto restore;
			}
			if (n < 16 || n > (ssize_t)sizeof(frame) || !fttr_sync_destination(frame, source))
				continue;
			slot = fttr_sync_peer_receive(peers, source, ethertype, frame, n);
			if (slot >= 0) {
				session = &sessions[slot];
				if (!session->policy)
					for (match = 0; match < FTTR_SYNC_PEERS; match++)
						if (configured[match].registration && !memcmp(configured[match].mac, peers[slot].mac, 6)) {
							session->policy = &configured[match];
							break;
						}
				continue;
			}
			if ((frame[14] & 15) != FTTR_SYNC_REQUEST && (frame[14] & 15) != FTTR_SYNC_RESPONSE)
				continue;
			for (slot = 0; slot < FTTR_SYNC_PEERS; slot++)
				if (peers[slot].online && sessions[slot].policy && !memcmp(peers[slot].mac, frame + 6, 6))
					break;
			if (slot == FTTR_SYNC_PEERS)
				continue;
			ret = receive_message(fd, source, ethertype, &sessions[slot], &peers[slot], slot, frame, n);
			if (ret)
				goto restore;
		}
		if (!empty)
			continue;
		wake = next;
		for (slot = 0; slot < FTTR_SYNC_PEERS && !stopping; slot++) {
			struct session *session = &sessions[slot];
			enum phase current = session->phase;

			if (current == PREFLIGHT_SEND || current == WRITE_SEND || current == READBACK_SEND || current == RECOVER_SEND || current == MESH_SEND) {
				unsigned int bit = current == MESH_SEND ? MESH_ATTEMPTED : 1u << session->configuration;
				struct json_object *mesh_request = NULL;
				if (session->receivers[0].count || session->receivers[1].count) {
					for (i = 0; i < 2; i++)
						if (session->receivers[i].count && session->receivers[i].deadline < wake)
							wake = session->receivers[i].deadline;
					continue;
				}
				if ((current == WRITE_SEND || current == MESH_SEND) && store >= 0 &&
				    !(session->policy->attempted & bit)) {
					ret = fttr_sync_store_mark(store, peers[slot].mac, session->policy->record,
						session->policy->attempted | bit);
					if (ret)
						goto restore;
					/* fsync can take time. Revisit ingress/liveness before
					 * sending; a queued replacement must cancel this write. */
					session->policy->attempted |= bit;
					wake = 0;
					break;
				}
				if (current == MESH_SEND) {
					mesh_request = json_object_new_object();
					if (!mesh_request) { ret = -ENOMEM; goto restore; }
					json_object_object_add(mesh_request, "act", json_object_new_string("config"));
					json_object_object_add(mesh_request, "EnableEasymesh", json_object_new_string(
						json_object_get_boolean(session->policy->mesh) ? "1" : "0"));
				}
				ret = fttr_sync_request(fd, source, peers[slot].mac, ethertype, slot, current == MESH_SEND ? 2 : 1,
					mesh_request ? mesh_request : current == WRITE_SEND ? desired(session) : NULL);
				json_object_put(mesh_request);
				if (ret)
					goto restore;
				if (current == WRITE_SEND || current == MESH_SEND)
					session->policy->attempted |= bit;
				phase(session, current == MESH_SEND ? MESH : current == PREFLIGHT_SEND ? PREFLIGHT : current == WRITE_SEND ? WRITE : current == RECOVER_SEND ? RECOVER : READBACK);
				session->deadline = now_ms() + (current == MESH_SEND ? 9000 : 3000);
			}
			if (session->phase != IDLE && session->deadline < wake)
				wake = session->deadline;
		}
		now = now_ms();
		ret = poll(&descriptor, 1, wake > now ? wake - now : 0);
		if (ret < 0) { if (errno == EINTR) { ret = 0; continue; } ret = -errno; break; }
		if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) { ret = -EIO; break; }
		ret = 0;
	}
restore:
	for (slot = 0; slot < FTTR_SYNC_PEERS; slot++)
		if (sessions[slot].phase != IDLE)
			revoke(&sessions[slot], &peers[slot], slot, ret ? ret : -ECANCELED);
	sigaction(SIGTERM, &old_term, NULL);
restore_int:
	sigaction(SIGINT, &old_int, NULL);
out:
	for (slot = 0; slot < FTTR_SYNC_PEERS; slot++)
		json_object_put(configured[slot].record);
	if (store >= 0) close(store);
	free(sessions);
	return ret;
}
