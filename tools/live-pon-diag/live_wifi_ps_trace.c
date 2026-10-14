// SPDX-License-Identifier: GPL-2.0-only
/* Read PE power-save ownership and NI admission at the same millisecond. */
#include <linux/module.h>
#include <linux/debugfs.h>
#include <linux/hrtimer.h>
#include <linux/io.h>
#include <linux/dma-mapping.h>
#include <linux/platform_device.h>
#include <linux/seq_file.h>

static unsigned long pe_address;
static unsigned int macid;
module_param(pe_address, ulong, 0400);
module_param(macid, uint, 0400);

struct sample { u64 us; u32 asleep, pending, queued, epp, eq, drops; };
static struct sample rows[4096], previous;
static unsigned int count;
static u64 started, last;
static struct hrtimer timer;
static struct device *owner;
static void __iomem *ni;
static u8 *pe;
static struct dentry *directory;

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
	s.epp = ((readl(ni + 0x7060) - readl(ni + 0x7160)) & 4095) / 8;
	s.eq = readl(ni + 0x6328 + 6 * 4);
	s.drops = readl(ni + 0x971c);
	if (count < ARRAY_SIZE(rows) && (!count || s.asleep != previous.asleep ||
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
	seq_puts(file, "us asleep PE_owned peer_queued EPP_queued EQ_ready L3TE_drops\n");
	for (unsigned int i = 0; i < total; i++) {
		const struct sample *s = &rows[i];
		seq_printf(file, "%llu %u %u %u %u %08x %u\n", s->us, s->asleep,
			s->pending, s->queued, s->epp, s->eq, s->drops);
	}
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(trace);

static int __init trace_init(void)
{
	if (!pe_address || macid >= 128) return -EINVAL;
	owner = bus_find_device_by_name(&platform_bus_type, NULL, "tyfza1-pe-probe");
	if (!owner) return -ENODEV;
	ni = ioremap(0x4f4300000ULL, 0x10000);
	if (!ni) { put_device(owner); return -ENOMEM; }
	pe = phys_to_virt(pe_address);
	directory = debugfs_create_dir("tyf-wifi-ps-trace", NULL);
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
	iounmap(ni);
	put_device(owner);
}
module_init(trace_init);
module_exit(trace_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Finite TYFZA1 PE power-save and NI drop observation");
