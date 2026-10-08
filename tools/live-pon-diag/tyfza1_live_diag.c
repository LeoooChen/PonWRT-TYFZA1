// SPDX-License-Identifier: GPL-2.0-or-later
/* Read-only snapshots of this TYFZA1's PON/global/GPIO registers. */
#include <linux/io.h>
#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/iopoll.h>

static void __iomem *global_low, *global_high, *mac, *ni;

static int diag_show(struct seq_file *seq, void *unused)
{
	static const unsigned int global_regs[] = {
		0x74, 0x7c, 0x80, 0x88, 0x8c, 0x90, 0x94, 0xf0, 0xf4,
		0x100, 0x164, 0x168, 0x16c, 0x170, 0x180, 0x184, 0x188, 0x18c,
		0x92c0, 0x92c4, 0x92c8,
	};
	static const unsigned int mac_regs[] = {
		0, 4, 0x10, 0x14, 0x180, 0x1b4, 0x1b8, 0x1d8, 0x1dc, 0x1c4, 0x1c8, 0x2000, 0x2004,
		0x2008, 0x200c, 0x2014, 0x2020, 0x2024, 0x20d8,
		0x814c, 0x8150, 0x8154, 0x8158, 0x815c,
		0x8174, 0x8178, 0x817c, 0x8180, 0x8184, 0x8188, 0x8190,
		0x8194, 0x8198, 0x819c, 0x1288, 0x1290, 0x12a0, 0x12a8,
		0x50, 0x54, 0x58, 0x5c, 0x64, 0x810, 0x834, 0x88c, 0x1250,
		0x9000, 0x9004, 0x9008, 0x900c, 0x9010, 0x9014, 0x9018, 0x901c, 0x9034,
		0x638, 0x63c, 0x8050, 0x8054, 0x8058, 0x805c,
		0x8080, 0x8084, 0x8108, 0x8110, 0x811c, 0x8120, 0x8124, 0x8128, 0x812c, 0x8130,
		0x808c, 0x80e0, 0x813c, 0x8140, 0x8144, 0x8148,
	};
	static const unsigned int ni_regs[] = {
		0x2300, 0x2394, 0x2398, 0x239c, 0x2d60, 0x2d64,
		0xa1bc, 0xa1f4, 0xa120, 0xa124, 0xa128,
		0x61fc, 0x6200, 0x6210, 0x6214, 0x632c, 0x6330, 0x610c, 0x68f8,
		0x67e4, 0x67e8, 0x6830, 0x6840, 0x6850, 0x9510, 0x9514,
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(global_regs); i++)
		seq_printf(seq, "global_%04x low=%08x high=%08x\n", global_regs[i],
			readl(global_low + global_regs[i]), readl(global_high + global_regs[i]));
	for (i = 0; i < ARRAY_SIZE(mac_regs); i++)
		seq_printf(seq, "mac_%04x=%08x\n", mac_regs[i], readl(mac + mac_regs[i]));
	for (i = 0; i < ARRAY_SIZE(ni_regs); i++)
		seq_printf(seq, "ni_%04x=%08x\n", ni_regs[i], readl(ni + ni_regs[i]));
	/* Per-LLID Ethernet MIBs: byte/multicast/broadcast/total and size bins. */
	for (i = 0; i < 16; i++) {
		u32 status;
		writel(BIT(31) | i, mac + 0x610);
		if (readl_poll_timeout(mac + 0x610, status, !(status & BIT(31)), 1, 100000))
			return -ETIMEDOUT;
		seq_printf(seq, "rx_mib%u=%08x\n", i, readl(mac + 0x614));
		writel(BIT(31) | i, mac + 0x628);
		if (readl_poll_timeout(mac + 0x628, status, !(status & BIT(31)), 1, 100000))
			return -ETIMEDOUT;
		seq_printf(seq, "tx_mib%u=%08x\n", i, readl(mac + 0x62c));
	}
	/* Read-only TQ table transaction while PON control is idle. */
	for (i = 0; i < 16; i++) {
		u32 status;
		unsigned int queue = i < 8 ? i : 120 + i;
		writel(BIT(31) | queue, mac + 0x802c);
		if (readl_poll_timeout(mac + 0x802c, status, !(status & BIT(31)), 1, 100000))
			return -ETIMEDOUT;
		seq_printf(seq, "tq%u=%08x/%08x\n", queue, readl(mac + 0x8030), readl(mac + 0x8034));
	}
	return 0;
}

static int __init diag_init(void)
{
	global_low = ioremap(0xf4320000ULL, 0x10000);
	global_high = ioremap(0x4f4320000ULL, 0x10000);
	mac = ioremap(0x4f5500000ULL, 0xc000);
	ni = ioremap(0x4f4300000ULL, 0x10000);
	if (!global_low || !global_high || !mac || !ni)
		goto unmap;
	if (!proc_create_single("tyfza1_pon_regs", 0400, NULL, diag_show))
		goto unmap;
	return 0;
unmap:
	iounmap(ni);
	iounmap(mac);
	iounmap(global_high);
	iounmap(global_low);
	return -ENOMEM;
}

static void __exit diag_exit(void)
{
	remove_proc_entry("tyfza1_pon_regs", NULL);
	iounmap(ni);
	iounmap(mac);
	iounmap(global_high);
	iounmap(global_low);
}

module_init(diag_init);
module_exit(diag_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 read-only live PON register snapshot");
