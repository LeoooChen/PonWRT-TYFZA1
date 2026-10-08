// SPDX-License-Identifier: GPL-2.0-or-later
/* Apply only the missing native PON LDPID32..63 mappings on the live NI. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
static int __init map_init(void)
{
	void __iomem *ni = ioremap(0x4f4300000ULL, 0x10000);
	unsigned int flags, port;
	u32 status;
	int ret = 0;
	if (!ni)
		return -ENOMEM;
	for (flags = 0; flags < 256; flags += 64)
		for (port = 32; port < 64; port++) {
			writel(BIT(31) | flags | port, ni + 0x1654);
			ret = readl_poll_timeout(ni + 0x1654, status, !(status & BIT(31)), 1, 100000);
			if (ret)
				goto out;
			if (port == 32)
				pr_info("TYFZA1 PON map %02x: %u -> 8\n", flags | port, readl(ni + 0x1658));
			writel(8, ni + 0x1658);
			writel(BIT(31) | BIT(30) | flags | port, ni + 0x1654);
			ret = readl_poll_timeout(ni + 0x1654, status, !(status & BIT(31)), 1, 100000);
			if (ret)
				goto out;
		}
out:
	iounmap(ni);
	return ret;
}
static void __exit map_exit(void) {}
module_init(map_init);
module_exit(map_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 live PON NI destination mapping");
