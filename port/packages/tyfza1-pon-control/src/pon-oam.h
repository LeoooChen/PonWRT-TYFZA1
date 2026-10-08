/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef TYFZA1_PON_OAM_H
#define TYFZA1_PON_OAM_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PON_OAM_FRAME_MAX 1514

struct pon_oam_identity {
	uint8_t mac[6];
	uint8_t loid[24];
	uint8_t password[12];
	uint8_t vendor[4];
	uint8_t model[4];
	uint8_t equipment[20];
	uint8_t hardware[16];
	uint8_t software[16];
	bool loid_configured;
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
	bool have_peer;
	bool have_sent;
	bool local_echoed;
	bool extension_selected;
	bool extension_advertised;
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
#endif
