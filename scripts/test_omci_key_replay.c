/* SPDX-License-Identifier: GPL-2.0-only */
/* Exercise the daemon's backend-before-response order with real OMCI frames. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pon-omci.h"

static unsigned int key_writes;

static int handle(struct omci_agent *agent, const uint8_t *frame, unsigned int length,
	uint8_t *reply, int backend_result)
{
	uint8_t row[18];
	if (tyfza1_omci_broadcast(agent, frame, length, row)) {
		assert(row[0] == 0 && row[1] == 0x40);
		key_writes++;
	}
	return tyfza1_omci_dispatch(agent, frame, length, reply, backend_result);
}

int main(void)
{
	struct omci_identity identity = {.serial = {'T','E','S','T',1,2,3,4}};
	for (unsigned int extended = 0; extended < 2; extended++) {
		struct omci_agent *agent = tyfza1_omci_new(&identity);
		uint8_t frame[44] = {0, 1, 0x48, extended ? 11 : 10, 1, 0x4c};
		uint8_t first[2048] = {}, reply[2048] = {};
		unsigned int mask = extended ? 10 : 8, length = extended ? 30 : 44;
		if (extended) frame[9] = 20;
		else frame[43] = 0x28;
		frame[mask + 1] = 0x20; frame[mask + 3] = 0x40;
		memset(frame + mask + 4, 0x55, 16);
		key_writes = 0;
		int bytes = handle(agent, frame, length, first, 0);
		assert(bytes > 0 && first[mask] == 0 && key_writes == 1);
		assert(handle(agent, frame, length, reply, 0) == bytes);
		assert(!memcmp(first, reply, bytes));
		assert(key_writes == 1);
		/* A new transaction, a failed backend, and a new session are distinct. */
		frame[1] = 2;
		assert(handle(agent, frame, length, reply, -5) > 0 && reply[mask] == 1);
		assert(key_writes == 2);
		assert(handle(agent, frame, length, reply, 0) > 0 && reply[mask] == 0);
		assert(key_writes == 3);
		assert(handle(agent, frame, length, reply, 0) > 0 && key_writes == 3);
		tyfza1_omci_epoch(agent);
		assert(handle(agent, frame, length, reply, 0) > 0 && key_writes == 4);
		tyfza1_omci_free(agent);
	}
	puts("Baseline/extended key SET: retransmitted replies do not rewrite hardware; failed writes and new sessions remain executable");
}
