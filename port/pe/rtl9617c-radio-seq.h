/* SPDX-License-Identifier: GPL-2.0-only */
#include <linux/rtl9617c-seq.h>

/* A queued NI request owns its interface generation. Never spin inside PE:
 * a busy ARM allocator yields, and removal rejects delayed requests. */
static u32 pe_sequence_next(u8 *memory, u32 generation, u32 *sequence)
{
	volatile struct rtl_seq_shared *s = (void *)(memory + RTL_SEQ_BASE);
	if (!generation || s->generation != generation) return 2;
	s->pe = 1; sync(); s->turn = 0; sync();
	if (s->arm && !s->turn) { s->pe = 0; sync(); return 0; }
	sync();
	if (s->generation != generation) { s->pe = 0; sync(); return 2; }
	*sequence = s->next;
	s->next = (*sequence + 1) & 4095;
	sync(); s->pe = 0; sync();
	return 1;
}
