/* SPDX-License-Identifier: GPL-2.0-only */
/* TYFZA1 AP post-download stages, following the board's mac_hal_init and
 * actual SCC tables in rtk_wifi6.ko. Host RX/TX/role setup follows these. */
#ifndef RTL_AX_AP_MAC_RUNTIME_H
#define RTL_AX_AP_MAC_RUNTIME_H
#include "download.h"

static inline int rtl_ax_host_getpkt(const struct rtl_ax_io *io, unsigned int macid)
{
	unsigned int retry;

	/* Actual mac_host_getpkt_h2c -> _mac_send_h2creg, C and XB.
	 * The receive worker calls this after validating a peer's PS-Poll. */
	for (retry = 0; retry < 100; retry++) {
		if (!(io->read8(io->ctx, 0x7160) & 1))
			break;
		io->delay_us(io->ctx, 200);
	}
	if (retry == 100)
		return -ETIMEDOUT;
	io->write32(io->ctx, 0x7140, 0x204 | macid << 16);
	io->write32(io->ctx, 0x7144, 0);
	io->write32(io->ctx, 0x7148, 0);
	io->write32(io->ctx, 0x714c, 0);
	io->write8(io->ctx, 0x7160, io->read8(io->ctx, 0x7160) | 1);
	return 0;
}

static inline void rtl_ax_bb_rf_disable(const struct rtl_ax_io *io)
{
	/* set_enable_bb_rf(enable=0), actual C/XB branch at21e29c. */
	rtl_ax_update32(io, 0x02f0, RTL_AX_BIT(17), 0);
	rtl_ax_update8(io, 0x0002, 3, 0);
}

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

static inline void rtl_ax_cmac_scheduler(const struct rtl_ax_io *io,
					 unsigned int chip)
{
	/* cmac_init@23f100..23f258, ASIC band0. AP beacon EDCA is16-bit. */
	rtl_ax_update32(io, 0xc33c, 0x7f, chip == RTL_AX_8852C ? 0x3e : 0x40);
	rtl_ax_update32(io, 0xc340, RTL_AX_BIT(5), 0);
	if (!(io->read32(io->ctx, 0x9d00) & RTL_AX_BIT(11)))
		rtl_ax_update32(io, 0xc338, 0x1f, 0x10);
	if (chip == RTL_AX_8852C)
		rtl_ax_update32(io, 0xc340, 0xff000000, 0x6a000000);
	io->write16(io->ctx, 0xc326, 0x3219); /* CWmin2,CWmax3,AIFS25. */
}

static inline int rtl_ax_cmac_address_cam(const struct rtl_ax_io *io)
{
	unsigned short last;

	/* addr_cam_init@1e8598: clear request is bit8,read back as16-bit. */
	rtl_ax_update32(io, 0xce34, 0, 0x007f0101);
	return io->poll16(io->ctx, 0xce34, 0x100, 0, 1, 2000, &last);
}

static inline void rtl_ax_cmac_receive_filter(const struct rtl_ax_io *io)
{
	/* Original rx_fltr_init and mac_set_typ_fltr_opt: HOST for all types.
	 * ELF rx_fltr_init_opt={3f,00,00,08,7f}; all five mask bytes areff.
	 * The original getter/converter discards undefined register bits. */
	io->write32(io->ctx, 0xce28, 0x55555555);
	io->write32(io->ctx, 0xce24, 0x55555555);
	io->write32(io->ctx, 0xce2c, 0x55555555);
	io->write32(io->ctx, 0xce20, 0x0800003f);
	io->write16(io->ctx, 0xce04, 0x007f);
}

