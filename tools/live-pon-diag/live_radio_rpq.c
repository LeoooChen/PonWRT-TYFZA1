// SPDX-License-Identifier: GPL-2.0-only
/* Observe RPQ; optionally test one-report aggregation until module removal. */
#include <linux/io.h>
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/unaligned.h>

static bool single;
module_param(single, bool, 0400);
static unsigned int delay_ms;
module_param(delay_ms, uint, 0400);
static bool recover_idle_ni;
module_param(recover_idle_ni, bool, 0400);
static struct pci_dev *radio_device;
static void __iomem *radio_bar;
static u32 aggregation_saved;

static int __init radio_rpq_init(void)
{
	static const u32 registers[] = {0x1000, 0x1010, 0x10b0, 0x10b4, 0x1180, 0x1184, 0x121c,
		0x1228, 0x30b0, 0x30b4, 0x8900, 0x8904, 0x9408, 0x9410, 0x9414, 0x9434};
	struct pci_dev *pdev = pci_get_domain_bus_and_slot(1, 1, PCI_DEVFN(0, 0));
	void __iomem *bar;
	u32 index, address;
	u8 *descriptor, *buffer;
	unsigned int i;

	if (recover_idle_ni) {
		void __iomem *ni = ioremap(0x4f4300000ULL, 0x10000);
		void __iomem *vp = ioremap(0x4f70016a0ULL, 0x80);
		if (readl(ni + 0x7060) != readl(ni + 0x7160) || readl(vp + 0x18) != readl(vp + 0x1c)) {
			iounmap(vp); iounmap(ni); pci_dev_put(pdev); return -EBUSY;
		}
		writel(0, vp);
		writel(readl(ni + 0x6108) & ~BIT(3), ni + 0x6108);
		writel(0, ni + 0x7260);
		pr_info("native radio: retired idle orphan VP9/EPP24; read/write pointers unchanged\n");
		iounmap(vp); iounmap(ni);
	}
	if (!pdev) return -ENODEV;
	bar = pci_iomap(pdev, 2, 0);
	if (!bar) { pci_dev_put(pdev); return -ENOMEM; }
	radio_device = pdev; radio_bar = bar;
	if (single) {
		aggregation_saved = readl(bar + 0x9414);
		writel((aggregation_saved & ~255U) | 1, bar + 0x9414);
	}
	if (delay_ms) msleep(delay_ms);
	for (i = 0; i < ARRAY_SIZE(registers); i++)
		pr_info("native radio RPQ reg=%04x value=%08x\n", registers[i], readl(bar + registers[i]));
	index = readl(bar + 0x121c);
	address = readl(bar + 0x1228);
	descriptor = phys_to_virt(address);
	address = get_unaligned_le32(descriptor + (index & 255) * 8 + 4);
	buffer = phys_to_virt(address);
	dma_rmb();
	pr_info("native radio RPQ head=%u hardware=%u DMA=%08x info=%08x words=%32phN\n",
		index & 1023, (index >> 16) & 1023, address, get_unaligned_le32(buffer), buffer + 4);
	return 0;
}
static void __exit radio_rpq_exit(void)
{
	if (single) writel(aggregation_saved, radio_bar + 0x9414);
	pci_iounmap(radio_device, radio_bar);
	pci_dev_put(radio_device);
}
module_init(radio_rpq_init);
module_exit(radio_rpq_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 native radio RPQ register observation");
