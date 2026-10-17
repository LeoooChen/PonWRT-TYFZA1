// SPDX-License-Identifier: GPL-2.0-or-later
/* Isolated Wi-Fi NI lookup and optional two-ID rule/delete experiment on VP9.
 * Snapshot QM buffers without consuming EPPs or changing buffer pool state. */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/etherdevice.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/unaligned.h>
#include <net/ip.h>
#include <net/checksum.h>
#include "rtl9617c-wifi-format.h"
#include "rtl9617c-fe-pipeline.h"

#define VP 9
#define COUNT 64
#define BYTES (COUNT * 8 + COUNT * 128)
#define QM_BYTES (8192 + 512 * 2048)
#define MAGIC 0x57464e45U
static void __iomem *ni, *dma, *vp;
static void *memory, *qm;
static dma_addr_t bus;
static struct net_device *netdev;
static unsigned int saved[5], first, sent;
static unsigned int before[8];
static bool configured;
static unsigned int saved_ippb;
static bool ippb_changed;
static unsigned int saved_debug;
static bool rules;
module_param(rules, bool, 0444);
static void *tables;
static unsigned int rule_slot[2], rule_crc[2], saved_mask62[8], saved_profile[3];
static unsigned short rule_crc16[2];
static unsigned int rule_count;
static bool rules_configured;
static unsigned int saved_hash_ini;

static unsigned int reg_read(void *ctx, unsigned int reg) { return readl(ni + reg); }
static void reg_write(void *ctx, unsigned int reg, unsigned int value) { writel(value, ni + reg); }
static int reg_access(void *ctx, unsigned int reg, unsigned int value)
{
	u32 status, busy = reg == 0x38e0 || reg == 0x38b0 ? 1 : BIT(31);
	writel(value, ni + reg);
	return readl_poll_timeout(ni + reg, status, !(status & busy), 1, 100000);
}
static const struct rtl_dq_io io = {NULL, reg_read, reg_write, reg_access};
static unsigned int field(const unsigned int *words, unsigned int bit, unsigned int width)
{
	u64 value = words[bit / 32];
	if (bit % 32 + width > 32) value |= (u64)words[bit / 32 + 1] << 32;
	return (value >> (bit % 32)) & (width == 32 ? ~0U : (1U << width) - 1);
}