static inline void rtl_ax_hal_start_receive(const struct rtl_ax_io *io,
	unsigned int rts_time, unsigned int rts_length)
{
	/* Actual hal_start_C/XB normal path uses RX_FLTR_MODE_AP_NORMAL4.
	 * rtw_hal_mac_set_rxfltr_by_mode@1b170c→converter@22fddc produces
	 * policy value440e/mask47ff. Its flags precede the original explicit
	 * MPDU limit11264B,represented by22 units of512B. */
	rtl_ax_update32(io, 0xce20, 0x000047ff, 0x0000440e);
	rtl_ax_update32(io, 0xce20, 0x003f0000, 22U << 16);
	/* hw_rts_th@1eb5b8:32us time units/highbyte,16B length units/lowbyte.
	 * Actual C/XB hw capability defaults both inputs to0. */
	io->write16(io->ctx, 0xc614, ((rts_time >> 5) << 8) | ((rts_length >> 4) & 255));
	/* The board passes frame type3 to subtype filtering. Its wrapper
	 * maps3→MAC DATA2. Actual callback maps DATA2 toce24. */
	rtl_ax_update32(io, 0xce24, 3U << 26, 0);
	rtl_ax_update32(io, 0xce24, 3U << 18, 0);
	rtl_ax_update32(io, 0xce24, 3U << 8, 0);
}

static inline int rtl_ax_receive_mode(const struct rtl_ax_io *io, unsigned int mode)
{
	/* Actual wrapper@1b1514 and converter@22fddc. Restore AP_NORMAL4
	 * before selecting SCAN1/SNIFFER0 so a previous monitor bit does
	 * not survive into a different requested operating mode. The explicit
	 * startup MPDU11264B limit is retained, as after the original wrapper. */
	if (mode != 0 && mode != 1 && mode != 4)
		return -EINVAL;
	rtl_ax_update32(io, 0xce20, 0x47ff, 0x440e);
	if (mode == 0)
		rtl_ax_update32(io, 0xce20, 0, 1);
	else if (mode == 1)
		rtl_ax_update32(io, 0xce20, 0xbe, 0x0e);
	return 0;
}

static inline void rtl_ax_cmac_medium_access(const struct rtl_ax_io *io)
{
	/* cmac_init@23f4e4 and240050. chk_patch_dis_resp_chk returnsfalse
	 * for both MAC chip IDs2/3,independent of their cuts. */
	rtl_ax_update32(io, 0xc390, ~0x71e1feffU, 0x712100ff);
	rtl_ax_update32(io, 0xcc00, 0, 0x00380000);
	rtl_ax_update32(io, 0xcc04, 0x00080000, 0x1f800000);
	/* mac_two_nav_cfg@23efb8: PLCP/trigger updates and12ms upper NAV,
	 * encoded as93 units of128us. */
	rtl_ax_update32(io, 0xcc80, 0x0000ff00, 0x04035d00);
	/* spatial_reuse_init@244f04 only clears SR_EN inthis board module. */
	rtl_ax_update8(io, 0xce4a, 1, 0);
}

static inline void rtl_ax_cmac_transmit(const struct rtl_ax_io *io)
{
	/* Original normal-mode TMAC. Its AP binary has no SW_PREFER_AC write
	 * between TXD_FIFO_CTRL and TRXPTCL,unlike the newer reference SDK. */
	rtl_ax_update32(io, 0xcc20, 1, 0);
	rtl_ax_update32(io, 0xca00, 0x007f0000, 0x00060000);
	rtl_ax_update32(io, 0xca1c, 0x0000ff00, 0x00007700);
}

static inline void rtl_ax_cmac_response(const struct rtl_ax_io *io)
{
	unsigned int control, rate, rates;

	/* Original C/XB branch uses CCK SIFS10,OFDM SIFS17 and checks trigger
	 * FCS. Private RRSR ops preserve defined fields,then write full words. */
	rtl_ax_update32(io, 0xcc04, 0x0000ffff, 0x0000110a);
	rtl_ax_update32(io, 0xccb0, 0, RTL_AX_BIT(20));
	control = io->read32(io->ctx, 0xcc08);
	rate = io->read32(io->ctx, 0xc090);
	rates = io->read32(io->ctx, 0xc08c);
	/* MAC get/set RRSR@24f09c/24f1d8: refer to RX rate AND CMAC table,
	 * retain OFDM/HT/VHT/HE rates,enable legacy rates and CCK1M only. */
	io->write32(io->ctx, 0xcc08, control & 0x0f3001ff);
	io->write32(io->ctx, 0xc090, (rate & 0x00000fc0) | 0x101);
	io->write32(io->ctx, 0xc08c, rates);
}

