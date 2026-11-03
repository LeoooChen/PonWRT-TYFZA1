/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_SYNC_PEER_H
#define TYFZA1_FTTR_SYNC_PEER_H

#include <stdbool.h>
#include "sync-wire.h"

#define FTTR_SYNC_PEERS 16
#define FTTR_SYNC_HEARTBEAT_MS 2000

extern const uint8_t fttr_sync_master_group[6];
extern const uint8_t fttr_sync_slave_group[6];

struct fttr_sync_peer {
	uint8_t mac[6], missed;
	bool online;
};

int fttr_sync_heartbeat(const uint8_t source[6], uint16_t ethertype,
			uint8_t sequence, uint8_t frame[16]);
bool fttr_sync_destination(const uint8_t destination[6], const uint8_t local[6]);
int fttr_sync_peer_receive(struct fttr_sync_peer peers[FTTR_SYNC_PEERS],
		const uint8_t local[6], uint16_t ethertype, const uint8_t *frame, size_t length);
/* Called immediately after each original two-second heartbeat send. */
void fttr_sync_peer_age(struct fttr_sync_peer peers[FTTR_SYNC_PEERS]);

#endif
