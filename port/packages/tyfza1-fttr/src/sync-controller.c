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
enum phase { IDLE, PREFLIGHT_SEND, PREFLIGHT, WRITE_SEND, WRITE, READBACK_SEND, READBACK, RECOVER_SEND, RECOVER, MESH_SEND, MESH, INFO_SEND, INFO, MESH_READ_SEND, MESH_READ };
#define MESH_ATTEMPTED (1U << 16)

struct policy_peer {
	uint8_t mac[6];
	struct json_object *registration, *wifi, *mesh, *radios, *time, *iptv, *ports, *password, *qos;
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
	bool mesh_state, mesh_recovery, mesh_ack;
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
	struct json_object *list, *entry, *mac, *registration, *wifi, *revision, *mesh, *radios, *time, *iptv, *ports, *password, *qos;
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
		radios=NULL;
		if (json_object_object_get_ex(entry,"Radios",&radios)) {
			if (!json_object_is_type(radios,json_type_array) || json_object_array_length(radios)>2) return -EINVAL;
			unsigned seen=0;
			for (size_t r=0;r<json_object_array_length(radios);r++) {
				struct json_object *row=json_object_array_get_idx(radios,r),*type;
				if (fttr_sync_radio_validate(row)) return -EINVAL;
				json_object_object_get_ex(row,"RadioType",&type);
				unsigned bit=!strcmp(json_object_get_string(type),"2.4G")?1:2;
				if (seen&bit) return -EINVAL;
				seen|=bit;
			}
			j++;
		}
  time=NULL;
  if(json_object_object_get_ex(entry,"Time",&time)) {
   if(fttr_sync_time_validate(time))return -EINVAL;
   j++;
  }
  iptv=NULL;
  if(json_object_object_get_ex(entry,"IPTV",&iptv)) {
   if(fttr_sync_iptv_validate(iptv))return -EINVAL;
   j++;
  }
  ports=NULL;
  if(json_object_object_get_ex(entry,"IPTVPorts",&ports)) {
   if(iptv || fttr_sync_iptv_ports_validate(ports))return -EINVAL;
   j++;
  }
  password=NULL;
  if(json_object_object_get_ex(entry,"WebPassword",&password)) {
   if(fttr_sync_web_password_validate(password))return -EINVAL;
   j++;
  }
		qos = NULL;
		if (json_object_object_get_ex(entry, "QoS", &qos)) {
			int error = fttr_sync_qos_validate(qos);
			if (error) return error;
			j++;
		}
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
		peers[i].radios = radios;
		peers[i].time = time;peers[i].iptv=iptv;peers[i].ports=ports;peers[i].password=password;peers[i].qos=qos;
		peers[i].input = entry;
	}
	return 0;
}

int fttr_sync_controller_policy(struct json_object *policy)
{
	struct policy_peer peers[FTTR_SYNC_PEERS] = { 0 };

	return policies(policy, peers);
}

static bool time_configuration(const struct session *session)
{
 unsigned int wifi=session->policy->wifi?json_object_array_length(session->policy->wifi):0;
 unsigned int radios=session->policy->radios?json_object_array_length(session->policy->radios):0;
 return session->policy->time && session->configuration==wifi+radios;
}

static bool iptv_configuration(const struct session *session)
{
 unsigned int wifi=session->policy->wifi?json_object_array_length(session->policy->wifi):0;
 unsigned int radios=session->policy->radios?json_object_array_length(session->policy->radios):0;
 return session->policy->iptv && session->configuration==wifi+radios+(session->policy->time?1:0);
}

static bool ports_configuration(const struct session *session)
{
 unsigned int wifi=session->policy->wifi?json_object_array_length(session->policy->wifi):0;
 unsigned int radios=session->policy->radios?json_object_array_length(session->policy->radios):0;
 return session->policy->ports && session->configuration==wifi+radios+(session->policy->time?1:0);
}

static bool password_configuration(const struct session *session)
{
 unsigned int wifi=session->policy->wifi?json_object_array_length(session->policy->wifi):0;
 unsigned int radios=session->policy->radios?json_object_array_length(session->policy->radios):0;
 return session->policy->password && session->configuration==wifi+radios+(session->policy->time?1:0)+((session->policy->iptv || session->policy->ports)?1:0);
}

