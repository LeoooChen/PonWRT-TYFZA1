/* SPDX-License-Identifier: GPL-2.0-only */
#include <linux/rtl9617c-key-pn.h>

/* Return1 only after assigning a unique PN,0 to yield,2 to discard a
 * revoked request. CPU and direct NI GTK use the same final publisher. */
static u32 pe_key_pn(u8 *memory, u32 *request)
{
	u32 generation = request[RTL_KEY_PN_REQUEST];
	if (!generation) return 1;
	u32 cam = (request[5] >> 16) & 127;
	volatile struct rtl_key_pn_shared *s = (void *)(memory + RTL_KEY_PN_BASE + cam * sizeof(*s));
	if (s->generation != generation || !(request[4] & BIT(15)) || (request[4] & BIT(16))) return 2;
	s->pe = 1; sync(); s->turn = 0; sync();
	if (s->arm && !s->turn) { s->pe = 0; sync(); return 0; }
	sync();
	if (s->generation != generation) { s->pe = 0; sync(); return 2; }
	u32 published = s->published, bank = published & 1;
	u32 low = s->low[bank] + 1, high = (s->high[bank] + !low) & 65535;
	published++; bank = published & 1;
	s->low[bank] = low; s->high[bank] = high;
	sync(); s->published = published;
	request[6] = low; request[7] = high;
	sync(); s->pe = 0; sync();
	return 1;
}
