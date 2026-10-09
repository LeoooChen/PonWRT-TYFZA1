/* SPDX-License-Identifier: GPL-2.0-only */
/* RTL8852C/8192XB AP download memory and HCI flow control. */
#ifndef RTL_AX_AP_DOWNLOAD_H
#define RTL_AX_AP_DOWNLOAD_H
#include "power.h"

#define RTL_AX_DMAC_CLOCK 0x8404
#define RTL_AX_HAXI_CONFIG 0x1000
#define RTL_AX_HAXI_STOP1 0x1010
#define RTL_AX_HAXI_STOP2 0x11c0
#define RTL_AX_HCI_FC 0x1700
#define RTL_AX_HCI_PAGE 0x1704
#define RTL_AX_DLE_ENABLE (RTL_AX_BIT(26) | RTL_AX_BIT(23))

static inline void rtl_ax_dmac_pre_enable(const struct rtl_ax_io *io)
{
	io->write32(io->ctx, RTL_AX_DMAC, 0x60444000);
	io->write32(io->ctx, RTL_AX_DMAC_CLOCK, RTL_AX_BIT(18));
	/* Native PCIe one-buffer DMA mode. */
	rtl_ax_update32(io, RTL_AX_HAXI_CONFIG, (3U << 18) | RTL_AX_BIT(17),
			RTL_AX_BIT(7) | RTL_AX_BIT(15));
	rtl_ax_update32(io, RTL_AX_HAXI_STOP1, 0x7ff00, 0);
	rtl_ax_update32(io, RTL_AX_HAXI_STOP2, 3, 0);
	rtl_ax_update32(io, RTL_AX_PLATFORM, 0, RTL_AX_BIT(3));
}

static inline void rtl_ax_hci_pause(const struct rtl_ax_io *io)
{
	/* RX and all TX channels stay stopped until their host rings exist. */
	rtl_ax_update32(io, RTL_AX_HAXI_STOP1, 0, 0xfff00);
	rtl_ax_update32(io, RTL_AX_HAXI_STOP2, 0, 3);
	rtl_ax_update32(io, RTL_AX_HAXI_CONFIG,
			RTL_AX_BIT(7) | RTL_AX_BIT(15), RTL_AX_BIT(17));
}

static inline void rtl_ax_download_stop(const struct rtl_ax_io *io)
{
	rtl_ax_hci_pause(io);
	rtl_ax_update32(io, RTL_AX_HCI_FC, RTL_AX_BIT(0) | RTL_AX_BIT(3), 0);
	rtl_ax_update32(io, RTL_AX_DMAC, RTL_AX_DLE_ENABLE, 0);
}

static inline int rtl_ax_dle_download(const struct rtl_ax_io *io,
				    unsigned int wcpu_minimum)
{
	unsigned int i, last;
	int ret;

	/* Both original PCIe DLFW tables: WDE 2048 x 64B unlinked, PLE
	 * 2544 x 128B linked + 16 unlinked, totaling the 448KiB FIFO. */
	rtl_ax_update32(io, RTL_AX_DMAC, RTL_AX_DLE_ENABLE, 0);
	rtl_ax_update32(io, RTL_AX_DMAC_CLOCK, 0, RTL_AX_DLE_ENABLE);
	rtl_ax_update32(io, 0x8c08, 0x1fff3f03, 0);
	rtl_ax_update32(io, 0x9008, 0x1fff3f03, (2544U << 16) | (16U << 8) | 1);
	io->write32(io->ctx, 0x8c40, 0);
	/* DLFW copies the operational profile's minimum WCPU quota: 60 for
	 * this board's SCC profile. STF has a different WCPU minimum (20),
	 * so the caller must supply the selected profile's value. */
	io->write32(io->ctx, 0x8c44, wcpu_minimum);
	io->write32(io->ctx, 0x8c4c, 0);
	io->write32(io->ctx, 0x8c50, 0);
	for (i = 0; i < 12; i++) {
		unsigned int quota = i == 2 ? 16 | (32U << 16) :
				     i == 3 ? 256 | (256U << 16) : 0;

		io->write32(io->ctx, 0x9040 + i * 4, quota);
	}
	rtl_ax_update32(io, RTL_AX_DMAC, 0, RTL_AX_DLE_ENABLE);
	ret = io->poll32(io->ctx, 0x8d00, 3, 3, 1, 2000, &last);
	if (!ret)
		ret = io->poll32(io->ctx, 0x9100, 3, 3, 1, 2000, &last);
	if (ret)
		rtl_ax_update32(io, RTL_AX_DMAC, RTL_AX_DLE_ENABLE, 0);
	return ret;
}

static inline void rtl_ax_hfc_download(const struct rtl_ax_io *io,
	unsigned int h2c_precost, unsigned int full_condition)
{
	rtl_ax_update32(io, RTL_AX_HCI_FC, RTL_AX_BIT(0) | RTL_AX_BIT(3), 0);
	io->write32(io->ctx, RTL_AX_HCI_PAGE, h2c_precost << 16);
	rtl_ax_update32(io, RTL_AX_HCI_FC, 3U << 10, full_condition << 10);
	rtl_ax_update32(io, RTL_AX_HCI_FC, RTL_AX_BIT(0), RTL_AX_BIT(3));
}

static inline int rtl_ax_download_prepare(const struct rtl_ax_io *io)
{
	int ret;

	rtl_ax_dmac_pre_enable(io);
	rtl_ax_hci_pause(io);
	ret = rtl_ax_dle_download(io, 60);
	if (ret)
		return ret;
	/* Original hfc_reset_param(DLFW) retains the allocated precost state,
	 * rather than copying the SDK's unused 256-page table. Fresh state is
	 * zero; post-download operational HFC will configure its own precost. */
	rtl_ax_hfc_download(io, 0, 0);
	return 0;
}
#endif
