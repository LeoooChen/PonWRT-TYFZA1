/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Execute native DQ/PUC sequences against ownership and indirect-table models. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-qm-deepq.h"
#include "rtl9617c-pon-queue.h"

struct model {
	unsigned int registers[0x10000 / 4];
	unsigned int credits[32], voq_profile[256], bp[256];
	unsigned int calls, fail_at;
};

static unsigned int read_reg(void *context, unsigned int reg)
{
	struct model *m = context;
	assert(reg < 0x10000 && !(reg & 3));
	return m->registers[reg / 4];
}

static void write_reg(void *context, unsigned int reg, unsigned int value)
{
	struct model *m = context;
	assert(reg < 0x10000 && !(reg & 3));
	m->registers[reg / 4] = value;
}

static int indirect(void *context, unsigned int reg, unsigned int value)
{
	struct model *m = context;
	unsigned int index = value & 255;
	assert(value & 0x80000000U);
	if (++m->calls == m->fail_at)
		return -1;
	if (reg == 0x95a8) {
		assert(index >= 16 && index < 32);
		m->credits[index] = read_reg(m, 0x95ac);
	} else if (reg == 0x95c0) {
		assert(index >= 128);
		m->voq_profile[index] = read_reg(m, 0x95c4);
	} else if (reg == 0x80e8) {
		if (value & 0x40000000U)
			m->bp[index] = read_reg(m, 0x80ec);
		else
			write_reg(m, 0x80ec, m->bp[index]);
	} else {
		assert(reg == 0x95b0 && index == 3);
	}
	return 0;
}

int main(void)
{
	struct model m = {};
	struct rtl_dq_io io = {&m, read_reg, write_reg, indirect};
	unsigned int cpu_pool[10], cpu_before[10];

	/* CPU EQ4/5 must retain their original BID regions and coherent attributes. */
	for (unsigned int i = 0; i < 10; i++)
		cpu_pool[i] = cpu_before[i] = m.registers[(0x61e8 + 4 * 20) / 4 + i] = 0x12340000 + i;
	write_reg(&m, 0x68f8, 0xa5a50003);
	write_reg(&m, 0x68b0, ~0U);
	write_reg(&m, 0x610c, ~0U);
	assert(rtl_qm_deepq_prepare(&io, 0x12000000) == 0);
	assert(read_reg(&m, 0x68f8) == 0xa5a5000a && !(read_reg(&m, 0x68b0) & 0xc8000000U));
	assert(read_reg(&m, 0x610c) == 0xffff0000U); /* no premature dequeue */
	assert(read_reg(&m, 0x612c) == 0x21);
	unsigned int bid_used[1536] = {};
	for (unsigned int i = 0; i < 512; i++) bid_used[i] = 1;
	for (unsigned int pool = 0; pool < 2; pool++) {
		unsigned int cfg = 0x61e8 + (pool + 1) * 20;
		unsigned int address = read_reg(&m, cfg) & ~127U;
		unsigned int count = (read_reg(&m, cfg + 4) >> 16) & 0x3fff;
		unsigned int first = read_reg(&m, cfg + 4) & 0x3fff;
		assert(address == 0x12000000 + pool * 1048576);
		assert(count == 512 && first == 512 + pool * 512);
		assert(read_reg(&m, cfg + 8) == 5 && read_reg(&m, cfg + 12) == 0x61008060);
		assert(read_reg(&m, cfg + 16) == 2);
		for (unsigned int i = first; i < first + count; i++) assert(!bid_used[i]++);
	}
	for (unsigned int i = 0; i < 10; i++)
		assert(m.registers[(0x61e8 + 4 * 20) / 4 + i] == cpu_before[i] && cpu_pool[i] == cpu_before[i]);
	for (unsigned int port = 16; port < 32; port++) {
		assert(read_reg(&m, 0x6148 + port * 4) == 1);
		assert(read_reg(&m, 0x63c4 + port * 4) == 255);
		assert(m.credits[port] == 0x42004200);
		for (unsigned int q = port * 8; q < port * 8 + 8; q++) assert(m.voq_profile[q] == 3);
	}
	assert(m.calls == 145);
	/* Every table failure is observable; dequeue stays disabled throughout. */
	for (unsigned int fail = 1; fail <= 145; fail++) {
		memset(&m, 0, sizeof(m)); m.fail_at = fail;
		write_reg(&m, 0x610c, ~0U);
		assert(rtl_qm_deepq_prepare(&io, 0x12000000) == -1);
		assert(!(read_reg(&m, 0x610c) & 0xffff));
	}
	memset(&m, 0, sizeof(m));
	for (unsigned int i = 0; i < 256; i++) m.bp[i] = 0xa55a0000U | i;
	assert(rtl_pon_queue_backpressure(&io) == 0 && m.calls == 512);
	for (unsigned int i = 0; i < 256; i++)
		assert(m.bp[i] == (0xa55a0000U | (i < 128 ? i + 64 : 255)));
	for (unsigned int fail = 1; fail <= 512; fail++) {
		memset(&m, 0, sizeof(m));m.fail_at=fail;
		assert(rtl_pon_queue_backpressure(&io) == -1);
	}
	puts("Native PON DQ: disjoint CPU/WAN/shared ownership, 16 ports/128 VOQs, credits and all 256 PUC BP mappings passed");
	return 0;
}