static int install_rules(void)
{
	struct rtl_fe_ipv4_key key = {.source = 0xc6120012, .destination = 0xc6120013,
		.source_port = 48518, .destination_port = 48519, .protocol = 17, .ingress = 18};
	unsigned int mask[8], words[23], action[8], id, slot;
	int ret;

	tables = memremap(readl(ni + 0x383c), RTL_FE_MEMORY_BYTES, MEMREMAP_WB);
	if (!tables) return -ENOMEM;
	dma_sync_single_for_cpu(netdev->dev.parent, readl(ni + 0x383c), RTL_FE_MEMORY_BYTES, DMA_FROM_DEVICE);
	ret = rtl_fe_mask_read(&io, 62, saved_mask62);
	if (ret) return ret;
	ret = rtl_fe_mask_read(&io, 63, mask);
	if (ret) return ret;
	saved_profile[0] = readl(ni + 0x3700); saved_profile[1] = readl(ni + 0x3708);
	saved_profile[2] = readl(ni + 0x3704);
	rules_configured = true;
	saved_hash_ini = readl(ni + 0x3834);
	writel(saved_hash_ini | BIT(17), ni + 0x3834);
	rtl_fe_wifi_mask(mask);
	ret = rtl_fe_mask_write(&io, 62, mask);
	if (ret) return ret;
	for (id = 0; id < 2; id++) {
		rtl_fe_ipv4_key(words, &key);
		rtl_fe_key_set(words, RTL_FE_KEY_ORIG_LSPID, 0);
		rtl_fe_key_set(words, RTL_FE_KEY_TABLE_ID, 0);
		rtl_fe_key_set(words, RTL_FE_KEY_MDATA_BYTE, id + 1);
		rtl_fe_key_crc(words, &rule_crc[id], &rule_crc16[id]);
		for (slot = rtl_fe_hash_bucket(rule_crc16[id]); slot < rtl_fe_hash_bucket(rule_crc16[id]) + 32; slot++)
			if (!READ_ONCE(((u32 *)tables)[slot])) break;
		if (slot == rtl_fe_hash_bucket(rule_crc16[id]) + 32) return -ENOSPC;
		memset(action, 0, sizeof(action));
		rtl_fe_action_set(action, RTL_FE_DPID_VLD, 1);
		rtl_fe_action_set(action, RTL_FE_DPID_PRI, 1);
		rtl_fe_action_set(action, RTL_FE_PERMIT, 1);
		rtl_fe_action_set(action, RTL_FE_MCGID, 16);
		rtl_fe_action_set(action, RTL_FE_MDATA_BYTE_VLD, 1);
		rtl_fe_action_set(action, RTL_FE_MDATA_BYTE, id + 5);
		rtl_fe_action_set(action, RTL_FE_CHK_MSK_PTR1, 62);
		rtl_fe_action_set(action, RTL_FE_CACHE_CTRL1, 1);
		rtl_fe_action_set(action, RTL_FE_T2_CTRL_VLD1, 1);
		rtl_fe_action_set(action, RTL_FE_T2_CTRL1, 15);
		memcpy(tables + RTL_FE_ACTION_OFFSET + slot * RTL_FE_ACTION_BYTES, action, sizeof(action));
		WRITE_ONCE(((u32 *)tables)[slot], rule_crc[id]);
		dma_sync_single_for_device(netdev->dev.parent, readl(ni + 0x383c) + slot * 4, 4, DMA_TO_DEVICE);
		dma_sync_single_for_device(netdev->dev.parent,
			readl(ni + 0x3844) + slot * RTL_FE_ACTION_BYTES, RTL_FE_ACTION_BYTES, DMA_TO_DEVICE);
		dma_wmb();
		rule_slot[id] = slot; rule_count++;
		ret = rtl_fe_main_age(&io, slot, 1);
		if (ret) return ret;
		ret = rtl_fe_cache_command(&io, slot, rule_crc16[id], 2);
		if (ret) return ret;
		pr_info("TYFZA1 wifi rule cache_status=%08x memory=%08x/%08x\n",
			readl(ni + 0x38b4), readl(ni + 0x383c), readl(ni + 0x3838));
		pr_info("TYFZA1 wifi rule input_id=%u output_id=%u slot=%u CRC=%08x/%04x\n",
			id + 1, id + 5, slot, rule_crc[id], rule_crc16[id]);
	}
	/* Retain the current wired tuple0; add the Wi-Fi identity tuple1. */
	writel(62 | BIT(8), ni + 0x3708);
	writel((saved_profile[0] & ~15U) | 2, ni + 0x3700);
	return 0;
}

static void remove_rules(void)
{
	unsigned int id, age;
	for (id = 0; id < rule_count; id++) {
		rtl_fe_main_age(&io, rule_slot[id], 0);
		rtl_fe_cache_command(&io, rule_slot[id], rule_crc16[id], 1);
		WRITE_ONCE(((u32 *)tables)[rule_slot[id]], 0);
		dma_sync_single_for_device(netdev->dev.parent, readl(ni + 0x383c) + rule_slot[id] * 4, 4, DMA_TO_DEVICE);
		dma_wmb();
		rtl_fe_main_age_read(&io, rule_slot[id], &age);
		pr_info("TYFZA1 wifi rule deleted slot=%u age=%u hash=%08x\n",
			rule_slot[id], age, READ_ONCE(((u32 *)tables)[rule_slot[id]]));
	}
	rule_count = 0;
	if (rules_configured) {
		writel(saved_profile[0], ni + 0x3700); writel(saved_profile[1], ni + 0x3708);
		writel(saved_profile[2], ni + 0x3704);
		writel(saved_hash_ini, ni + 0x3834);
		rtl_fe_mask_write(&io, 62, saved_mask62);
		rules_configured = false;
	}
}

