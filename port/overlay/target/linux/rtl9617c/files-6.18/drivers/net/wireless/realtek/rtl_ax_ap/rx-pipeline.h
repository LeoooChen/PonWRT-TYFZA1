/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RX_PIPELINE_H
#define RTL_AX_AP_RX_PIPELINE_H
#include "transport.h"

struct rtl_ax_rx_pipeline {
	u16 cca[2], crc_ok[5], crc_error[5];
	u16 mac_crc_ok[8], mac_crc_error[8], mac_false_alarm[8];
	u32 rx_index[2], haxi, dmac, cmac;
	u32 cck_gate[3];
};

static inline void rtl_ax_rx_pipeline_read(const struct rtl_ax_io *io,
					 struct rtl_ax_rx_pipeline *snapshot)
{
	/* Actual CLIENT stat CR@2cfb48 and statistics@2cf1c0/2cf3a8.
	 * CCK, legacy OFDM, HT, VHT, HE; low16 OK, high16 CRC error. */
	static const u16 crc_registers[5] = { 0x1714, 0x1a64, 0x1a60, 0x1a58, 0x1a5c };
	/* Actual mac_rx_cnt@1f6b08 constructs these eight-entry arrays.
	 * Order CCK,OFDM,HT,VHTSU,VHTMU,HESU,HEMU,HETB. */
	static const u8 mac_indices[3][8] = {
		{ 3, 0, 6, 10, 14, 18, 22, 26 },
		{ 4, 1, 7, 11, 15, 19, 23, 27 },
		{ 5, 2, 9, 13, 17, 21, 25, 29 },
	};
	u16 *mac_counts[3] = { snapshot->mac_crc_ok, snapshot->mac_crc_error, snapshot->mac_false_alarm };
	unsigned int i, type;

	snapshot->cca[0] = io->read32(io->ctx, 0x11710) & 65535;
	snapshot->cca[1] = io->read32(io->ctx, 0x11a24) & 65535;
	for (i = 0; i < 5; i++) {
		u32 value = io->read32(io->ctx, 0x10000 | crc_registers[i]);

		snapshot->crc_ok[i] = value;
		snapshot->crc_error[i] = value >> 16;
	}
	/* Original read operation: W8 selector cee0, R16 count cee2.
	 * No reset operation or receive-policy change is issued. */
	for (type = 0; type < 3; type++)
		for (i = 0; i < 8; i++) {
			io->write8(io->ctx, 0xcee0, mac_indices[type][i]);
			mac_counts[type][i] = io->read16(io->ctx, 0xcee2);
		}
	snapshot->rx_index[0] = io->read32(io->ctx, 0x1218);
	snapshot->rx_index[1] = io->read32(io->ctx, 0x121c);
	snapshot->haxi = io->read32(io->ctx, RTL_AX_HAXI_CONFIG);
	snapshot->dmac = io->read32(io->ctx, 0x8400);
	snapshot->cmac = io->read32(io->ctx, 0xc000);
	snapshot->cck_gate[0] = io->read32(io->ctx, 0x10700);
	snapshot->cck_gate[1] = io->read32(io->ctx, 0x12320);
	snapshot->cck_gate[2] = io->read32(io->ctx, 0x10c80);
}
#endif