static bool qos_configuration(const struct session *session)
{
	unsigned int wifi = session->policy->wifi ? json_object_array_length(session->policy->wifi) : 0;
	unsigned int radios = session->policy->radios ? json_object_array_length(session->policy->radios) : 0;
	return session->policy->qos && session->configuration == wifi + radios +
		!!session->policy->time + !!(session->policy->iptv || session->policy->ports) + !!session->policy->password;
}

static bool radio_configuration(const struct session *session)
{
 unsigned int wifi=session->policy->wifi?json_object_array_length(session->policy->wifi):0;
 unsigned int radios=session->policy->radios?json_object_array_length(session->policy->radios):0;
 return session->configuration>=wifi && session->configuration<wifi+radios;
}

static struct json_object *desired(const struct session *session)
{
	unsigned int wifi=session->policy->wifi?json_object_array_length(session->policy->wifi):0;
	if (session->configuration<wifi) return json_object_array_get_idx(session->policy->wifi,session->configuration);
	unsigned int radio=session->configuration-wifi;
	return session->policy->radios && radio<json_object_array_length(session->policy->radios)?
		json_object_array_get_idx(session->policy->radios,radio):time_configuration(session)?session->policy->time:iptv_configuration(session)?session->policy->iptv:ports_configuration(session)?session->policy->ports:password_configuration(session)?session->policy->password:qos_configuration(session)?session->policy->qos:NULL;
}

static unsigned int configuration_bit(const struct session *session)
{
	unsigned int wifi=session->policy->wifi?json_object_array_length(session->policy->wifi):0;
	if(qos_configuration(session))return 1U<<23;
	if(password_configuration(session))return 1U<<22;
	if(ports_configuration(session))return 1U<<21;
	if(iptv_configuration(session))return 1U<<20;
	if(time_configuration(session))return 1U<<19;
	return 1U << (session->configuration<wifi ? session->configuration : 17+session->configuration-wifi);
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
	if (!strcmp(name, "sync_resident_qos")) {
		json_object_object_add(value, "setting", json_object_new_string("QoS"));
		json_object_object_add(value, "ack_seen", json_object_new_boolean(session->ack_seen));
		json_object_object_add(value, "prior_write_outcome_unknown", json_object_new_boolean(session->phase == RECOVER_SEND));
		json_object_object_add(value, "write_replayed", json_object_new_boolean(0));
		json_object_object_add(value, "scheduler_verified", json_object_new_boolean(0));
	}
	if (!strcmp(name, "sync_resident_easymesh")) {
		json_object_object_add(value, "requested_enabled", json_object_get(session->policy->mesh));
		json_object_object_add(value, "ack_seen", json_object_new_boolean(session->mesh_ack));
		json_object_object_add(value, "completion_reported", json_object_new_boolean(!session->mesh_recovery && !session->write_result));
		json_object_object_add(value, "readback_result", json_object_new_int(readback));
		json_object_object_add(value, "reported_state_matches", json_object_new_boolean(!readback));
		json_object_object_add(value, "recovered_read_only", json_object_new_boolean(session->mesh_recovery));
		json_object_object_add(value, "write_replayed", json_object_new_boolean(0));
	}
	if (!strcmp(name, "sync_resident_recovery")) {
		struct json_object *index;

  if(password_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("WebPassword"));
   json_object_object_add(value,"password_matches",json_object_new_boolean(!readback));
  } else if(ports_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("IPTVPorts"));
   json_object_object_add(value,"ports_match",json_object_new_boolean(!readback));
   json_object_object_add(value,"vlan_verified",json_object_new_boolean(0));
   json_object_object_add(value,"traffic_verified",json_object_new_boolean(0));
  } else if(iptv_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("IPTV"));
   json_object_object_add(value,"traffic_verified",json_object_new_boolean(0));
  } else if(time_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("Time"));
   json_object_object_add(value,"clock_synchronized",json_object_new_boolean(0));
  } else {
   bool radio=radio_configuration(session);
   json_object_object_get_ex(desired(session),radio?"RadioType":"SSIDIndex",&index);
   json_object_object_add(value,radio?"radio_type":"ssid_index",json_object_get(index));
  }
		json_object_object_add(value, "prior_write_outcome_unknown", json_object_new_boolean(1));
		json_object_object_add(value, "write_replayed", json_object_new_boolean(0));
		json_object_object_add(value, "readable_fields_match", json_object_new_boolean(!result));
	}
	if (!strcmp(name, "sync_resident_apply")) {
		struct json_object *index;

  if(password_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("WebPassword"));
   json_object_object_add(value,"password_matches",json_object_new_boolean(!readback));
  } else if(ports_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("IPTVPorts"));
   json_object_object_add(value,"ports_match",json_object_new_boolean(!readback));
   json_object_object_add(value,"vlan_verified",json_object_new_boolean(0));
   json_object_object_add(value,"traffic_verified",json_object_new_boolean(0));
  } else if(iptv_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("IPTV"));
   json_object_object_add(value,"traffic_verified",json_object_new_boolean(0));
  } else if(time_configuration(session)) {
   json_object_object_add(value,"setting",json_object_new_string("Time"));
   json_object_object_add(value,"clock_synchronized",json_object_new_boolean(0));
  } else {
   bool radio=radio_configuration(session);
   json_object_object_get_ex(desired(session),radio?"RadioType":"SSIDIndex",&index);
   json_object_object_add(value,radio?"radio_type":"ssid_index",json_object_get(index));
  }
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