static void check_keys(unsigned int header[32])
{
	unsigned int saved_mask[8], mask[8], words[23], actual[23], id, i, hw32, sw32, failures = 0;
	unsigned short hw16, sw16;
	struct rtl_fe_ipv4_key key = {
		.source = field(header, 361, 32), .destination = field(header, 233, 32),
		.source_port = field(header, 90, 16), .destination_port = field(header, 74, 16),
		.protocol = field(header, 492, 8), .ingress = field(header, 837, 6),
		.llid = field(header, 802, 8),
	};
	int ret;

	if (key.source != 0xc6120012 || key.destination != 0xc6120013) return;
	ret = rtl_fe_mask_read(&io, 62, saved_mask);
	if (ret) return;
	ret = rtl_fe_mask_read(&io, 63, mask);
	if (ret) return;
	pr_info("TYFZA1 wifi key mask63=%08x/%08x/%08x/%08x/%08x/%08x/%08x/%08x\n",
		mask[0], mask[1], mask[2], mask[3], mask[4], mask[5], mask[6], mask[7]);
	rtl_fe_wifi_mask(mask);
	ret = rtl_fe_mask_write(&io, 62, mask);
	if (ret) goto restore;
	ret = rtl_fe_mask_read(&io, 62, actual);
	if (ret || memcmp(actual, mask, sizeof(mask))) {
		pr_err("TYFZA1 wifi key mask62 readback failed error=%d\n", ret);
		goto restore;
	}
	rtl_fe_header_ipv4(header, &key);
	for (id = 1; id <= RTL_WIFI_ID_MAX; id++) {
		rtl_fe_ipv4_key(words, &key);
		rtl_fe_key_set(words, RTL_FE_KEY_ORIG_LSPID, 0);
		rtl_fe_key_set(words, RTL_FE_KEY_TABLE_ID, 0);
		rtl_fe_key_set(words, RTL_FE_KEY_MDATA_BYTE, id);
		rtl_fe_key_crc(words, &sw32, &sw16);
		rtl_fe_bits_set(header, 734, 8, id);
		ret = rtl_fe_crc_hardware(&io, header, 62, &hw32, &hw16);
		if (ret) break;
		for (i = 0; i < 23; i++) actual[i] = readl(ni + 0x38dc);
		if (hw32 != sw32 || hw16 != sw16 || memcmp(actual, words, sizeof(words))) failures++;
		if (id <= 2)
			pr_info("TYFZA1 wifi key id=%u source=%u CRC=%08x/%04x native=%08x/%04x key_equal=%u\n",
				id, key.ingress, hw32, hw16, sw32, sw16, !memcmp(actual, words, sizeof(words)));
		if (id == 1)
			for (i = 0; i < 23; i++)
				if (actual[i] != words[i]) pr_info("TYFZA1 wifi key word%u actual=%08x expected=%08x\n", i, actual[i], words[i]);
	}
	pr_info("TYFZA1 wifi key cases=%u failures=%u error=%d\n", id - 1, failures, ret);
restore:
	ret = rtl_fe_mask_write(&io, 62, saved_mask);
	if (!ret) ret = rtl_fe_mask_read(&io, 62, actual);
	pr_info("TYFZA1 wifi key mask62_restored=%u error=%d\n",
		!ret && !memcmp(actual, saved_mask, sizeof(saved_mask)), ret);
}

struct probe_snapshot {
 u32 command, size;
 unsigned int queue, entry;
 u8 bytes[128];
};
static struct probe_snapshot snapshots[16];

