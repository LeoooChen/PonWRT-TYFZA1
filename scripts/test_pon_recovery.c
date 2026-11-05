/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Exercise the driver's actual backoff and persistent refill implementations. */
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-mpcp-backoff.h"
#include "rtl9617c-qm-refill.h"

struct registers { unsigned int packet_flag, start, reads, writes; };
static unsigned int reg_read(void *context, unsigned int reg)
{
	struct registers *r = context;
	r->reads++;
	assert(reg == 0x20d8 || reg == 0x200c);
	return reg == 0x20d8 ? r->packet_flag : r->start;
}
static void reg_write(void *context, unsigned int reg, unsigned int value)
{
	struct registers *r = context;
	assert(reg == 0x200c);
	r->start = value; r->writes++;
}

struct hardware {
	unsigned int bid_ready[2], fifo_ready;
	unsigned int owned[512], returned;
};
static int push(void *context, unsigned int address, unsigned int eq)
{
	struct hardware *hw = context;
	unsigned int buffer = (address - 0x10000000U) / 2048;
	assert(buffer < 512 && eq == 4 + buffer / 256);
	if (!hw->bid_ready[eq - 4] || !hw->fifo_ready)
		return -1;
	assert(hw->owned[buffer] == 0); /* A duplicate return fails here. */
	hw->owned[buffer] = 1;
	hw->returned++;
	return 0;
}

int main(void)
{
	struct registers r = {.packet_flag = 4U << 16, .start = 0xa5};
	struct rtl_mpcp_io io = {&r, reg_read, reg_write};
	struct rtl_mpcp_backoff backoff = {};
	struct rtl_qm_refill queue = {};
	struct hardware hw = {};

	assert(!rtl_mpcp_backoff_event(&backoff, &io, 0, 0, 60000));
	assert(!r.reads && !r.writes);
	assert(rtl_mpcp_backoff_event(&backoff, &io, 2, 100, 60000));
	assert(r.start == 0xa4 && r.writes == 1 && backoff.nacks == 1);
	assert(rtl_mpcp_backoff_active(&backoff, 60099));
	for (unsigned int now = 1000; now < 60000; now += 1000)
		assert(!rtl_mpcp_backoff_event(&backoff, &io, 0, now, 60000));
	assert(backoff.until == 60100 && backoff.nacks == 1);
	assert(!rtl_mpcp_backoff_active(&backoff, 60100));
	/* Expiry permits the worker to reassert START, including clock wrap. */
	r.start |= 1;
	assert(rtl_mpcp_backoff_event(&backoff, &io, 2, ULONG_MAX - 100, 60000));
	assert(rtl_mpcp_backoff_active(&backoff, 59898));
	assert(!rtl_mpcp_backoff_active(&backoff, 59899));
	assert(rtl_mpcp_backoff_event(&backoff, &io, 2, 100, 60000));
	r.packet_flag = 3U << 16;
	assert(!rtl_mpcp_backoff_event(&backoff, &io, 2, 101, 60000));
	assert(!rtl_mpcp_backoff_active(&backoff, 101));
	puts("MPCP actual 0x20d8 NACK read, 0x200c stop, sticky event gating, 60s expiry and wrap passed");

	/* Simulate all 512 EPP entries retired with both BIDs/FIFO unavailable. */
	for (unsigned int i = 0; i < 512; i++) {
		rtl_qm_refill_add(&queue, (0x10000000U + i * 2048) | (4 + i / 256));
		rtl_qm_refill_drain(&queue, &hw, push, 64);
	}
	assert(queue.pending == 512 && queue.peak == 512 && !hw.returned);
	hw.fifo_ready = 1;
	hw.bid_ready[1] = 1;
	for (unsigned int tick = 0; tick < 8; tick++)
		rtl_qm_refill_drain(&queue, &hw, push, 64);
	assert(queue.pending == 256 && hw.returned == 256); /* EQ5 not starved. */
	hw.bid_ready[0] = 1;
	hw.fifo_ready = 0;
	for (unsigned int tick = 0; tick < 100; tick++)
		rtl_qm_refill_drain(&queue, &hw, push, 64);
	assert(queue.pending == 256 && hw.returned == 256);
	hw.fifo_ready = 1;
	for (unsigned int tick = 0; tick < 8; tick++)
		rtl_qm_refill_drain(&queue, &hw, push, 64);
	assert(!queue.pending && queue.returned == 512 && hw.returned == 512);
	for (unsigned int i = 0; i < 512; i++) assert(hw.owned[i] == 1);
	/* Queue cursors wrap over multiple rounds without duplicates or loss. */
	for (unsigned int round = 0; round < 5; round++) {
		for (unsigned int i = 0; i < 512; i++) {
			hw.owned[i] = 0;
			rtl_qm_refill_add(&queue, (0x10000000U + i * 2048) | (4 + i / 256));
		}
		while (queue.pending) rtl_qm_refill_drain(&queue, &hw, push, 64);
	}
	assert(hw.returned == 3072);
	/* Stop retains ownership; TQM reset discards old pending BIDs before reload. */
	hw.owned[0] = 0;
	rtl_qm_refill_add(&queue, 0x10000004);
	assert(queue.pending == 1);
	memset(&queue, 0, sizeof(queue));
	hw.owned[0] = 1;
	rtl_qm_refill_drain(&queue, &hw, push, 64);
	assert(hw.returned == 3072);
	puts("QM 512-buffer stall/recovery, independent EQs, FIFO stall, cursor wrap and reset ownership passed");
	/* A single active EQ must not strand half the available refill budget. */
	for (unsigned int eq = 0; eq < 2; eq++) {
		memset(&queue, 0, sizeof(queue));
		memset(&hw, 0, sizeof(hw));
		hw.fifo_ready = hw.bid_ready[eq] = 1;
		for (unsigned int i = 0; i < 64; i++)
			rtl_qm_refill_add(&queue, (0x10000000U + (eq * 256 + i) * 2048) | (4 + eq));
		rtl_qm_refill_drain(&queue, &hw, push, 64);
		assert(!queue.pending && hw.returned == 64);
	}
	/* Both active queues retain a fair share, including an odd budget. */
	memset(&queue, 0, sizeof(queue));
	memset(&hw, 0, sizeof(hw));
	hw.fifo_ready = hw.bid_ready[0] = hw.bid_ready[1] = 1;
	for (unsigned int i = 0; i < 512; i++)
		rtl_qm_refill_add(&queue, (0x10000000U + i * 2048) | (4 + i / 256));
	rtl_qm_refill_drain(&queue, &hw, push, 65);
	assert(hw.returned == 65 && queue.count[0] == 223 && queue.count[1] == 224);
	puts("QM single-EQ full budget and dual-EQ fair budget passed");
	return 0;
}
