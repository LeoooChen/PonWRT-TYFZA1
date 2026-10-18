/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_CF_H
#define RTL9617C_FTTR_CF_H
#include "rtl9617c-qm-deepq.h"

/* Original ca-ne CF TCAM has 512 X/Y pairs and three-word actions. The
 * owner holds the FE table mutex across allocation, publication and restore. */
struct rtl_fttr_cf {
	unsigned int index, destination;
	unsigned int saved[9]; /* X, Y, action */
	int error, cleanup_error;
	bool owned, active;
};

static inline int rtl_fttr_cf_read(const struct rtl_dq_io *io, bool action,
				 unsigned int index, unsigned int words[3])
{
	unsigned int reg = action ? 0x1e14 : 0x1e04;
	unsigned int data = action ? 0x1e20 : 0x1e10;
	unsigned int i;
	int ret = io->indirect(io->context, reg, 0x80000000U | index);

	if (ret)
		return ret;
	for (i = 0; i < 3; i++)
		words[i] = io->read(io->context, data - i * 4);
	return 0;
}

static inline int rtl_fttr_cf_write(const struct rtl_dq_io *io, bool action,
				  unsigned int index, const unsigned int words[3])
{
	unsigned int reg = action ? 0x1e14 : 0x1e04;
	unsigned int data = action ? 0x1e20 : 0x1e10;
	unsigned int observed[3], i;
	int ret;

	for (i = 0; i < 3; i++)
		io->write(io->context, data - i * 4, words[i]);
	ret = io->indirect(io->context, reg, 0xc0000000U | index);
	if (!ret)
		ret = rtl_fttr_cf_read(io, action, index, observed);
	if (!ret)
		for (i = 0; i < 3; i++)
			if (words[i] != observed[i])
				return -EUCLEAN;
	return ret;
}

static inline int rtl_fttr_cf_claim(const struct rtl_dq_io *io, struct rtl_fttr_cf *cf)
{
	unsigned int index;
	int ret;

	for (index = 0; index < 512; index++) {
		ret = rtl_fttr_cf_read(io, false, index + 512, cf->saved + 3);
		if (ret)
			goto out;
		if (!(cf->saved[5] & (1U << 16)))
			break;
		ret = rtl_fttr_cf_read(io, false, index, cf->saved);
		if (ret)
			goto out;
		/* A higher-priority existing rule that can match source6 would
		 * hide this admission rule for part or all of the P6 traffic. */
		if ((((cf->saved[0] & ~(6U << 6)) |
		      (cf->saved[3] & (6U << 6))) & (63U << 6)) == (63U << 6)) {
			ret = -EBUSY;
			goto out;
		}
	}
	if (index == 512) {
		ret = -ENOSPC;
		goto out;
	}
	ret = rtl_fttr_cf_read(io, false, index, cf->saved);
	if (!ret)
		ret = rtl_fttr_cf_read(io, true, index, cf->saved + 6);
	if (!ret) {
		cf->index = index;
		cf->owned = true;
	}
out:
	cf->error = ret;
	return ret;
}

static inline int rtl_fttr_cf_destination(const struct rtl_dq_io *io,
					struct rtl_fttr_cf *cf, unsigned int destination)
{
	const unsigned int x[3] = { ~(6U << 6), ~0U, ~0U };
	const unsigned int y[3] = { (6U << 6) | ~(63U << 6), ~0U, ~0U };
	const unsigned int action[3] = { 0, (3U << 5) | (destination << 8), 0 };
	int ret;

	/* On first activation, prepare/verify the action before the valid key. */
	ret = rtl_fttr_cf_write(io, true, cf->index, action);
	if (!ret && !cf->active)
		ret = rtl_fttr_cf_write(io, false, cf->index, x);
	if (!ret && !cf->active)
		ret = rtl_fttr_cf_write(io, false, cf->index + 512, y);
	if (!ret) {
		cf->destination = destination;
		cf->active = true;
	}
	cf->error = ret;
	return ret;
}

static inline int rtl_fttr_cf_release(const struct rtl_dq_io *io, struct rtl_fttr_cf *cf)
{
	unsigned int invalid[3] = { (6U << 6) | ~(63U << 6), ~0U, ~(1U << 16) };
	int ret;

	if (!cf->owned)
		return 0;
	/* Retire the live match before restoring its previously unused slot. */
	ret = rtl_fttr_cf_write(io, false, cf->index + 512, invalid);
	if (!ret) {
		cf->active = false;
		ret = rtl_fttr_cf_write(io, true, cf->index, cf->saved + 6);
	}
	if (!ret)
		ret = rtl_fttr_cf_write(io, false, cf->index, cf->saved);
	if (!ret)
		ret = rtl_fttr_cf_write(io, false, cf->index + 512, cf->saved + 3);
	if (!ret)
		cf->owned = false;
	cf->cleanup_error = ret;
	return ret;
}
#endif