static void capture(const char *phase)
{
 unsigned int q, entry, found = 0, base = readl(ni + 0x7200), i, header[32];
 unsigned int stages[2][32], groups[] = {1, 8}, g, w;

 /* Capture RAM before serial logging gives NAPI time to recycle its BIDs. */
 for (q = 0; q < 8; q++) {
  unsigned int end = readl(ni + 0x7000 + q * 4) & 1023;
  dma_sync_single_for_cpu(netdev->dev.parent, base + q * 1024, 1024, DMA_FROM_DEVICE);
  for (entry = before[q]; entry != end && found < ARRAY_SIZE(snapshots); entry = (entry + 8) & 1023) {
   void *epp = qm + q * 1024 + entry;
   struct probe_snapshot *snap = &snapshots[found];
   u32 command = get_unaligned_le32(epp), size = get_unaligned_le32(epp + 4);
   u32 offset = (command & 0xffffff80U) - base;
   if (offset < 8192 || offset > QM_BYTES - sizeof(snap->bytes)) continue;
   dma_sync_single_for_cpu(netdev->dev.parent, base + offset, sizeof(snap->bytes), DMA_FROM_DEVICE);
   dma_rmb();
   memcpy(snap->bytes, qm + offset, sizeof(snap->bytes));
   if (get_unaligned_be32(snap->bytes + 90) != MAGIC ||
       get_unaligned_be32(snap->bytes + 74) != 0xc6120012) continue;
   if (command != get_unaligned_le32(epp) || size != get_unaligned_le32(epp + 4) ||
       memcmp(snap->bytes, qm + offset, sizeof(snap->bytes))) continue;
   snap->command = command; snap->size = size; snap->queue = q; snap->entry = entry;
   found++;
  }
 }
 for (i = 0; i < 32; i++) { writel(i, ni + 0x30b8); header[i] = readl(ni + 0x30bc); }
 if (rules)
  for (g = 0; g < 2; g++)
   for (w = 0; w < 32; w++) { writel((groups[g] << 5) | w, ni + 0x30b8); stages[g][w] = readl(ni + 0x30bc); }
 for (i = 0; i < found; i++) {
  const struct probe_snapshot *snap = &snapshots[i];
  pr_info("TYFZA1 wifi packet phase=%s seq=%u q=%u epp=%08x/%08x hA=%08x/%08x hCPU=%08x/%08x id=%u len=%d frame=%*phN\n",
   phase, get_unaligned_be32(snap->bytes + 94), snap->queue, snap->command, snap->size,
   get_unaligned_be32(snap->bytes + 32), get_unaligned_be32(snap->bytes + 36),
   get_unaligned_be32(snap->bytes + 40), get_unaligned_be32(snap->bytes + 44),
   rtl_wifi_epp_id(snap->size), rtl_wifi_epp_length(snap->size, 2000), 60, snap->bytes + 48);
 }
 pr_info("TYFZA1 wifi lookup phase=%s captured=%u VP9 read=%u write=%u stage0=%08x crc_en=%u\n",
  phase, found, readl(vp + 0x1c), readl(vp + 0x18), readl(ni + 0x3400), !!(readl(ni + 0x6108) & BIT(25)));
 if (!rules) check_keys(header);
 else {
  unsigned int age;
  for (i = 0; i < rule_count; i++) {
   rtl_fe_main_age_read(&io, rule_slot[i], &age);
   pr_info("TYFZA1 wifi rule active slot=%u age=%u hash=%08x\n", rule_slot[i], age,
    READ_ONCE(((u32 *)tables)[rule_slot[i]]));
  }
  for (g = 0; g < 2; g++)
   pr_info("TYFZA1 wifi stage phase=%s group=%u source=%u metadata=%08x/%08x hash_index=%u fwd_valid=%u\n",
    phase, groups[g], field(stages[g], 837, 6), field(stages[g], 766, 32), field(stages[g], 734, 32),
    field(stages[g], 847, 21), field(stages[g], 888, 9));
 }
}

