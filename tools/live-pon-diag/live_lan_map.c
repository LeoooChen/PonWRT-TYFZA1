// SPDX-License-Identifier: GPL-2.0-or-later
/* Inspect/apply the original aal_port ARB mapping for native LAN0..3. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
static bool apply;
module_param(apply, bool, 0444);
static int __init lan_map_init(void)
{
	void __iomem *ni = ioremap(0x4f4300000ULL, 0x10000);
	u32 status;
	unsigned int flags, port;
	int ret = 0;
	if (!ni)
		return -ENOMEM;
	for (flags = 0; flags < 256; flags += 64)
		for (port = 0; port < 4; port++) {
			unsigned int wanted = flags & 64 ? 8 : port;
			writel(BIT(31) | flags | port, ni + 0x1654);
			ret = readl_poll_timeout(ni + 0x1654, status, !(status & BIT(31)), 1, 100000);
			if (ret)
				goto out;
			pr_info("TYFZA1 LAN ARB index=%02x old=%u required=%u apply=%u\n",
				flags | port, readl(ni + 0x1658), wanted, apply);
			if (apply) {
				writel(wanted, ni + 0x1658);
				writel(BIT(31) | BIT(30) | flags | port, ni + 0x1654);
				ret = readl_poll_timeout(ni + 0x1654, status, !(status & BIT(31)), 1, 100000);
				if (ret)
					goto out;
			}
		}
out:
	iounmap(ni);
	return ret;
}
static void __exit lan_map_exit(void) {}
module_init(lan_map_init);
module_exit(lan_map_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 native LAN destination map inspection");
