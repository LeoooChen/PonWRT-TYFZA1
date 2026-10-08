/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Wire vectors normalized from pon-fw-r1's CTC/802.3ah tests to plain Ethernet. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "pon-oam.h"

static void message(uint8_t *frame, const uint8_t *payload, size_t size)
{
	memset(frame, 0, PON_OAM_FRAME_MAX);
	frame[12] = 0x88; frame[13] = 9; frame[14] = 3; frame[17] = 0xfe;
	memcpy(frame + 18, payload, size);
}

static void test_set_selectors(struct pon_oam_session *session, const struct pon_oam_identity *identity)
{
	struct pon_oam_session selected = *session;
	uint8_t frame[PON_OAM_FRAME_MAX], reply[PON_OAM_FRAME_MAX];
	/* Two port instances and both selector widths, followed by Set variables. */
	const uint8_t request[] = {0x11,0x11,0x11,3,
		0x37,0,1,4,1,0,0,7, 0xc7,0,0x25,1,2,
		0x36,0,1,1,3, 0xc7,0,0x13,2,0,0xe3, 0};
	const uint8_t response[] = {0x11,0x11,0x11,4,
		0x37,0,1,4,1,0,0,7, 0xc7,0,0x25,0x87,
		0x36,0,1,1,3, 0xc7,0,0x13,0x87, 0};

	session = &selected;
	message(frame, request, sizeof(request));
	assert(pon_oam_receive(session, identity, frame, 60, reply) == 60);
	assert(!memcmp(reply + 18, response, sizeof(response)));
	frame[25] = 3; /* 0x37 needs its four-byte instance width. */
	assert(pon_oam_receive(session, identity, frame, 60, reply) == -EPROTO);
	message(frame, request, sizeof(request));
	assert(pon_oam_receive(session, identity, frame, 29, reply) == -EPROTO);
	frame[22] = 0x36; frame[25] = 1;
	assert(pon_oam_receive(session, identity, frame, 26, reply) == -EPROTO);
}

