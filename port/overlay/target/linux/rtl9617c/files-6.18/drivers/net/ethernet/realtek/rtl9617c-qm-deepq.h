/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_QM_DEEPQ_H
#define RTL9617C_QM_DEEPQ_H

/* Native WAN private/shared DRAM pools. CPU EQ4/5 own BIDs0..511;
 * these two hardware-managed pools own BIDs512..1535. */
#define RTL_DQ_BUFFERS 512
#define RTL_DQ_BUFFER_BYTES 2048
#define RTL_DQ_MEMORY_BYTES (2 * RTL_DQ_BUFFERS * RTL_DQ_BUFFER_BYTES)
#define RTL_DQ_EQ_PRIVATE 1
#define RTL_DQ_EQ_SHARED 2

struct rtl_dq_io {
	void *context;
	unsigned int (*read)(void *context, unsigned int reg);
	void (*write)(void *context, unsigned int reg, unsigned int value);
	int (*indirect)(void *context, unsigned int reg, unsigned int value);
};

/* GPL aal-77c/aal_l3qm.c init_DQ_shared_dram, init_empty_buffer_DQ_1;
 * field layouts from the RTL9617C register header. No vendor reserved RAM. */
static inline int rtl_qm_deepq_prepare(const struct rtl_dq_io *io, unsigned int address)
{
	unsigned int pool, port, voq;
	unsigned int count = RTL_DQ_BUFFERS;
	unsigned int low = (count * 2) / 3;
	unsigned int high = low * 2;
	int ret;

	/* Keep PUC dequeue disabled until the native pools and credits are ready. */
	io->write(io->context, 0x610c, io->read(io->context, 0x610c) & ~0xffffU);
	for (pool = 0; pool < 2; pool++) {
		unsigned int eq = RTL_DQ_EQ_PRIVATE + pool;
		unsigned int cfg = 0x61e8 + eq * 20;

		io->write(io->context, cfg, (address + pool * count * RTL_DQ_BUFFER_BYTES) | 1U);
		io->write(io->context, cfg + 4, (count << 16) | (512 + pool * count));
		io->write(io->context, cfg + 8, 5); /* 2KiB, hardware-owned, no CPU refill. */
		/* Linux coherent allocations use the same CCI alias as native CPU EQs. */
		io->write(io->context, cfg + 12, 0x61008060);
		io->write(io->context, cfg + 16, 2);
	}
	io->write(io->context, 0x612c, RTL_DQ_EQ_PRIVATE | (RTL_DQ_EQ_SHARED << 4));
	io->write(io->context, 0x68f8, (io->read(io->context, 0x68f8) & ~15U) | 10);
	/* Stock TYFZA1 selects ordinary 2KiB buffers, without jumbo packing. */
	io->write(io->context, 0x68b0, io->read(io->context, 0x68b0) & ~0xc8000000U);
	io->write(io->context, 0x958c, low | (high << 16));
	io->write(io->context, 0x959c, 0x3fff3fff); /* Group admission thresholds disabled. */
	io->write(io->context, 0x95a4, ~0U); /* PON DQ ports16..31 use port profile3. */
	/* WAN VOQ profile3: high/low derived from its shared native pool. */
	io->write(io->context, 0x95b4, 0);
	io->write(io->context, 0x95b8, (25U << 24) | (0x3fffU << 10) | 0x3ffU);
	io->write(io->context, 0x95bc, low | (count << 14) | 0xf0000000U);
	ret = io->indirect(io->context, 0x95b0, 0xc0000003U);
	if (ret)
		return ret;
	for (port = 16; port < 32; port++) {
		io->write(io->context, 0x6148 + port * 4, 1); /* WAN profile1. */
		io->write(io->context, 0x63c4 + port * 4, 255);
		io->write(io->context, 0x6444 + port * 8, 0x01010101);
		io->write(io->context, 0x6448 + port * 8, 0x01010101);
		io->write(io->context, 0x95ac, count | (1U << 14) | (count << 16) | (1U << 30));
		ret = io->indirect(io->context, 0x95a8, 0xc0000000U | port);
		if (ret)
			return ret;
		for (voq = port * 8; voq < port * 8 + 8; voq++) {
			io->write(io->context, 0x95c4, 3);
			ret = io->indirect(io->context, 0x95c0, 0xc0000000U | voq);
			if (ret)
				return ret;
		}
	}
	return 0;
}
#endif
