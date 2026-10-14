/* SPDX-License-Identifier: GPL-2.0-only */
/* Owned PE1 RAM and bus windows shared by the packet bring-up probes. */
#ifndef TYFZA1_PE_PROBE_H
#define TYFZA1_PE_PROBE_H
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/sizes.h>
#include <linux/unaligned.h>

#define PE_PROBE_BYTES SZ_64K
#define PE_PROBE_MAILBOX 0x800
#define PE_PROBE_RESET (BIT(19) | BIT(21))
struct pe_probe {
	struct platform_device *device;
	void __iomem *reset, *atu, *ram;
	void *memory;
	dma_addr_t dma;
	u32 reset_saved, ram_saved, staging[8], entries[4][7];
	bool configured, staging_saved;
};

static int pe_probe_access(struct pe_probe *pe, u32 command)
{
	u32 state;
	writel(command, pe->atu);
	return readl_poll_timeout(pe->atu, state, !(state & (BIT(30) | BIT(31))), 1, 10000);
}

static void pe_probe_reset(struct pe_probe *pe, u32 bits)
{
	writel((readl(pe->reset) & ~PE_PROBE_RESET) | bits, pe->reset);
	readl(pe->reset);
}

static void pe_probe_stop(struct pe_probe *pe)
{
	if (!pe->configured) return;
	pe_probe_reset(pe, PE_PROBE_RESET);
	writel(readl(pe->ram) | BIT(5), pe->ram);
	udelay(1000);
}

static void pe_probe_free(struct pe_probe *pe)
{
	unsigned int index, i;

	pe_probe_stop(pe);
	if (pe->configured) {
		for (index = 0; index < 4; index++) {
			for (i = 0; i < 7; i++)
				writel(pe->entries[index][i], pe->atu + 4 + i * 4);
			pe_probe_access(pe, (pe->staging[0] & 255) | index << 24 | BIT(31));
		}
		writel(pe->ram_saved, pe->ram);
		pe_probe_reset(pe, pe->reset_saved & PE_PROBE_RESET);
	}
	if (pe->staging_saved) {
		for (i = 1; i < 8; i++)
			writel(pe->staging[i], pe->atu + i * 4);
		writel(pe->staging[0] & ~(BIT(30) | BIT(31)), pe->atu);
	}
	if (pe->memory)
		dma_free_coherent(&pe->device->dev, PE_PROBE_BYTES, pe->memory, pe->dma);
	if (pe->device) platform_device_unregister(pe->device);
	if (pe->ram) iounmap(pe->ram);
	if (pe->atu) iounmap(pe->atu);
	if (pe->reset) iounmap(pe->reset);
}

static int pe_probe_alloc(struct pe_probe *pe)
{
	unsigned int i, index;
	int ret;

	pe->reset = ioremap(0xf4320098, 4);
	pe->atu = ioremap(0x90300100, 0x20);
	pe->ram = ioremap(0x90303074, 4);
	if (!pe->reset || !pe->atu || !pe->ram) return -ENOMEM;
	pe->reset_saved = readl(pe->reset);
	pe->ram_saved = readl(pe->ram);
	for (i = 0; i < 8; i++) pe->staging[i] = readl(pe->atu + i * 4);
	pe->staging_saved = true;
	if (!(pe->reset_saved & BIT(21)) || (pe->staging[0] & 14)) return -EBUSY;
	for (index = 0; index < 4; index++) {
		ret = pe_probe_access(pe, (pe->staging[0] & 255) | index << 24 | BIT(30));
		if (ret) return ret;
		for (i = 0; i < 7; i++) pe->entries[index][i] = readl(pe->atu + 4 + i * 4);
	}
	pe->device = platform_device_register_simple("tyfza1-pe-probe", -1, NULL, 0);
	if (IS_ERR(pe->device)) { ret = PTR_ERR(pe->device); pe->device = NULL; return ret; }
	ret = dma_coerce_mask_and_coherent(&pe->device->dev, DMA_BIT_MASK(32));
	if (ret) return ret;
	pe->memory = dma_alloc_coherent(&pe->device->dev, PE_PROBE_BYTES, &pe->dma, GFP_KERNEL);
	if (!pe->memory) return -ENOMEM;
	if (!IS_ALIGNED(pe->dma, PE_PROBE_BYTES)) return -EINVAL;
	memset(pe->memory, 0, PE_PROBE_BYTES);
	return 0;
}

static int pe_probe_start(struct pe_probe *pe)
{
	static const u32 input[] = { 0x1fc0, 0x1000, 0x1100, 0x1200 };
	static const u32 output[] = { 0, 0xf430, 0xa040, 0xb040 };
	unsigned int index, i;
	int ret;

	pe->configured = true;
	pe_probe_stop(pe);
	for (index = 0; index < 4; index++) {
		writel(input[index], pe->atu + 4);
		writel(index ? 0xfff0 : 0xffff, pe->atu + 8);
		writel(index ? output[index] : pe->dma >> 16, pe->atu + 12);
		for (i = 3; i < 7; i++) writel(pe->entries[0][i], pe->atu + 4 + i * 4);
		ret = pe_probe_access(pe, (pe->staging[0] & 255) | 15 | index << 24 | BIT(31));
		if (ret) return ret;
	}
	writel(readl(pe->ram) & ~BIT(5), pe->ram);
	pe_probe_reset(pe, BIT(21));
	udelay(1000);
	pe_probe_reset(pe, 0);
	return 0;
}
#endif
