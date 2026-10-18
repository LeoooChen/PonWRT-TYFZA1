/* SPDX-License-Identifier: GPL-2.0-or-later */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "pon-oam.h"

static unsigned int random_calls;
static ssize_t key_random(void *buffer, size_t length, unsigned int flags)
{
	unsigned char *key = buffer;

	(void)flags;
	random_calls++;
	for (size_t i = 0; i < length; i++)
		key[i] = random_calls + i;
	return length;
}
#define getrandom key_random
#include "../port/packages/tyfza1-pon-control/src/pon-backend.c"
#undef getrandom

int main(void)
{
	static const char *modes[] = {"epon", "epon-d10g", "epon-bi10g"};
	char temporary[] = "/tmp/tyfza1-epon-key-XXXXXX", path[128], mpcp[128];
	struct pon_backend backend = {};
	struct pon_oam_identity identity = {};
	uint8_t request[60] = {}, reply[PON_OAM_FRAME_MAX], previous[9], installed[19];
	FILE *file;

	assert(mkdtemp(temporary));
	snprintf(mpcp, sizeof(mpcp), "%s/mpcp", temporary);
	assert(!pon_backend_init(&backend, mpcp));
	backend.llid = 7;
	backend.epoch = 0x102030405060708;
	pon_backend_bind(&identity, &backend);
	memcpy(request + 12, "\x88\x09\x03", 3);
	request[17] = 0xfe;
	memcpy(request + 18, "\x11\x11\x11\x09\0\0", 6);
	for (unsigned int mode = 0; mode < 3; mode++) {
		struct pon_oam_session session = {.local_echoed = true};
		unsigned int width = mode ? 9 : 3, calls = random_calls;

		snprintf(path, sizeof(path), "%s/serdes", temporary);
		file = fopen(path, "w");
		assert(file && fprintf(file, "mode=%s configured=1\n", modes[mode]) > 0);
		assert(!fclose(file));
		request[23] = 0;
		assert(pon_oam_receive(&session, &identity, request, sizeof(request), reply) == 60);
		assert(random_calls == calls + 1 && reply[21] == 9 && reply[22] == 1 && reply[23] == 1);
		memcpy(previous, reply + 24, width);
		snprintf(path, sizeof(path), "%s/epon_key", temporary);
		file = fopen(path, "rb");
		assert(file && fread(installed, 1, sizeof(installed), file) == width + 10);
		assert(!fclose(file));
		assert(installed[0] == 7 && installed[1] == 1 &&
			!memcmp(installed + 2, "\x01\x02\x03\x04\x05\x06\x07\x08", 8) &&
			!memcmp(installed + 10, previous, width));
		assert(pon_oam_receive(&session, &identity, request, sizeof(request), reply) == 60);
		assert(random_calls == calls + 1 && !memcmp(reply + 24, previous, width));
		request[23] = 1;
		assert(pon_oam_receive(&session, &identity, request, sizeof(request), reply) == 60);
		assert(random_calls == calls + 2 && reply[23] == 0);
		pon_oam_reset(&session);
		assert(!session.key_length && !memcmp(session.key, (uint8_t[9]){}, 9));
		session.local_echoed = true;
		assert(!unlink(path) && !mkdir(path, 0700));
		assert(pon_oam_receive(&session, &identity, request, sizeof(request), reply) < 0);
		assert(!session.key_length && !session.extension_selected);
		assert(!rmdir(path));
		assert(pon_oam_receive(&session, &identity, request, sizeof(request), reply) == 60);
		assert(session.key_length == width);
		request[23] = 2;
		assert(pon_oam_receive(&session, &identity, request, sizeof(request), reply) == -EPROTO);
		assert(!unlink(path));
	}
	snprintf(path, sizeof(path), "%s/serdes", temporary);
	assert(!unlink(path) && !rmdir(temporary));
	puts("EPON key exchange: three modes, repeat/rotation, LLID binding, session reset and refused-write suppression pass");
	return 0;
}