static void release(void)
{
	remove_rules();
	if (configured) {
		u32 status;
		writel(0, vp);
		readl(vp);
		/* VP9 has reached its write pointer; disable it before releasing DMA.
		 * Never reset the shared DMA engine used by LAN/PON on VP8. */
		usleep_range(1000, 2000);
		writel(saved[0], dma + 0x14);
		writel(saved[1], vp + 0x08);
		writel(saved[2], vp + 0x0c);
		writel(BIT(31) | BIT(30), vp + 0x04);
		if (readl_poll_timeout(vp + 0x04, status, !(status & BIT(31)), 1, 100000))
			pr_err("TYFZA1 wifi lookup VP9 base restore failed\n");
		writel(saved[3], vp + 0x64);
		writel(saved[4], vp + 0x78);
	}
	if (ippb_changed) {
		int ret;
		writel(saved_ippb, ni + 0x11e0);
		ret = reg_access(NULL, 0x11dc, BIT(31) | BIT(30) | RTL_WIFI_SOURCE);
		if (!ret) ret = reg_access(NULL, 0x11dc, BIT(31) | RTL_WIFI_SOURCE);
		pr_info("TYFZA1 wifi lookup IPPB18_restored=%u VP9_disabled=%u error=%d\n",
			!ret && readl(ni + 0x11e0) == saved_ippb, !readl(vp), ret);
		writel(saved_debug, ni + 0x30b8);
	}
	if (qm) memunmap(qm);
	if (tables) memunmap(tables);
	if (memory) dma_free_coherent(netdev->dev.parent, BYTES, memory, bus);
	if (dma) iounmap(dma);
	if (ni) iounmap(ni);
	if (netdev) dev_put(netdev);
}