int main(void)
{
	struct pon_oam_identity identity = {.loid_configured = true};
	struct pon_oam_session session = {};
	uint8_t frame[PON_OAM_FRAME_MAX], reply[PON_OAM_FRAME_MAX], local[14];
	const uint8_t challenge[] = {0x11,0x11,0x11,5,1,0,1,1};
	const uint8_t auth_response[] = {0x11,0x11,0x11,5,2,0,37,1};
	const uint8_t accepted[] = {0x11,0x11,0x11,5,3,0,0};
	const uint8_t rejected[] = {0x11,0x11,0x11,5,4,0,1,7};
	const uint8_t get[] = {0x11,0x11,0x11,1,0xc7,0,1,0xc7,0,2,0};
	int length;

	memcpy(identity.mac, "\x02\x11\x22\x33\x44\x55", 6);
	memcpy(identity.loid, "0012345678", 10);
	memcpy(identity.vendor, "TEST", 4);
	memcpy(identity.model, "FZA1", 4);
	memcpy(identity.hardware, "HW-test-123", 11);
	memcpy(identity.software, "SW-test-456", 11);
	memcpy(identity.equipment, "TYFZA1", 6);
	pon_oam_reset(&session);
	assert(pon_oam_information(&session, &identity, frame) == 60);
	assert(frame[17] == 0 && frame[18] == 1 && frame[19] == 16);
	assert(frame[21] == 0 && frame[22] == 1 && frame[25] == 5 && frame[26] == 0xee);
	memcpy(local, frame + 20, 14);
	pon_oam_information_sent(&session, frame);
	/* Peer Local first, then a genuine Remote echo of our transmitted TLV. */
	frame[16] = 8;
	frame[21] = 2; frame[22] = 3;
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == PON_OAM_HANDLED);
	assert(!session.local_echoed && pon_oam_flags(&session) == 0x28);
	frame[16] = 16;
	frame[34] = 2; frame[35] = 16; memcpy(frame + 36, local, 14);
	memcpy(frame + 50, "\xfe\x0b\x11\x11\x11\x01\x30\x11\x11\x11\x30\0\0", 13);
	assert(pon_oam_receive(&session, &identity, frame, 63, reply) == PON_OAM_HANDLED);
	assert(session.local_echoed && pon_oam_flags(&session) == 0x50);
	test_set_selectors(&session, &identity);
	assert(pon_oam_information(&session, &identity, reply) == 63);
	assert(!memcmp(reply + 50, "\xfe\x0b\x11\x11\x11\x01\0\x11\x11\x11\x30", 11));
	/* Blank password is a valid supplied value; encode all twelve zero bytes. */
	message(frame, challenge, sizeof(challenge));
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == 62);
	assert(!memcmp(reply + 18, auth_response, sizeof(auth_response)));
	assert(!memcmp(reply + 26, identity.loid, 24));
	for (unsigned int i = 50; i < 62; i++) assert(reply[i] == 0);
	assert(session.auth == PON_AUTH_CHALLENGE);
	message(frame, accepted, sizeof(accepted));
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == PON_OAM_HANDLED);
	assert(session.auth == PON_AUTH_ACCEPTED && session.auth_results == 1);
	/* Live OLT: five advertised versions and revision1 -> revision0 after
	 * authentication, with the stable flag and the same Remote echo. */
	{
		struct pon_oam_session live = session;
		uint8_t info[PON_OAM_FRAME_MAX];
		pon_oam_information(&live, &identity, info);
		memcpy(info + 20, live.peer_local, 14);
		info[21] ^= 1;
		info[16] = 0x50;
		info[34] = 2; info[35] = 16;
		memcpy(info + 36, live.last_local, 14);
		memcpy(info + 50, "\xfe\x1b\x11\x11\x11\x01\x30\x11\x11\x11\x30\x11\x11\x11\x21\x11\x11\x11\x20\x11\x11\x11\x13\x11\x11\x11\x01\0\0", 29);
		assert(pon_oam_receive(&live, &identity, info, 79, reply) == PON_OAM_HANDLED);
		assert(live.local_echoed && live.generation == session.generation);
		assert(live.auth == PON_AUTH_ACCEPTED && live.auth_successes == 1);
		assert(live.extension_advertised && live.extension_version == 0x30);
		assert(pon_oam_information(&live, &identity, reply) == 63);
		assert(reply[51] == 11 && reply[60] == 0x30);
		info[51] = 26;
		assert(pon_oam_receive(&live, &identity, info, 79, reply) == -EPROTO);
	}
	/* Invalid/ignored PDUs must remain distinct from validated peer activity. */
	{
		struct pon_oam_session live = session;
		message(frame, accepted, sizeof(accepted));
		for (size_t size = 18; size < 22; size++)
			assert(pon_oam_receive(&live, &identity, frame, size, reply) == -EMSGSIZE);
		frame[17] = 2;
		assert(pon_oam_receive(&live, &identity, frame, 60, reply) == PON_OAM_IGNORED);
		frame[17] = 0xfe; frame[18] = 0x12;
		assert(pon_oam_receive(&live, &identity, frame, 60, reply) == PON_OAM_IGNORED);
		frame[18] = 0x11; frame[21] = 0x7f;
		assert(pon_oam_receive(&live, &identity, frame, 60, reply) == PON_OAM_IGNORED);
		frame[21] = 1; memset(frame + 22, 0, sizeof(frame) - 22);
		assert(pon_oam_receive(&live, &identity, frame, 60, reply) == PON_OAM_IGNORED);
		frame[21] = 3;
		assert(pon_oam_receive(&live, &identity, frame, 60, reply) == PON_OAM_IGNORED);
		assert(live.auth == PON_AUTH_ACCEPTED && live.auth_results == session.auth_results);
		assert(live.extension_selected == session.extension_selected);
	}
	/* Same peer Local, but Remote echo withdrawn: no stale auth or selection. */
	for (unsigned int mismatch = 0; mismatch < 2; mismatch++) {
		struct pon_oam_session regressed = session;
		uint8_t information[PON_OAM_FRAME_MAX];
		unsigned int generation = regressed.generation;

		regressed.extension_selected = true;
		regressed.auth_failure = 7;
		pon_oam_information(&regressed, &identity, information);
		memcpy(information + 20, regressed.peer_local, 14);
		information[16] = 8;
		memset(information + 34, 0, sizeof(information) - 34);
		if (mismatch) {
			information[34] = 2; information[35] = 16;
			memcpy(information + 36, regressed.last_local, 14);
			information[38] ^= 1;
		}
		assert(pon_oam_receive(&regressed, &identity, information, 60, reply) == PON_OAM_HANDLED);
		assert(!regressed.local_echoed && !regressed.have_sent);
		assert(regressed.auth == PON_AUTH_WAITING && !regressed.auth_failure);
		assert(!regressed.extension_selected && !regressed.extension_advertised);
		assert(!regressed.extension_version && regressed.auth_successes == 1);
		assert(regressed.generation == generation + 1);
		/* Continuous EVAL messages do not inherit success or rotate revisions. */
		assert(pon_oam_receive(&regressed, &identity, information, 60, reply) == PON_OAM_HANDLED);
		assert(regressed.generation == generation + 1);
	}
	message(frame, challenge, sizeof(challenge));
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == 62);
	assert(session.auth == PON_AUTH_CHALLENGE);
	message(frame, rejected, sizeof(rejected));
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == PON_OAM_HANDLED);
	assert(session.auth == PON_AUTH_REJECTED && session.auth_failure == 7);
	assert(session.auth_successes == 1 && session.auth_failures == 1);
	/* Version parameters reach the actual C7 identity and firmware Get responses. */
	message(frame, get, sizeof(get));
	length = pon_oam_receive(&session, &identity, frame, 60, reply);
	assert(length == 100 && reply[21] == 2 && reply[25] == 54);
	assert(!memcmp(reply + 26, "TESTFZA1", 8));
	assert(!memcmp(reply + 34, identity.mac, 6));
	assert(!memcmp(reply + 40, identity.hardware, 8));
	assert(!memcmp(reply + 48, identity.software, 16));
	assert(!memcmp(reply + 64, identity.equipment, 16));
	assert(reply[83] == 16 && !memcmp(reply + 84, identity.software, 16));
	/* CTC 00:0d:b6 uses the same typed authentication payload. */
	message(frame, challenge, sizeof(challenge));
	memcpy(frame + 18, "\0\x0d\xb6", 3);
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == 62);
	assert(!memcmp(reply + 18, frame + 18, 3));
	frame[23] = 1; frame[24] = 1;
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == 62);
	assert(!memcmp(reply + 26, identity.loid, 24));
	frame[23] = 0; frame[24] = 1;
	/* Unsupported authentication type gets a typed NAK without identity bytes. */
	frame[25] = 2;
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == 60);
	assert(reply[24] == 2 && reply[25] == 2 && reply[26] == 2);
	for (unsigned int i = 27; i < 60; i++) assert(reply[i] == 0);
	/* Protocol truncation never accepts an auth result or overreads a TLV. */
	frame[23] = 0xff; frame[24] = 0xff;
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == -EMSGSIZE);
	message(frame, accepted, sizeof(accepted));
	assert(pon_oam_receive(&session, &identity, frame, 24, reply) == -EMSGSIZE);
	for (size_t i = 0; i < 18; i++)
		assert(pon_oam_receive(&session, &identity, frame, i, reply) == -EPROTO);
	/* Link reset preserves diagnostic history and revokes the current success. */
	pon_oam_reset(&session);
	assert(session.auth == PON_AUTH_WAITING && session.auth_results == 2);
	assert(!session.local_echoed && session.generation == 2);
	assert(pon_oam_receive(&session, &identity, frame, 60, reply) == -EAGAIN);
	pon_oam_information(&session, &identity, frame);
	assert(frame[22] == 2 && memcmp(frame + 20, local, 14));
	puts("OAM discovery, blank-password LOID, dynamic versions, CTC OUIs and session reset passed");
	return 0;
}
