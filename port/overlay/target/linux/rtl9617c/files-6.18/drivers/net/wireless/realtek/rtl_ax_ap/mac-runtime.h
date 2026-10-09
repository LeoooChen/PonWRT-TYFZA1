/* SPDX-License-Identifier: GPL-2.0-only */
/* TYFZA1 AP post-download stages, following the board's mac_hal_init and
 * actual SCC tables in rtk_wifi6.ko. Host RX/TX/role setup follows these. */
#ifndef RTL_AX_AP_MAC_RUNTIME_H
#define RTL_AX_AP_MAC_RUNTIME_H
#include "download.h"

static inline int rtl_ax_bb_rf_enable(const struct rtl_ax_io *io, unsigned int chip)
{
	unsigned int i;
	int ret;
	const unsigned char addresses[] = {0x00, 0x90, 0x80, 0x81, 0x03};
	unsigned char values[] = {0x07, 0x6c, 0xc7, 0xc7, chip == RTL_AX_8852C ? 0x0d : 0x0f};

	rtl_ax_update8(io, 0x0002, 0, 3);
	if (chip == RTL_AX_8852C)
		rtl_ax_update32(io, 0x0200, 3U << 17, 1U << 17);
	rtl_ax_update32(io, 0x02f0, 0, RTL_AX_BIT(17));
	rtl_ax_update32(io, 0x02f0, RTL_AX_BIT(17), 0);
	rtl_ax_update32(io, 0x02f0, 0, RTL_AX_BIT(17));
	if (chip == RTL_AX_8852C)
		rtl_ax_update32(io, 0x0444, 0x03600000, 0x01200000);
	for (i = 0; i < sizeof(addresses); i++) {
		ret = rtl_ax_xtal_write(io, addresses[i], values[i], 255);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_ax_mac_system(const struct rtl_ax_io *io, unsigned char *secure)
{
	unsigned char efuse;
	int ret;

	/* Both original dmac_func_en implementations write these complete masks. */
	io->write32(io->ctx, RTL_AX_DMAC, 0xfb7d4000);
	io->write32(io->ctx, RTL_AX_DMAC_CLOCK, 0x0b1f0000);
	/* cmac_func_en(BAND0,1), separate from each chip's AP power sequence. */
	rtl_ax_update32(io, 0xc004, 0, 0x4000003f);
	rtl_ax_update32(io, 0xc000, 0, 0xf000003f);
	ret = rtl_ax_efuse_read(io, 0x5ed, &efuse);
	if (!ret)
		*secure = !(efuse & 0x80); /* Original mac_chk_sec_rec. */
	return ret;
}

static inline int rtl_ax_dle_scc(const struct rtl_ax_io *io)
{
	const unsigned short wde[] = {3228, 60, 0, 0, 40};
	const unsigned short ple_min[] = {100, 0, 8, 10, 7, 7, 90, 0, 16, 31, 4, 8};
	const unsigned short ple_max[] = {771, 0, 16, 10, 678, 7, 761, 0, 687, 31, 80, 679};
	unsigned int i, last;
	int ret;

	/* Actual AP SCC: 3328 x64B WDE; PLE952 linked+8 unlinked x256B.
	 * PLE starts after WDE at 26 x8KiB; the combined FIFO remains448KiB. */
	rtl_ax_update32(io, RTL_AX_DMAC, RTL_AX_DLE_ENABLE, 0);
	rtl_ax_update32(io, RTL_AX_DMAC_CLOCK, 0, RTL_AX_DLE_ENABLE);
	rtl_ax_update32(io, 0x8c08, 0x1fff3f03, 3328U << 16);
	rtl_ax_update32(io, 0x9008, 0x1fff3f03, (952U << 16) | (26U << 8) | 2);
	for (i = 0; i < 5; i++) {
		if (i == 2)
			continue; /* Original WDE quota routine does not write DCPU Q2. */
		io->write32(io->ctx, 0x8c40 + i * 4, wde[i] | (unsigned int)wde[i] << 16);
	}
	for (i = 0; i < 12; i++)
		io->write32(io->ctx, 0x9040 + i * 4,
			    ple_min[i] | (unsigned int)ple_max[i] << 16);
	rtl_ax_update32(io, RTL_AX_DMAC, 0, RTL_AX_DLE_ENABLE);
	ret = io->poll32(io->ctx, 0x8d00, 3, 3, 1, 2000, &last);
	if (!ret)
		ret = io->poll32(io->ctx, 0x9100, 3, 3, 1, 2000, &last);
	if (ret)
		rtl_ax_update32(io, RTL_AX_DMAC, RTL_AX_DLE_ENABLE, 0);
	return ret;
}

static inline void rtl_ax_preload_scc(const struct rtl_ax_io *io)
{
	/* Original preload_init_set(BAND0,SCC). Keep both CAM entry fields. */
	rtl_ax_update32(io, 0x9f48, 0x03ff0000, 0x82080000);
	rtl_ax_update32(io, 0x9f4c, 0x00000fff, 0x00000134);
}

static inline void rtl_ax_hfc_scc(const struct rtl_ax_io *io, unsigned int chip)
{
	unsigned int channel;

	rtl_ax_update32(io, RTL_AX_HCI_FC, RTL_AX_BIT(0) | RTL_AX_BIT(3), 0);
	for (channel = 0; channel < 12; channel++) {
		unsigned int value;

		if (chip == RTL_AX_8192XB && ((0x0cf0U >> channel) & 1))
			continue; /* Original HFC skips XB's unavailable DMA channels. */
		value = channel < 4 || channel == 8 || channel == 9 ?
			26 | (3098U << 16) : 0;
		io->write32(io->ctx, 0x1710 + channel * 4, value);
	}
	/* Board AP tables put all3228 public pages in group0. */
	io->write32(io->ctx, 0x1790, 3228);
	io->write32(io->ctx, 0x17a4, 0);
	/* Actual hfc_preccfg_pcie={2,40,0,0,X2,X1,X1,X1},POH mode0. */
	io->write32(io->ctx, 0x1704, 2 | (40U << 16));
	io->write32(io->ctx, 0x1794, 3228);
	io->write32(io->ctx, 0x17a0, 0);
	rtl_ax_update32(io, 0x1700, 0xff6, 1U << 4);
	rtl_ax_update32(io, 0x1700, 0, RTL_AX_BIT(0) | RTL_AX_BIT(3));
	io->delay_us(io->ctx, 10);
}

static inline int rtl_ax_mac_memory_start(const struct rtl_ax_io *io,
					unsigned int chip, unsigned char *secure)
{
	int ret;

	ret = rtl_ax_bb_rf_enable(io, chip);
	if (ret)
		return ret;
	ret = rtl_ax_mac_system(io, secure);
	if (ret)
		return ret;
	ret = rtl_ax_dle_scc(io);
	if (ret)
		return ret;
	rtl_ax_preload_scc(io);
	rtl_ax_hfc_scc(io, chip);
	return 0;
}

static inline int rtl_ax_sta_scheduler(const struct rtl_ax_io *io, int firmware_tx)
{
	unsigned int last;
	int ret;

	/* Original dmac_init inlines sta_sch_init at0x23e99c: enable, wait
	 * INIT_DONE_1, warm-init flag, mode and52C/92XB SS2F path correction. */
	rtl_ax_update8(io, 0x9e10, 0, 1);
	ret = io->poll32(io->ctx, 0x9e10, RTL_AX_BIT(31), RTL_AX_BIT(31),
			 1, 2000, &last);
	if (ret)
		return ret;
	rtl_ax_update32(io, 0x9e10, 0, RTL_AX_BIT(29));
	rtl_ax_update32(io, 0x9e10, RTL_AX_BIT(28), firmware_tx ? RTL_AX_BIT(28) : 0);
	rtl_ax_update32(io, 0x9e50, 0x3f00, 0x0a00);
	if (firmware_tx) {
		io->write32(io->ctx, 0x9e24, 0x0003001b);
		io->write32(io->ctx, 0x9e1c, 0x00010050);
		io->write32(io->ctx, 0x9ea4, 0x0000ffff);
		io->write32(io->ctx, 0x9e9c, 0x0000ffff);
		rtl_ax_update32(io, 0x9e20, 0, 0x00003f3f);
	}
	return 0;
}

static inline void rtl_ax_mpdu_initialize(const struct rtl_ax_io *io)
{
	io->write32(io->ctx, 0x9c04, 0x02a95a95); /* ACTION_FWD0 */
	io->write32(io->ctx, 0x9c14, 0x0000aa55); /* Trigger frame forwarding. */
	rtl_ax_update32(io, 0x9c00, 0, 3); /* Append FCS,accept ICV errors as original. */
	io->write32(io->ctx, 0x9c40, 0x010e05f0); /* CUT_AMSDU_CTRL. */
}

static inline void rtl_ax_security_initialize(const struct rtl_ax_io *io)
{
	/* Original PCIe branch retains TX_PARTIAL_MODE and enables cipher
	 * clocks,TX encryption/RX decryption including multicast/management. */
	rtl_ax_update32(io, 0x9d00, 0, 0x0000073f);
	rtl_ax_update32(io, 0x9d04, 0, 3); /* Append MIC and ICV. */
	rtl_ax_update32(io, 0x9d1c, 0xc0000000, 0x80000000); /* TX timeout selector. */
	rtl_ax_update32(io, 0xce00, 0x00000700, 0x00000500); /* AP driver-info patch. */
}

static inline int rtl_ax_mac_dmac_start(const struct rtl_ax_io *io, int firmware_tx)
{
	int ret;

	ret = rtl_ax_sta_scheduler(io, firmware_tx);
	if (ret)
		return ret;
	rtl_ax_mpdu_initialize(io);
	rtl_ax_security_initialize(io);
	return 0;
}
#endif