static int __init probe_init(void)
{
	u32 status;
	unsigned int i;
	int ret = -ENODEV;

	netdev = dev_get_by_name(&init_net, "lan1");
	if (!netdev) return ret;
	ni = ioremap(0x4f4300000ULL, 0x10000);
	dma = ioremap(0x4f7001000ULL, 0x1000);
	if (!ni || !dma) { ret = -ENOMEM; goto fail; }
	saved_debug = readl(ni + 0x30b8);
	vp = dma + 0x100 + VP * 0xa0;
	pr_info("TYFZA1 wifi lookup VP9 ctrl=%08x rp=%u wp=%u base=%08x/%08x ss=%08x\n",
		readl(vp), readl(vp + 0x1c), readl(vp + 0x18),
		readl(vp + 0x08), readl(vp + 0x0c), readl(dma + 0x900));
	if (readl(vp) || readl(vp + 0x1c) != readl(vp + 0x18) ||
	    !(readl(dma + 0x900) & BIT(VP)) || !readl(ni + 0x383c)) {
		ret = -EBUSY; goto fail;
	}
	first = readl(vp + 0x1c) & (COUNT - 1);
	for (i = 16; i < 20; i++) {
		writel(BIT(31) | i, ni + 0x11dc);
		ret = readl_poll_timeout(ni + 0x11dc, status, !(status & BIT(31)), 1, 100000);
		if (ret) goto fail;
		pr_info("TYFZA1 wifi lookup IPPB%u=%08x\n", i, readl(ni + 0x11e0));
		if (i == RTL_WIFI_SOURCE) saved_ippb = readl(ni + 0x11e0);
	}
	writel((saved_ippb & ~63U) | RTL_WIFI_SOURCE, ni + 0x11e0);
	ippb_changed = true;
	ret = reg_access(NULL, 0x11dc, BIT(31) | BIT(30) | RTL_WIFI_SOURCE);
	if (ret) goto fail;
	qm = memremap(readl(ni + 0x7200), QM_BYTES, MEMREMAP_WB);
	memory = dma_alloc_coherent(netdev->dev.parent, BYTES, &bus, GFP_KERNEL);
	if (!qm || !memory) { ret = -ENOMEM; goto fail; }
	memset(memory, 0, BYTES);
	for (i = 0; i < 8; i++) {
		unsigned int slot = (first + i) & (COUNT - 1);
		u8 *packet = memory + COUNT * 8 + slot * 128;
		struct iphdr *ip = (void *)(packet + ETH_HLEN);

		memcpy(packet, "\x02\x57\x46\x4e\x45\x02\x02\x57\x46\x4e\x45\x01\x08\x00", ETH_HLEN);
		ether_addr_copy(packet, netdev->dev_addr);
		ip->version = 4; ip->ihl = 5; ip->ttl = 64; ip->protocol = IPPROTO_UDP;
		ip->tot_len = htons(46); ip->id = htons(i);
		ip->saddr = htonl(0xc6120012); ip->daddr = htonl(0xc6120013);
		ip_send_check(ip);
		put_unaligned_be16(48518, packet + 34); put_unaligned_be16(48519, packet + 36);
		put_unaligned_be16(26, packet + 38);
		put_unaligned_be32(MAGIC, packet + 42); put_unaligned_be32(i, packet + 46);
		put_unaligned(csum_tcpudp_magic(ip->saddr, ip->daddr, 26, IPPROTO_UDP,
			csum_partial(packet + 34, 26, 0)), (__sum16 *)(packet + 40));
		put_unaligned_le32(lower_32_bits(bus) + COUNT * 8 + slot * 128, memory + slot * 8);
		put_unaligned_le32(rtl_wifi_lookup_control(1 + (i & 1), 60), memory + slot * 8 + 4);
	}
	if (rules) { ret = install_rules(); if (ret) goto fail; }
	saved[0] = readl(dma + 0x14); saved[1] = readl(vp + 0x08);
	saved[2] = readl(vp + 0x0c); saved[3] = readl(vp + 0x64); saved[4] = readl(vp + 0x78);
	configured = true;
	writel(0, vp + 0x64);
	writel(2, vp + 0x08); writel(lower_32_bits(bus) | 6, vp + 0x0c);
	writel(BIT(31) | BIT(30), vp + 0x04);
	ret = readl_poll_timeout(vp + 0x04, status, !(status & BIT(31)), 1, 100000);
	if (ret) goto fail;
	writel((saved[0] & ~(3U << (VP * 2))) | (1U << (VP * 2)), dma + 0x14);
	writel(BIT(9), vp + 0x78);
	dma_wmb();
	for (i = 0; i < 8; i++) before[i] = readl(ni + 0x7000 + i * 4) & 1023;
	writel(1, vp);
	sent = (first + 8) & (COUNT - 1);
	writel(sent, vp + 0x18); writel(1, vp + 0x68);
	ret = readl_poll_timeout(vp + 0x1c, status, (status & 63) == sent, 10, 100000);
	capture(rules ? "hit" : "miss");
	if (rules) {
		remove_rules();
		for (i = 0; i < 8; i++) {
			unsigned int oldslot = (first + i) & 63, newslot = (sent + i) & 63;
			memcpy(memory + newslot * 8, memory + oldslot * 8, 8);
		}
		for (i = 0; i < 8; i++) before[i] = readl(ni + 0x7000 + i * 4) & 1023;
		dma_wmb(); sent = (sent + 8) & 63;
		writel(sent, vp + 0x18); writel(1, vp + 0x68);
		ret = readl_poll_timeout(vp + 0x1c, status, (status & 63) == sent, 10, 100000);
		if (ret) pr_err("TYFZA1 wifi deleted-rule DMA timeout\n");
		capture("deleted");
	}
	pr_info("TYFZA1 wifi lookup submitted=8 descriptor=%08x/%08x DMA_result=%d\n",
		rtl_wifi_lookup_control(1, 60), rtl_wifi_lookup_control(2, 60), ret);
	/* Keep the diagnostic loaded until explicitly unloaded after inspection. */
	return 0;
fail:
	release();
	return ret;
}
static void __exit probe_exit(void) { release(); }
module_init(probe_init);
module_exit(probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 isolated eight-packet Wi-Fi NI lookup experiment");
