// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <string.h>

#include "sync-peer.h"

const uint8_t fttr_sync_master_group[6] = { 1, 0x10, 0xec, 0, 0, 1 };
const uint8_t fttr_sync_slave_group[6] = { 1, 0x10, 0xec, 0, 0, 2 };

bool fttr_sync_destination(const uint8_t destination[6], const uint8_t local[6])
{
	return !memcmp(destination, local, 6) || !memcmp(destination, fttr_sync_master_group, 6) ||
		!memcmp(destination, fttr_sync_slave_group, 6);
}

int fttr_sync_heartbeat(const uint8_t source[6], uint16_t ethertype,
			uint8_t sequence, uint8_t frame[16])
{
	if (ethertype < 0x600)
		return -EINVAL;
	memcpy(frame, fttr_sync_slave_group, 6);
	memcpy(frame + 6, source, 6);
	frame[12] = ethertype >> 8;
	frame[13] = ethertype;
	frame[14] = 1;
	frame[15] = 0x80 | (sequence & 7);
	return 16;
}

int fttr_sync_peer_receive(struct fttr_sync_peer peers[FTTR_SYNC_PEERS],
		const uint8_t local[6], uint16_t ethertype, const uint8_t *frame, size_t length)
{
	static const uint8_t zero[6];
	int slot, available = -1;

	if (length < 16 || length > 60)
		return -EMSGSIZE;
	if (((uint16_t)frame[12] << 8 | frame[13]) != ethertype ||
	    !fttr_sync_destination(frame, local) || (frame[14] & 15) != 1 || (frame[15] & 0x80))
		return -EAGAIN;
	if ((frame[6] & 1) || !memcmp(frame + 6, zero, 6) || !memcmp(frame + 6, local, 6))
		return -EINVAL;
	/* The original prefers a retained exact MAC, even in an offline slot,
	 * over the first free slot. Never evict an online different peer. */
	for (slot = 0; slot < FTTR_SYNC_PEERS; slot++) {
		if (!memcmp(peers[slot].mac, frame + 6, 6))
			break;
		if (available < 0 && !peers[slot].online)
			available = slot;
	}
	if (slot == FTTR_SYNC_PEERS)
		slot = available;
	if (slot < 0)
		return -ENOSPC;
	memcpy(peers[slot].mac, frame + 6, 6);
	peers[slot].online = true;
	peers[slot].missed = 0;
	return slot;
}

void fttr_sync_peer_age(struct fttr_sync_peer peers[FTTR_SYNC_PEERS])
{
	int slot;

	for (slot = 0; slot < FTTR_SYNC_PEERS; slot++)
		if (peers[slot].online && ++peers[slot].missed > 2)
			peers[slot].online = false;
}
