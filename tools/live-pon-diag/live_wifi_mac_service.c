// SPDX-License-Identifier: GPL-2.0-only
/* Correlate NI tail drops with the original MAC HESU counter and BB TX state.
 * mac_get_tx_cnt selects CAE0[3:0]; halbb_chk_tx_idle_8852c selects BB20f0=30002.
 * Original get_macid_pause reads 9EB0/C2C0 bitmaps for MACID0..31.
 */
#include <linux/module.h>
#include <linux/debugfs.h>
#include <linux/hrtimer.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/dma-mapping.h>
#include <linux/platform_device.h>
#include <linux/pci.h>
#include <linux/seq_file.h>

static unsigned long pe_address;
static unsigned int macid;
module_param(pe_address, ulong, 0400);
module_param(macid, uint, 0400);

struct sample {
	u64 us;
	u32 asleep, pending, queued, epp, drops;
	u32 tx_index, rpq_index, reports, tx_state, he_count;
	u32 mac_pause, mac_sleep, peer_rate;
};
static struct sample rows[16384], previous;
static unsigned int count;
static u64 started, last;
static struct hrtimer timer;
static struct device *owner;
static void __iomem *ni, *bar;
static struct pci_dev *radio;
static u8 *pe;
static struct dentry *directory;
static u32 saved_counter, saved_select, saved_enable, ring_mask;

static u32 shared(unsigned int offset)
{
	dma_sync_single_for_cpu(owner, pe_address + offset, 4, DMA_FROM_DEVICE);
	return READ_ONCE(*(u32 *)(pe + offset));
}
static enum hrtimer_restart sample_tick(struct hrtimer *timer)
{
	struct sample s = {.us = (ktime_get_ns() - started) / 1000};
	s.asleep = shared(0xe800 + macid * 16);
	s.pending = shared(0x2000 + 30 * 4);
	s.queued = shared(0x11800 + macid * 4);
	s.epp = ((readl(ni + 0x7060) - readl(ni + 0x7160)) & ring_mask) / 8;
	s.drops = readl(ni + 0x971c);
	s.tx_index = readl(bar + 0x1058);
	s.rpq_index = readl(bar + 0x121c);
	s.reports = shared(0x2000 + 75 * 4);
	s.tx_state = readl(bar + 0x11730) & 63;
	s.he_count = readl(bar + 0xcae0) >> 16;
	s.mac_pause = (readl(bar + 0x9eb0 + (macid / 32) * 4) >> (macid & 31)) & 1;
	s.mac_sleep = (readl(bar + 0xc2c0 + (macid / 32) * 4) >> (macid & 31)) & 1;
	s.peer_rate = shared(0xd000 + macid * 32 + 12);
	if (count < ARRAY_SIZE(rows) && (!count || s.asleep != previous.asleep ||
	    s.mac_pause != previous.mac_pause || s.mac_sleep != previous.mac_sleep ||
	    s.drops != previous.drops || s.us - last >= 10000)) {
		rows[count] = s;
		smp_store_release(&count, count + 1);
		last = s.us;
	}
	previous = s;
	hrtimer_forward_now(timer, ns_to_ktime(NSEC_PER_MSEC));
	return HRTIMER_RESTART;
}
static int trace_show(struct seq_file *file, void *unused)
{
	unsigned int total = smp_load_acquire(&count);
	seq_printf(file, "ring_bytes=%u HESU counter is16bit; PHY_state0 means TX idle\n", ring_mask + 1);
	seq_puts(file, "us asleep PE_owned peer_queued EPP_queued L3TE_drops TX0 RPQ reports PHY_state HE_TX_count MAC_pause MAC_sleep peer_rate\n");
	for (unsigned int i = 0; i < total; i++) {
		const struct sample *s = &rows[i];
		seq_printf(file, "%llu %u %u %u %u %u %08x %08x %u %u %u %u %u %08x\n",
			s->us, s->asleep, s->pending, s->queued, s->epp, s->drops,
			s->tx_index, s->rpq_index, s->reports, s->tx_state, s->he_count,
			s->mac_pause, s->mac_sleep, s->peer_rate);
	}
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(trace);
static void restore(void)
{
	writel(saved_counter, bar + 0xcae0);
	writel(saved_select, bar + 0x120f0);
	writel(saved_enable, bar + 0x120f8);
}
static int __init trace_init(void)
{
	u32 status;
	int ret;
	owner = bus_find_device_by_name(&platform_bus_type, NULL, "tyfza1-pe-probe");
	if (!owner) return -ENODEV;
	ni = ioremap(0x4f4300000ULL, 0x10000);
	if (!ni) { put_device(owner); return -ENOMEM; }
	radio = pci_get_domain_bus_and_slot(0, 1, PCI_DEVFN(0, 0));
	bar = radio ? pci_iomap(radio, 2, 0) : NULL;
	if (!bar) { pci_dev_put(radio); iounmap(ni); put_device(owner); return -ENODEV; }
	pe = phys_to_virt(pe_address);
	ring_mask = (((readl(ni + 0x6598) >> 8) & 0x1fffff) * 16) - 1;
	saved_counter = readl(bar + 0xcae0);
	saved_select = readl(bar + 0x120f0);
	saved_enable = readl(bar + 0x120f8);
	writel((saved_counter & ~0xf0fU) | 0x1707, bar + 0xcae0);
	ret = readl_poll_timeout(bar + 0xcae0, status, !(status & BIT(12)), 5, 5000);
	if (ret) { restore(); pci_iounmap(radio, bar); pci_dev_put(radio); iounmap(ni); put_device(owner); return ret; }
	writel(saved_enable | BIT(31), bar + 0x120f8);
	writel(0x00030002, bar + 0x120f0);
	directory = debugfs_create_dir("tyf-wifi-mac-service", NULL);
	debugfs_create_file("samples", 0400, directory, NULL, &trace_fops);
	started = ktime_get_ns();
	hrtimer_setup(&timer, sample_tick, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	hrtimer_start(&timer, ns_to_ktime(NSEC_PER_MSEC), HRTIMER_MODE_REL);
	return 0;
}
static void __exit trace_exit(void)
{
	hrtimer_cancel(&timer);
	debugfs_remove_recursive(directory);
	restore();
	pci_iounmap(radio, bar); pci_dev_put(radio);
	iounmap(ni); put_device(owner);
}
module_init(trace_init);
module_exit(trace_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Finite TYFZA1 MAC service and NI tail-drop observation");