static void next_configuration(struct session *session)
{
	if (desired(session)) {
		struct json_object *ready;
		json_object_object_get_ex(session->policy->registration, "Ready", &ready);
		if (session->policy->attempted & configuration_bit(session))
			phase(session, RECOVER_SEND);
		else if (qos_configuration(session))
			phase(session, json_object_get_int(ready) ? WRITE_SEND : IDLE);
		else
			phase(session, PREFLIGHT_SEND);
	}
	else {
		struct json_object *ready;
		json_object_object_get_ex(session->policy->registration, "Ready", &ready);
		session->mesh_recovery = session->policy->attempted & MESH_ATTEMPTED;
		session->mesh_ack = false; session->write_result = -EINPROGRESS;
		phase(session, session->policy->mesh && (json_object_get_int(ready) || session->mesh_recovery) ? INFO_SEND : IDLE);
	}
}

static void completed(struct session *session, const struct fttr_sync_peer *peer, int slot, int result)
{
	struct json_object *ready;

	if (session->phase == INFO) {
		if (!result && !session->mesh_recovery) phase(session, MESH_SEND);
		else {
			if (!result && session->mesh_state != json_object_get_boolean(session->policy->mesh)) result = -EUCLEAN;
			event("sync_resident_easymesh", session, peer, slot, result, result);
			if (result) session->policy->blocked = true;
			phase(session, IDLE);
		}
	} else if (session->phase == MESH) {
		session->write_result = result; session->mesh_ack = session->ack_seen;
		phase(session, MESH_READ_SEND);
	} else if (session->phase == MESH_READ) {
		if (!result && session->mesh_state != json_object_get_boolean(session->policy->mesh)) result = -EUCLEAN;
		event("sync_resident_easymesh", session, peer, slot, session->write_result ? session->write_result : result, result);
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
		if (qos_configuration(session)) {
			event("sync_resident_qos", session, peer, slot, result, -EOPNOTSUPP);
			if (result) {
				session->policy->blocked = true;
				phase(session, IDLE);
			} else {
				session->configuration++;
				next_configuration(session);
			}
		} else phase(session, READBACK_SEND);
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
		    !(session->policy->attempted & configuration_bit(session))) {
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
			if (desired(session) || session->policy->mesh)
				next_configuration(session);
			else
				phase(session, PREFLIGHT_SEND);
		}
		return 0;
	}
	if (!session->registered || (session->phase != PREFLIGHT && session->phase != WRITE && session->phase != READBACK && session->phase != RECOVER && session->phase != MESH && session->phase != INFO && session->phase != MESH_READ))
		return 0;
	message.slot = slot;
	bool info_phase = session->phase == INFO || session->phase == MESH_READ;
	bool qos=desired(session) && qos_configuration(session);
 bool password=desired(session) && password_configuration(session);
 bool ports=desired(session) && ports_configuration(session);
 bool iptv=desired(session) && iptv_configuration(session);
 bool time=desired(session) && time_configuration(session);
 bool radio=desired(session) && radio_configuration(session);
	ret = fttr_sync_reply_decode(&message, peer->mac, info_phase ? 3 : session->phase == MESH ? 2 : qos?15:password?(session->phase==WRITE?14:13):ports?(session->phase==WRITE?12:11):iptv?(session->phase==WRITE?10:9):time?(session->phase==WRITE?8:7):radio?(session->phase==WRITE?5:4):1, session->phase == WRITE || session->phase == MESH,
		&session->ack_seen, &summary, &observed);
	json_object_put(summary);
	if (session->ack_seen)
		session->protocol = 2;
	if (!ret && info_phase) ret = fttr_sync_info_mesh(observed, &session->mesh_state);
	if (!ret && !info_phase && session->phase != WRITE && session->phase != MESH && desired(session))
		ret = password?fttr_sync_web_password_check(observed,desired(session),session->phase==READBACK || session->phase==RECOVER):ports?fttr_sync_iptv_ports_check(observed,desired(session),session->phase==READBACK || session->phase==RECOVER):iptv?fttr_sync_iptv_check(observed,desired(session),session->phase==READBACK || session->phase==RECOVER):time?fttr_sync_time_check(observed,desired(session),session->phase==READBACK || session->phase==RECOVER):radio?fttr_sync_radio_check(observed,desired(session),session->phase==READBACK || session->phase==RECOVER):
			fttr_sync_wifi_check(observed, desired(session), session->phase == READBACK || session->phase == RECOVER);
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

			if ((!peer->wifi || !json_object_array_length(peer->wifi)) && !peer->mesh &&
			    (!peer->radios || !json_object_array_length(peer->radios)) && !peer->time && !peer->iptv && !peer->ports && !peer->password && !peer->qos)
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
			if ((session->phase == PREFLIGHT || session->phase == WRITE || session->phase == READBACK || session->phase == RECOVER || session->phase == MESH || session->phase == INFO || session->phase == MESH_READ) && now >= session->deadline)
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

			if (current == PREFLIGHT_SEND || current == WRITE_SEND || current == READBACK_SEND || current == RECOVER_SEND || current == MESH_SEND || current == INFO_SEND || current == MESH_READ_SEND) {
				unsigned int bit = current == MESH_SEND ? MESH_ATTEMPTED : desired(session)?configuration_bit(session):0;
				struct json_object *mesh_request = NULL;
				if (session->receivers[0].count || session->receivers[1].count) {
					for (i = 0; i < 2; i++)
						if (session->receivers[i].count && session->receivers[i].deadline < wake)
							wake = session->receivers[i].deadline;
					continue;
				}
				/* QosSync has no proven read RPC. Retained intent prevents a
				 * second write after a crash, including a lost successful ACK. */
				if (current == RECOVER_SEND && qos_configuration(session)) {
					event("sync_resident_qos", session, &peers[slot], slot, -EINPROGRESS, -EOPNOTSUPP);
					session->policy->blocked = true;
					phase(session, IDLE);
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
				bool qos=desired(session) && qos_configuration(session);
 bool password=desired(session) && password_configuration(session);
 bool ports=desired(session) && ports_configuration(session);
 bool iptv=desired(session) && iptv_configuration(session);
 bool time=desired(session) && time_configuration(session);
 bool radio=desired(session) && radio_configuration(session);
				ret = fttr_sync_request(fd, source, peers[slot].mac, ethertype, slot, (current == INFO_SEND || current == MESH_READ_SEND) ? 3 : current == MESH_SEND ? 2 : qos?15:password?(current==WRITE_SEND?14:13):ports?(current==WRITE_SEND?12:11):iptv?(current==WRITE_SEND?10:9):time?(current==WRITE_SEND?8:7):radio?(current==WRITE_SEND?5:4):1,
					mesh_request ? mesh_request : current == WRITE_SEND ? desired(session) : NULL);
				json_object_put(mesh_request);
				if (ret)
					goto restore;
				if (current == WRITE_SEND || current == MESH_SEND)
					session->policy->attempted |= bit;
				phase(session, current == INFO_SEND ? INFO : current == MESH_READ_SEND ? MESH_READ : current == MESH_SEND ? MESH : current == PREFLIGHT_SEND ? PREFLIGHT : current == WRITE_SEND ? WRITE : current == RECOVER_SEND ? RECOVER : READBACK);
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
