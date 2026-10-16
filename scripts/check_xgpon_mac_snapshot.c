/* SPDX-License-Identifier: GPL-2.0-only */
/* One reset fixture checks the MAC snapshot, deferred VOQs and PDC policy. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-xgpon-reset.h"

static unsigned int regs[0xc000 / 4], pvt[32][5], cir[256][2], pir[256][2];
static unsigned int bp[256], upstream[256], pdc[256][2];
static unsigned int tcont[32];
static unsigned int random_writes[3], random_count;
static int access_error;

static unsigned int read_reg(void *context, unsigned int address)
{
	(void)context;
	return regs[address / 4];
}

static void write_reg(void *context, unsigned int address, unsigned int value)
{
	(void)context;
	regs[address / 4] = value;
	if (address == 0x4a18) {
		assert(random_count < 3);
		random_writes[random_count++] = value;
	}
}

static int access_reg(void *context, unsigned int address, unsigned int command)
{
	unsigned int *table, data, count, index = command & 255;
	(void)context;
	if (access_error)
		return access_error;
	switch (address) {
	case 0x8000: table = pvt[index]; data = 0x8014; count = 5; break;
	case 0x81c4: table = cir[index]; data = 0x81cc; count = 2; break;
	case 0x81ec: table = pir[index]; data = 0x81f4; count = 2; break;
	case 0x80e8: table = &bp[index]; data = 0x80ec; count = 1; break;
	case 0x4618: table = &upstream[index]; data = 0x461c; count = 1; break;
	case 0x4838: table = &tcont[index]; data = 0x483c; count = 1; break;
	case 0x9020: table = pdc[index]; data = 0x9028; count = 2; break;
	case 0x8060: return 0;
	default: assert(0); return -1;
	}
	for (unsigned int i = 0; i < count; i++) {
		if (command & 0x40000000U)
			table[i] = regs[(data - i * 4) / 4];
		else
			regs[(data - i * 4) / 4] = table[i];
	}
	return 0;
}

int main(void)
{
	const struct rtl_dq_io io = {0, read_reg, write_reg, access_reg};
	struct rtl_xgpon_mac_saved saved;

	upstream[0] = 0x10011;
	upstream[8] = 0x31234;
	upstream[255] = 0x5ffff;
	regs[0x412c / 4] = 0x20011;
	regs[(0x412c + 8 * 4) / 4] = 0x21234;
	regs[(0x412c + 255 * 4) / 4] = 0x3ffff;
	regs[0x81bc / 4] = 0xffffff;
	regs[(0x81bc - 7 * 4) / 4] = 0xff000000;
	for (unsigned int i = 0; i < 256; i++) {
		pdc[i][0] = (24 << 3) | (7 << 9);
		pdc[i][1] = i << 4;
	}
	assert(!rtl_xgpon_mac_save(&io, &saved));
	memset(regs, 0, sizeof(regs));
	memset(pvt, 0, sizeof(pvt));
	memset(cir, 0, sizeof(cir));
	memset(pir, 0, sizeof(pir));
	memset(bp, 0, sizeof(bp));
	memset(upstream, 0, sizeof(upstream));
	memset(pdc, 0, sizeof(pdc));
	assert(!rtl_xgpon_mac_restore(&io, &saved));
	assert(upstream[0] == 0x10011 && upstream[8] == 0x31234 && upstream[255] == 0x5ffff);
	assert(regs[0x412c / 4] == 0x20011 && regs[(0x412c + 8 * 4) / 4] == 0x21234);
	assert(regs[(0x412c + 255 * 4) / 4] == 0x3ffff);
	assert(regs[0x81bc / 4] == 255 && regs[(0x81bc - 7 * 4) / 4] == 0);
	assert(saved.puc.valid[0] == 0xffffff && saved.puc.valid[7] == 0xff000000);
	for (unsigned int i = 0; i < 256; i++) {
		assert(pdc[i][0] == ((24 << 3) | (7 << 9)) && pdc[i][1] == i << 4);
	}
	regs[0x4a18 / 4] = 0x60000002;
	rtl_xgpon_random_load(&io, ~0U);
	assert(random_count == 3 && random_writes[0] == 0x7fffffff);
	assert(random_writes[1] == 0xffffffff && random_writes[2] == 0x7fffffff);
	access_error = -110;
	assert(rtl_xgpon_mac_restore(&io, &saved) == -110);
	assert(saved.puc.valid[0] == 0xffffff && saved.puc.valid[7] == 0xff000000);
	assert(rtl_xgpon_mac_save(&io, &saved) == -110);
	puts("MAC snapshot: OMCC/unicast/multicast GEMs and native PDC policy restored; business VOQs deferred; random-load pulse and real access errors retained");
}
