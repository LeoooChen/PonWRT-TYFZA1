/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef TYFZA1_PON_OAM_H
#define TYFZA1_PON_OAM_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PON_OAM_FRAME_MAX 1514

/* Receive result: negative errno = invalid, 0 = ignored, 1 = handled without
 * a reply; 60..FRAME_MAX = validated reply length. Only handled PDUs are live. */
enum pon_oam_receive_result {
	PON_OAM_IGNORED,
	PON_OAM_HANDLED,
};

struct pon_oam_request {
	uint8_t version;
	uint8_t branch;
	uint16_t leaf;
	const uint8_t *object;
	size_t object_size;
	const uint8_t *parameters;
	size_t parameter_size;
};

enum pon_oam_alarm_operation {
	PON_ALARM_STATUS_GET,
	PON_ALARM_STATUS_SET,
	PON_ALARM_THRESHOLD_GET,
	PON_ALARM_THRESHOLD_SET,
};

struct pon_oam_alarm {
	uint16_t object_type;
	uint32_t instance;
	uint16_t id;
	uint32_t enabled;
	uint32_t threshold, clear_threshold;
	bool active;
	uint8_t info_length;
	uint8_t info[8];
};

#define PON_OAM_ALARM_BATCH 60

struct pon_oam_identity {
	uint8_t mac[6];
	uint8_t loid[24];
	uint8_t password[12];
	uint8_t vendor[4];
	uint8_t model[4];
	uint8_t equipment[20];
	uint8_t hardware[16];
	uint8_t software[16];
	uint8_t firmware[3];
	bool loid_configured;
	void *backend_context;
	int (*get_parameter)(void *context, const struct pon_oam_request *request,
		uint8_t *value, size_t capacity);
	uint8_t (*set_parameter)(void *context, const struct pon_oam_request *request,
		const uint8_t *value, size_t size);
	int (*churning_key)(void *context, uint8_t index, uint8_t key[9], size_t length);
	int (*alarm)(void *context, struct pon_oam_alarm *alarm, enum pon_oam_alarm_operation operation);
	int (*alarms_poll)(void *context, unsigned int generation,
		struct pon_oam_alarm *events, size_t capacity);
	void (*alarms_sent)(void *context, const struct pon_oam_alarm *events, size_t count);
};

enum pon_oam_auth {
	PON_AUTH_WAITING,
	PON_AUTH_CHALLENGE,
	PON_AUTH_ACCEPTED,
	PON_AUTH_REJECTED,
};

struct pon_oam_session {
	uint8_t peer_local[14];
	uint8_t last_local[14];
	uint8_t extension_oui[3];
	uint8_t extension_version;
	uint16_t peer_flags;
	uint16_t event_sequence;
	bool have_peer;
	bool have_sent;
	bool local_echoed;
	bool extension_selected;
	bool extension_advertised;
	uint8_t key[9];
	uint8_t key_length;
	uint8_t key_index;
	enum pon_oam_auth auth;
	uint8_t auth_failure;
	unsigned int generation;
	unsigned int rx_frames;
	unsigned int tx_frames;
	unsigned int auth_challenges;
	unsigned int auth_results;
	unsigned int auth_successes;
	unsigned int auth_failures;
};

void pon_oam_reset(struct pon_oam_session *session);
uint16_t pon_oam_flags(const struct pon_oam_session *session);
size_t pon_oam_information(struct pon_oam_session *session, const struct pon_oam_identity *identity,
			   uint8_t output[PON_OAM_FRAME_MAX]);
void pon_oam_information_sent(struct pon_oam_session *session, const uint8_t *frame);
int pon_oam_receive(struct pon_oam_session *session, const struct pon_oam_identity *identity,
		    const uint8_t *input, size_t length, uint8_t output[PON_OAM_FRAME_MAX]);
int pon_oam_events(struct pon_oam_session *session, const struct pon_oam_identity *identity,
	struct pon_oam_alarm events[PON_OAM_ALARM_BATCH], size_t *count,
	uint8_t output[PON_OAM_FRAME_MAX]);
void pon_oam_events_sent(struct pon_oam_session *session, const struct pon_oam_identity *identity,
	const struct pon_oam_alarm *events, size_t count);
#endif