static inline int rtl_ax_cmac_receive(const struct rtl_ax_io *io, int firmware_tx)
{
	unsigned int last;
	int ret;

	/* rst_bacam@23c48c,all entries: bits0/1 must both clear. */
	rtl_ax_update32(io, 0xce3c, 3, 2);
	ret = io->poll32(io->ctx, 0xce3c, 3, 0, 1, 1000, &last);
	if (ret)
		return ret;
	rtl_ax_update8(io, 0xce3c, 0, 4); /* SSN selection. */
	/* cmac_init@23fcd0: keep low byte,replace upper byte of DLK timer. */
	io->write16(io->ctx, 0xce02, (io->read16(io->ctx, 0xce02) & 0xff) | 0x80f2);
	/* Original board binary@23f7fc uses15 for SW TX mode,1 for HW TX.
	 * This is a complete low-byte RCR write,not the newer SDK's RMW. */
	io->write8(io->ctx, 0xce00, firmware_tx ? 0x0f : 1);
	/* SCC C0 min quota100 x256B exceeds11966B spec maximum;512B units. */
	rtl_ax_update32(io, 0xce20, 0x003f0000, 23U << 16);
	/* chk_patch_wmac_timer_src returnsfalse forC/XB;no timer-source patch.
	 * Original RMAC skips VHT SU SIG-B CRC and has no RX_TIME_MON write. */
	rtl_ax_update8(io, 0xce04, 0x10, 0);
	return 0;
}

static inline void rtl_ax_cmac_protocol(const struct rtl_ax_io *io, int firmware_tx)
{
	/* cmac_com_init,normal TXSC. C/XB skip the older chips' RRSR1 patch. */
	rtl_ax_update32(io, 0xc088, 0xfff, 0);
	/* Original is_qta_poh@202c00 returns1 for PCIe SCC. That predicate
	 * is separate from the HFC POH control field's encoded mode0. */
	rtl_ax_update32(io, 0xc624, 0xfffc0000, 0x04050000);
	rtl_ax_update32(io, 0xc6e8, 0x40, 0x3f);
	if (firmware_tx)
		rtl_ax_update8(io, 0xc600, 3, 0x1c);
	else
		rtl_ax_update8(io, 0xc600, 0x1c, 3);
	rtl_ax_update8(io, 0xc660, 0x30, 0x10); /* Special reports to WCPU. */
	/* Original VHT aggregation patch and CMAC DMA writes are for other
	 * chip families. BothC/XB return without those writes. */
}

static inline int rtl_ax_mac_cmac_start(const struct rtl_ax_io *io,
					unsigned int chip, int firmware_tx)
{
	int ret;

	rtl_ax_cmac_scheduler(io, chip);
	/* rst_port_info resets host port/report objects only. Those belong to
	 * the forthcoming native AP role lifecycle,before host data starts. */
	ret = rtl_ax_cmac_address_cam(io);
	if (ret)
		return ret;
	rtl_ax_cmac_receive_filter(io);
	rtl_ax_cmac_medium_access(io);
	rtl_ax_cmac_transmit(io);
	rtl_ax_cmac_response(io);
	ret = rtl_ax_cmac_receive(io, firmware_tx);
	if (ret)
		return ret;
	rtl_ax_cmac_protocol(io, firmware_tx);
	return 0;
}

