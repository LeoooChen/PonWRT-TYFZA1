// SPDX-License-Identifier: GPL-2.0-or-later
/* Map individual Header-I bits through SWO using an otherwise unused mask. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include "rtl9617c-fe-table.h"

static void __iomem *ni;
static unsigned int reg_read(void *ctx, unsigned int reg) { return readl(ni + reg); }
static void reg_write(void *ctx, unsigned int reg, unsigned int value) { writel(value, ni + reg); }
static int reg_access(void *ctx, unsigned int reg, unsigned int value)
{
	u32 status, busy = reg == 0x38e0 ? 1 : BIT(31);
	writel(value, ni + reg);
	return readl_poll_timeout(ni + reg, status, !(status & busy), 1, 100000);
}
static const struct rtl_dq_io io = {NULL, reg_read, reg_write, reg_access};

static int tuple(unsigned int header[32], unsigned int key[23])
{
	unsigned int crc32, i;
	unsigned short crc16;
	int ret = rtl_fe_crc_hardware(&io, header, 61, &crc32, &crc16);

	if (!ret)
		for (i = 0; i < 23; i++) key[i] = readl(ni + 0x38dc);
	return ret;
}

static int __init probe_init(void)
{
	unsigned int saved[8], mask[8] = {}, readback[8], header[32], base[23], first[23], key[23];
	struct rtl_fe_ipv4_key input = {
		.source = 0xc6120012, .destination = 0xc6120013,
		.source_port = 48518, .destination_port = 48519, .protocol = 17, .ingress = 18,
	};
	unsigned int bit, word, mapped = 0, unstable = 0;
	int ret, restored;

	ni = ioremap(0x4f4300000ULL, 0x10000);
	if (!ni) return -ENOMEM;
	ret = rtl_fe_mask_read(&io, 61, saved);
	if (ret) goto done;
	ret = rtl_fe_mask_write(&io, 61, mask);
	if (ret) goto restore;
	ret = rtl_fe_mask_read(&io, 61, readback);
	if (ret || memcmp(mask, readback, sizeof(mask))) { ret = -EIO; goto restore; }
	rtl_fe_header_ipv4(header, &input);
	ret = tuple(header, base);
	if (ret) goto restore;
	ret = tuple(header, base);
	if (ret) goto restore;
	for (bit = 700; bit < 832; bit++) {
		bool changed = false;
		rtl_fe_header_ipv4(header, &input);
		header[bit / 32] ^= BIT(bit % 32);
		ret = tuple(header, first);
		if (ret) break;
		ret = tuple(header, key);
		if (ret) break;
		if (memcmp(first, key, sizeof(key))) unstable++;
		for (word = 0; word < 23; word++) {
			unsigned int delta = base[word] ^ key[word];
			if (delta) {
				pr_info("TYFZA1 SWO map input=%u key_word=%u xor=%08x repeat_equal=%u\n",
					bit, word, delta, !memcmp(first, key, sizeof(key)));
				changed = true;
			}
		}
		mapped += changed;
		if (!changed) pr_info("TYFZA1 SWO map input=%u no_key_change\n", bit);
	}
	pr_info("TYFZA1 SWO map vectors=%u mapped=%u unstable=%u error=%d\n", bit - 700, mapped, unstable, ret);
restore:
	restored = rtl_fe_mask_write(&io, 61, saved);
	if (!restored) restored = rtl_fe_mask_read(&io, 61, readback);
	pr_info("TYFZA1 SWO map mask61_restored=%u error=%d\n",
		!restored && !memcmp(saved, readback, sizeof(saved)), restored);
done:
	iounmap(ni);
	return ret;
}
static void __exit probe_exit(void) { }
module_init(probe_init);
module_exit(probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 SWO Header-I to Hash-key single-bit map");
