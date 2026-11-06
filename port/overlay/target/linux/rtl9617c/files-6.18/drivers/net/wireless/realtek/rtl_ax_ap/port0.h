/* SPDX-License-Identifier: GPL-2.0-only */
/* Original mac_port_init@2281b8 and mac_port_cfg@22543c, SCC band0/port0.
 * Initial AP profile: 100 TU, DTIM1, MBID7 as selected by the original
 * rtw_hal_mac_port_init@1aa104. Beacon templates and AP enable follow.
 */
#ifndef RTL_AX_AP_PORT0_H
#define RTL_AX_AP_PORT0_H

#include "power.h"

static const unsigned int rtl_ax_port0_registers[] = {
	0xc400, 0xc404, 0xc408, 0xc40c, 0xc410, 0xc414, 0xc424,
	0xc540, 0xc544, 0xc590, 0xc594, 0xc6a0, 0xca08,
};

struct rtl_ax_port0 {
	unsigned int before[13], timer_before, timer_after, timer_reads;
	unsigned int interval, dtim;
	int result;
	bool ready;
};

static inline void rtl_ax_port0_field(const struct rtl_ax_io *io,
	unsigned int reg, unsigned int mask, unsigned int value)
{
	unsigned int old = io->read32(io->ctx, reg);
	unsigned int next = (old & ~mask) | value;

	/* The original only writes a changed field. */
	if (next != old)
		io->write32(io->ctx, reg, next);
}

static inline void rtl_ax_port0_color(const struct rtl_ax_io *io, unsigned int color)
{
	/* Original set_role_bss_clr@22ec08: SCC band0/port0 uses bits5:0. */
	rtl_ax_port0_field(io, 0xc6a0, 0x3f, color);
}

/* Original mac_get_tsf@240928: port0 low/high at C438/C43C. */
static inline u64 rtl_ax_port0_tsf(const struct rtl_ax_io *io)
{
	u32 high, low, next;

	high = io->read32(io->ctx, 0xc43c);
	do {
		next = high;
		low = io->read32(io->ctx, 0xc438);
		high = io->read32(io->ctx, 0xc43c);
	} while (high != next);
	return (u64)high << 32 | low;
}

static inline int rtl_ax_tsf_sync(const struct rtl_ax_io *io,
	unsigned int source, unsigned int target, int offset)
{
	u32 reg = 0xc2a0 + target * 4, last;
	u32 value = (io->read32(io->ctx, reg) & ~0x7707ffffU) |
		source << 24 | (offset < 0 ? BIT(18) | (u32)-offset : (u32)offset);

	/* Original mac_tsf_sync@228ef0, signed 32us units. NOW is self-clearing;
	 * the vendor mport.c offload path polls it before the next operation. */
	io->write32(io->ctx, reg, value);
	io->write32(io->ctx, reg, value | BIT(30));
	return io->poll32(io->ctx, reg, BIT(30), 0, 1, 1000, &last);
}

static inline int rtl_ax_port0_offset_tsf(const struct rtl_ax_io *io, int offset)
{
	u32 config = io->read32(io->ctx, 0xc440);
	u32 sync0 = io->read32(io->ctx, 0xc2a0);
	u32 sync1 = io->read32(io->ctx, 0xc2a4);
	int ret;

	/* One AP/mesh interface owns port0. Temporarily run unused port1's
	 * clock with beacon/RX synchronization disabled. Copy 0->1, then
	 * 1->0 plus the drift; both clocks keep ticking during MMIO latency. */
	io->write32(io->ctx, 0xc440, (config & ~(BIT(12) | BIT(9) | BIT(5) | BIT(3))) | BIT(2));
	ret = rtl_ax_tsf_sync(io, 0, 1, 0);
	if (!ret)
		ret = rtl_ax_tsf_sync(io, 1, 0, offset);
	io->write32(io->ctx, 0xc2a0, sync0);
	io->write32(io->ctx, 0xc2a4, sync1);
	io->write32(io->ctx, 0xc440, config);
	return ret;
}