static inline void rtl_ax_mac_trx_registers(const struct rtl_ax_io *io)
{
	const struct {
		unsigned int address, keep, enable;
	} dmac[] = {
		{0x9430, 0xffffccc8, 0x00003327}, /* WDRLS */
		{0x9d2c, 0xffffffff, 0x00000003}, /* Security */
		{0x9bf4, 0xfffffc01, 0x0000036e}, /* MPDU TX */
		{0x9cf4, 0xfffffff4, 0x00000008}, /* MPDU RX */
		{0x9ef0, 0xffffffff, 0x00000007}, /* STA scheduler */
		{0x9f78, 0xffcaf0f0, 0x00350f03}, /* TX packet control0 */
		{0x9fb8, 0xffcaf0f0, 0x00350f03}, /* TX packet control1 */
		{0x8c38, 0xc0f00c00, 0x3f0ff3ff}, /* WDE */
		{0x9038, 0xc0f00c00, 0x3f0ff3ff}, /* PLE */
		{0x9a20, 0xffffffff, 0x00000001}, /* Packet-in */
		{0x8850, 0x00380000, 0x0c100060}, /* Host dispatcher */
		{0x8854, 0x80840002, 0x36000060}, /* CPU dispatcher */
		{0x8858, 0x00002020, 0x303cc000}, /* Other dispatcher */
		{0x9840, 0xffffeeee, 0x00001111}, /* CPU IO */
		{0x9608, 0xfffffffc, 0x00000000}, /* BB report common */
		{0x9628, 0xffffffff, 0x000000ff}, /* Channel-info */
		{0x9638, 0xffffffff, 0x00000001}, /* DFS */
		{0x966c, 0xffffffff, 0x00000001}, /* LA */
		{0x10b8, 0xffffffff, 0x0000000f}, /* PCIe HAXI errors */
	}, cmac[] = {
		{0xc3e8, 0xfffffffc, 0x00000001}, /* Scheduler */
		{0xc6c0, 0xffffffff, 0x00000003}, /* Protocol */
		{0xc804, 0x800000f9, 0x7fffff06}, /* CMAC DMA */
		{0xc870, 0xffffffff, 0xff00c000}, /* PHY interface */
		{0xccf8, 0xffffffff, 0x0000003f}, /* TRX protocol */
		{0xcef8, 0xfffffc00, 0x000003c0}, /* RMAC */
		{0xccbc, 0xfffffe00, 0x0000017f}, /* RX trigger */
	};
	unsigned int i, value, next;

	/* Actual mac_trx_init private slot0x128: both chips' coex init only
	 * writesda48=0. Then SER IMR runs DMAC before CMAC band0. */
	io->write32(io->ctx, 0xda48, 0);
	for (i = 0; i < sizeof(dmac) / sizeof(dmac[0]); i++)
		io->write32(io->ctx, dmac[i].address,
			    (io->read32(io->ctx, dmac[i].address) & dmac[i].keep) | dmac[i].enable);
	for (i = 0; i < sizeof(cmac) / sizeof(cmac[0]); i++)
		io->write32(io->ctx, cmac[i].address,
			    (io->read32(io->ctx, cmac[i].address) & cmac[i].keep) | cmac[i].enable);
	/* mac_err_imr_ctrl(1),SCC has no CMAC1. */
	io->write32(io->ctx, 0x8520, 0xffffffff);
	io->write32(io->ctx, 0xc160, 0xffffffff);
	/* set_host_rpr: PCIe SCC's POH profile enables all four WP outcomes.
	 * Eight-MSDU aggregates reduce 124 BIDs to 15 WPs. Report after 12
	 * WPs so recycling starts before the BID pool fills. */
	value = io->read32(io->ctx, 0x9408);
	next = value & ~3U;
	if (value != next)
		io->write32(io->ctx, 0x9408, next);
	value = io->read32(io->ctx, 0x9410);
	next = value | 0x0f000000;
	if (value != next)
		io->write32(io->ctx, 0x9410, next);
	value = io->read32(io->ctx, 0x9414);
	next = (value & 0xff00ff00) | 0x00ff000c;
	if (value != next)
		io->write32(io->ctx, 0x9414, next);
	/* set_l2_status changes a host state byte,not a hardware register.
	 * mac_feat_init next sends BA-CAM8-entry H2C;runtime H2C follows. */
}
#endif
