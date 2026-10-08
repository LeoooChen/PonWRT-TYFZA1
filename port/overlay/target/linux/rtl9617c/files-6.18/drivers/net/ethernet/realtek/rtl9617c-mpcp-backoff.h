/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_MPCP_BACKOFF_H
#define RTL9617C_MPCP_BACKOFF_H

#define RTL_MPCP_START 0x200c
#define RTL_MPCP_PACKET_FLAG 0x20d8

struct rtl_mpcp_backoff {
	unsigned long until;
	unsigned int active, nacks, register_flag;
};

struct rtl_mpcp_io {
	void *context;
	unsigned int (*read)(void *context, unsigned int reg);
	void (*write)(void *context, unsigned int reg, unsigned int value);
};

/* PKT_FLAG has no MAC index. Silence the shared registration engine; the
 * stock driver only establishes NACK ownership for LLID0. Event gating avoids
 * extending the deadline every time a sticky register_flag is polled. */
static inline int rtl_mpcp_backoff_event(struct rtl_mpcp_backoff *state,
	const struct rtl_mpcp_io *io, unsigned int irq, unsigned long now,
	unsigned long period)
{
	if (!(irq & (1U << 1)))
		return 0;
	state->register_flag = (io->read(io->context, RTL_MPCP_PACKET_FLAG) >> 16) & 255;
	if (state->register_flag == 4) {
		io->write(io->context, RTL_MPCP_START,
			  io->read(io->context, RTL_MPCP_START) & ~1U);
		state->active = 1;
		state->until = now + period;
		state->nacks++;
		return 1;
	}
	if (state->register_flag == 3)
		state->active = 0;
	return 0;
}

static inline int rtl_mpcp_backoff_active(struct rtl_mpcp_backoff *state, unsigned long now)
{
	if (state->active && (long)(now - state->until) >= 0)
		state->active = 0;
	return state->active;
}
#endif