static inline int rtl_ax_port0_start(const struct rtl_ax_io *io,
	struct rtl_ax_port0 *port, void *ctx, int (*drop_mbid)(void *, unsigned int),
	unsigned int interval, unsigned int dtim)
{
	unsigned int i, next, delta, subspace = min(interval / 8, 255U);
	unsigned int early = 160, hold = 400;
	unsigned char mode;
	int ret;

	port->interval = interval;
	port->dtim = dtim;
	if (subspace * 32 < early + hold) {
		early = subspace * 96 / 10;
		hold = subspace * 160 / 10;
	}
	for (i = 0; i < 13; i++)
		port->before[i] = io->read32(io->ctx, rtl_ax_port0_registers[i]);
	/* TX/RX report disable -> AP network type -> beacon protection ->
	 * RX update/sync disable -> beacon TX enable. Masks from original tables. */
	rtl_ax_port0_field(io, 0xc400, 2, 0);
	rtl_ax_port0_field(io, 0xc400, 1, 0);
	rtl_ax_port0_field(io, 0xc400, 0xc00, 0xc00);
	rtl_ax_port0_field(io, 0xc400, 0x12000, 0x12000);
	rtl_ax_port0_field(io, 0xc400, 0x10, 0);
	rtl_ax_port0_field(io, 0xc400, 8, 0);
	rtl_ax_port0_field(io, 0xc400, 0x1000, 0);
	rtl_ax_port0_field(io, 0xc414, 0xffff, interval);
	rtl_ax_port0_field(io, 0xc6a0, 0x3f, 0);
	rtl_ax_update16(io, 0xc412, 0xff00, 1U << 8);
	for (i = 0; i <= 7; i++) {
		/* Actual AP role allocation sets hiq_win50@13af24..13af28;
		 * port_init copies it into each port0 MBID's transmission window. */
		io->write8(io->ctx, 0xc590 + i, 50);
		mode = io->read8(io->ctx, 0xca08);
		if ((mode | 3) != mode)
			io->write8(io->ctx, 0xca08, mode | 3);
		io->write8(io->ctx, i ? 0xc540 + i : 0xc427, dtim);
		/* Original hiq_drop_ctrl bypasses its MMIO workaround on C/XB.
		 * Only MBID1..7 require MAC/general packet-drop H2C. */
		if (i) {
			ret = drop_mbid(ctx, i);
			if (ret)
				return ret;
		}
	}
	/* Original subspace for 100 TU / eight slots: 12 TU.
	 * mac_port_init derives early=12*96/10=115 and hold=12*160/10=192. */
	rtl_ax_port0_field(io, 0xc414, 0xff0000, subspace << 16);
	rtl_ax_port0_field(io, 0xc404, 0xfff0000, hold << 16);
	rtl_ax_port0_field(io, 0xc408, 0xfff0000, 0);
	rtl_ax_port0_field(io, 0xc400, 4, 4);
	/* dly_port_us: enabled port counter must advance at least10 us.
	 * Original bound2000 observations; first delay10us, then10us per read. */
	port->timer_before = io->read32(io->ctx, 0xc434);
	io->delay_us(io->ctx, 10);
	for (i = 0; i < 2000; i++) {
		next = io->read32(io->ctx, 0xc434);
		port->timer_after = next;
		port->timer_reads = i + 1;
		delta = next >= port->timer_before ? next - port->timer_before :
			port->timer_before + 1 - next;
		if (delta >= 10)
			break;
		if (next == port->timer_before)
			return -EIO;
		io->delay_us(io->ctx, 10);
	}
	if (i == 2000)
		return -ETIMEDOUT;
	rtl_ax_port0_field(io, 0xc40c, 0xfff, early);
	rtl_ax_port0_field(io, 0xc404, 0xff, 4);
	rtl_ax_update16(io, 0xc40e, 0xfff, 5);
	port->ready = true;
	return 0;
}

#endif
