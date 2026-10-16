/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_XGTC_RECOVERY_H
#define RTL9617C_XGTC_RECOVERY_H
#include "rtl9617c-pon-itu-puc.h"

/* TYFZA1 aal_xgpon_backup_puc/restore_puc and the actual AAL setters. */
struct rtl_xgtc_puc_saved {
	unsigned int ni_enable, mac, burst, laser, to1, to2;
	unsigned int control[5], limit_select[16], limit[3], valid[8];
	unsigned int pvt[32][5], cir[256][2], pir[256][2], backpressure[192];
};

static const unsigned int rtl_xgtc_control_regs[5] = {0x813c, 0x8140, 0x8144, 0x8050, 0x808c};
static const unsigned int rtl_xgtc_control_masks[5] = {0x7fe07f7f, 0x7fff, 0x0780001f, 0xfb11193f, 0xce070003};

static inline void rtl_xgtc_restart_sync(const struct rtl_dq_io *io)
{
	unsigned int status = io->read(io->context, 0x4d30), state = status & 7;
	/* Original clock-sequence reset applies only in O1/O8 sub-state1. */
	if ((state == 0 || state == 6) && (status & (1U << 4)))
		io->write(io->context, 0x4d30, (status & ~(3U << 16)) | (1U << 16));
}

static inline int rtl_xgtc_table_read(const struct rtl_dq_io *io, unsigned int reg,
	unsigned int data0, unsigned int index, unsigned int *words, unsigned int count)
{
	int ret = io->indirect(io->context, reg, 0x80000000U | index);
	if (ret) return ret;
	for (unsigned int i = 0; i < count; i++) words[i] = io->read(io->context, data0 - i * 4);
	return 0;
}

static inline int rtl_xgtc_table_restore(const struct rtl_dq_io *io, unsigned int reg,
	unsigned int data0, unsigned int index, const unsigned int *saved,
	const unsigned int *mask, unsigned int count)
{
	unsigned int loaded[5];
	int ret = rtl_xgtc_table_read(io, reg, data0, index, loaded, count);
	if (ret) return ret;
	for (unsigned int i = 0; i < count; i++)
		io->write(io->context, data0 - i * 4, (loaded[i] & ~mask[i]) | (saved[i] & mask[i]));
	return io->indirect(io->context, reg, 0xc0000000U | index);
}

static inline int rtl_xgtc_puc_save(const struct rtl_dq_io *io, struct rtl_xgtc_puc_saved *saved)
{
	int ret;
	for (unsigned int i = 0; i < 5; i++) saved->control[i] = io->read(io->context, rtl_xgtc_control_regs[i]);
	for (unsigned int i = 0; i < 16; i++) saved->limit_select[i] = io->read(io->context, 0x8090 + i * 4);
	for (unsigned int i = 0; i < 3; i++) saved->limit[i] = io->read(io->context, 0x80d0 + i * 4);
	for (unsigned int i = 0; i < 8; i++) saved->valid[i] = io->read(io->context, 0x81bc - i * 4);
	for (unsigned int i = 0; i < 32; i++) {
		ret = rtl_xgtc_table_read(io, 0x8000, 0x8014, i, saved->pvt[i], 5);
		if (ret) return ret;
	}
	for (unsigned int i = 0; i < 256; i++) {
		ret = rtl_xgtc_table_read(io, 0x81c4, 0x81cc, i, saved->cir[i], 2);
		if (!ret) ret = rtl_xgtc_table_read(io, 0x81ec, 0x81f4, i, saved->pir[i], 2);
		if (!ret && i < 192) ret = rtl_xgtc_table_read(io, 0x80e8, 0x80ec, i, &saved->backpressure[i], 1);
		if (ret) return ret;
	}
	return 0;
}

static inline int rtl_xgtc_puc_restore(const struct rtl_dq_io *io, const struct rtl_xgtc_puc_saved *saved)
{
	static const unsigned int pvt_mask[5] = {~0U, ~0U, 0xffff10ff, ~0U, 0xffff};
	static const unsigned int bucket_mask[2] = {~0U, 0x7fffffff}, bp_mask = 255;
	unsigned int shift = 7 + (io->read(io->context, 0x8048) & 3);
	int ret;

	for (unsigned int i = 0; i < 32; i++) {
		unsigned int credit[5] = {0};
		ret = rtl_xgtc_table_restore(io, 0x8000, 0x8014, i, saved->pvt[i], pvt_mask, 5);
		if (ret) return ret;
		/* Seed fresh scheduler credits from weights, as original restore_puc. */
		for (unsigned int q = 0; q < 8; q++) {
			unsigned int weight = q < 2 ? saved->pvt[i][2] >> (16 + q * 8) :
				q < 6 ? saved->pvt[i][3] >> ((q - 2) * 8) : saved->pvt[i][4] >> ((q - 6) * 8);
			unsigned int bit = q * 20;
			unsigned long long value = (weight & 255U) << shift;
			credit[bit / 32] |= value << (bit % 32);
			if (bit % 32 > 12) credit[bit / 32 + 1] |= value >> (32 - bit % 32);
		}
		for (unsigned int q = 0; q < 5; q++) io->write(io->context, 0x8074 - q * 4, credit[q]);
		ret = io->indirect(io->context, 0x8060, 0xc0000000U | i);
		if (ret) return ret;
	}
	for (unsigned int i = 0; i < 4; i++) {
		unsigned int reg = rtl_xgtc_control_regs[i], mask = rtl_xgtc_control_masks[i];
		io->write(io->context, reg, (io->read(io->context, reg) & ~mask) | (saved->control[i] & mask));
	}
	for (unsigned int i = 0; i < 256; i++) {
		ret = rtl_xgtc_table_restore(io, 0x81c4, 0x81cc, i, saved->cir[i], bucket_mask, 2);
		if (!ret) ret = rtl_xgtc_table_restore(io, 0x81ec, 0x81f4, i, saved->pir[i], bucket_mask, 2);
		if (!ret && i < 192) ret = rtl_xgtc_table_restore(io, 0x80e8, 0x80ec, i, &saved->backpressure[i], &bp_mask, 1);
		if (ret) return ret;
	}
	for (unsigned int i = 0; i < 16; i++) io->write(io->context, 0x8090 + i * 4, saved->limit_select[i]);
	for (unsigned int i = 0; i < 3; i++)
		io->write(io->context, 0x80d0 + i * 4, (io->read(io->context, 0x80d0 + i * 4) & ~0x3fffU) | (saved->limit[i] & 0x3fffU));
	io->write(io->context, 0x808c, (io->read(io->context, 0x808c) & ~rtl_xgtc_control_masks[4]) | (saved->control[4] & rtl_xgtc_control_masks[4]));
	for (unsigned int i = 0; i < 8; i++) io->write(io->context, 0x81bc - i * 4, saved->valid[i]);
	return 0;
}
#endif
