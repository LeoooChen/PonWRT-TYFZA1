// SPDX-License-Identifier: GPL-2.0-only
/* Native PE1 reset-vector experiment, using an owned 64KB DMA allocation. */
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/sizes.h>
#include <linux/unaligned.h>

#define PE1_RESET (BIT(19) | BIT(21))
#define PE_BYTES SZ_64K
#define PE_MAILBOX 256

static void __iomem *reset, *atu, *control;
static struct platform_device *device;
static void *memory;
static dma_addr_t dma;
static u32 reset_saved, ram_saved, staging[8], boot[4][7];
static bool configured;
static unsigned int attr;
module_param(attr, uint, 0400);
static bool ddr_high;
module_param(ddr_high, bool, 0400);
static bool big_endian;
module_param(big_endian, bool, 0400);

static int pe_atu_command(u32 command)
{
	u32 state;

	writel(command, atu);
	return readl_poll_timeout(atu, state, !(state & (BIT(30) | BIT(31))), 1, 10000);
}

static void pe_reset_bits(u32 value)
{
	writel((readl(reset) & ~PE1_RESET) | value, reset);
	readl(reset);
}

static void pe_restore(void)
{
	unsigned int i, index;

	if (configured) {
		/* Stop PE1 before restoring its translation and returning RAM. */
		pe_reset_bits(PE1_RESET);
		writel(readl(control) | BIT(5), control);
		udelay(1000);
		for (index = 0; index < ARRAY_SIZE(boot); index++) {
			for (i = 0; i < 7; i++)
				writel(boot[index][i], atu + 4 + i * 4);
			pe_atu_command((staging[0] & 255) | index << 24 | BIT(31));
		}
		for (i = 1; i < 8; i++)
			writel(staging[i], atu + i * 4);
		writel(staging[0] & ~(BIT(30) | BIT(31)), atu);
		writel(ram_saved, control);
		pe_reset_bits(reset_saved & PE1_RESET);
	}
	if (memory)
		dma_free_coherent(&device->dev, PE_BYTES, memory, dma);
	if (device)
		platform_device_unregister(device);
	if (control) iounmap(control);
	if (atu) iounmap(atu);
	if (reset) iounmap(reset);
}

static int __init live_pe_heartbeat_init(void)
{
	u32 program[] = {
		0x3c080000, /* lui t0, uncached mailbox address high16 */
		0x35080000, /* ori t0, t0, mailbox address low16 */
		0x3c095045, /* lui t1, 'PE' */
		0x35293131, /* ori t1, t1, '11' */
		0xad090000, /* sw t1, 0(t0) */
		0x24090001, 0xad090014, /* stage1: NI */
		0x3c0bb000, 0x356b3834, 0x8d6c0000, 0xad0c0008,
		0x24090002, 0xad090014, /* stage2: radio0 */
		0x3c0bb100, 0x356b9b00, 0x8d6c0000, 0xad0c000c,
		0x24090003, 0xad090014, /* stage3: radio1 */
		0x3c0bb200, 0x356b9b00, 0x8d6c0000, 0xad0c0010,
		0x24090004, 0xad090014, /* all bus reads completed */
		0x240a0000, /* addiu t2, zero, 0 */
		0x254a0001, /* addiu t2, t2, 1 */
		0xad0a0004, /* sw t2, 4(t0) */
		0x1000fffd, /* branch back to increment */
		0x00000000,
	};
	u32 exception[] = {
		0x3c080000, 0x35080000,
		0x40096800, 0xad090018, /* Cause */
		0x40097000, 0xad09001c, /* EPC */
		0x40094000, 0xad090020, /* BadVAddr */
		0x40096000, 0xad090024, /* Status */
		0x3c095045, 0x35294558, 0xad090028,
		0x1000ffff, 0x00000000,
	};
	/* The stock DT and MIPS firmware use NI's low physical alias. */
	static const phys_addr_t bus_physical[] = { 0xf4300000, 0xa0400000, 0xb0400000 };
	static const unsigned int bus_offsets[] = { 0x3834, 0x9b00, 0x9b00 };
	u32 address, first, second, expected[3];
	unsigned int i, index;
	int ret;

	reset = ioremap(0xf4320098, 4);
	atu = ioremap(0x90300100, 0x20);
	control = ioremap(0x90303074, 4);
	if (!reset || !atu || !control) { ret = -ENOMEM; goto release; }
	reset_saved = readl(reset);
	ram_saved = readl(control);
	/* The observed boot state holds this CPU in reset. */
	if (!(reset_saved & BIT(21))) { ret = -EBUSY; goto release; }
	for (i = 0; i < 8; i++)
		staging[i] = readl(atu + i * 4);
	for (index = 0; index < ARRAY_SIZE(boot); index++) {
		ret = pe_atu_command((staging[0] & 255) | index << 24 | BIT(30));
		if (ret) goto release;
		for (i = 0; i < 7; i++)
			boot[index][i] = readl(atu + 4 + i * 4);
	}
	for (index = 0; index < ARRAY_SIZE(expected); index++) {
		void __iomem *register_address = ioremap(bus_physical[index] + bus_offsets[index], 4);
		if (!register_address) { ret = -ENOMEM; goto release; }
		expected[index] = readl(register_address);
		iounmap(register_address);
	}
	device = platform_device_register_simple("tyfza1-pe-heartbeat", -1, NULL, 0);
	if (IS_ERR(device)) { ret = PTR_ERR(device); device = NULL; goto release; }
	ret = dma_coerce_mask_and_coherent(&device->dev, DMA_BIT_MASK(32));
	if (ret) goto release;
	memory = dma_alloc_coherent(&device->dev, PE_BYTES, &dma, GFP_KERNEL);
	if (!memory) { ret = -ENOMEM; goto release; }
	if (!IS_ALIGNED(dma, PE_BYTES)) { ret = -EINVAL; goto release; }
	memset(memory, 0, PE_BYTES);
	address = 0xbfc00000U + PE_MAILBOX;
	program[0] |= (address | 0xa0000000U) >> 16;
	program[1] |= address & 0xffff;
	exception[0] |= (address | 0xa0000000U) >> 16;
	exception[1] |= address & 0xffff;
	for (i = 0; i < ARRAY_SIZE(program); i++)
		if (big_endian) put_unaligned_be32(program[i], memory + i * 4);
		else put_unaligned_le32(program[i], memory + i * 4);
	for (i = 0; i < ARRAY_SIZE(exception); i++)
		if (big_endian) put_unaligned_be32(exception[i], memory + 0x380 + i * 4);
		else put_unaligned_le32(exception[i], memory + 0x380 + i * 4);
	dma_wmb();
	configured = true;
	pe_reset_bits(PE1_RESET);
	writel(readl(control) | BIT(5), control);
	{
		void __iomem *bist = ioremap(0xf4320004, 4);
		u32 before = readl(bist);
		writel(before & ~BIT(18), bist);
		pr_info("native PE stock BIST before=%08x after=%08x\n", before, readl(bist));
		iounmap(bist);
	}
	/* The stock PE1 boot entry compares physical1fc0xxxx. Redirect only
	 * that entry to the newly allocated uncached MIPS reset program. */
	writel(0x1fc0, atu + 4);
	writel(0xffff, atu + 8);
	writel(((u32)dma >> 16) | (ddr_high ? 0x8000 : 0), atu + 12);
	for (i = 3; i < 7; i++)
		writel(boot[0][i], atu + 4 + i * 4);
	if (attr) writel(attr, atu + 16);
	ret = pe_atu_command((staging[0] & 255) | 1 | BIT(31));
	if (ret) goto release;
	for (index = 0; index < ARRAY_SIZE(bus_physical); index++) {
		writel(0x1000 + index * 0x100, atu + 4);
		writel(0xfff0, atu + 8);
		writel(bus_physical[index] >> 16, atu + 12);
		for (i = 3; i < 7; i++)
			writel(boot[0][i], atu + 4 + i * 4);
		if (attr) writel(attr, atu + 16);
		ret = pe_atu_command((staging[0] & 255) | 15 | (index + 1) << 24 | BIT(31));
		if (ret) goto release;
	}
	writel(readl(control) & ~BIT(5), control);
	pe_reset_bits(BIT(21));
	udelay(1000);
	pe_reset_bits(0);
	msleep(20);
	dma_rmb();
	first = get_unaligned_le32(memory + PE_MAILBOX + 4);
	msleep(20);
	dma_rmb();
	second = get_unaligned_le32(memory + PE_MAILBOX + 4);
	pr_info("native PE heartbeat dma=%pad magic=%08x first=%u second=%u reset=%08x\n",
		&dma, get_unaligned_le32(memory + PE_MAILBOX), first, second, readl(reset));
	ret = get_unaligned_le32(memory + PE_MAILBOX) == 0x50453131 && second != first ? 0 : -EIO;
	pr_info("native PE heartbeat bus_stage=%u\n", get_unaligned_le32(memory + PE_MAILBOX + 20));
	{
		void __iomem *debug = ioremap(0x90303050, 8);
		pr_info("native PE bus debug control=%08x status=%08x attr=%08x\n",
			readl(debug), readl(debug + 4), attr);
		iounmap(debug);
	}
	pr_info("native PE heartbeat exception=%08x Cause=%08x EPC=%08x BadVAddr=%08x Status=%08x\n",
		get_unaligned_le32(memory + PE_MAILBOX + 40), get_unaligned_le32(memory + PE_MAILBOX + 24),
		get_unaligned_le32(memory + PE_MAILBOX + 28), get_unaligned_le32(memory + PE_MAILBOX + 32),
		get_unaligned_le32(memory + PE_MAILBOX + 36));
	for (index = 0; index < ARRAY_SIZE(expected); index++) {
		u32 actual = get_unaligned_le32(memory + PE_MAILBOX + 8 + index * 4);
		pr_info("native PE heartbeat bus=%u ARM=%08x PE1=%08x\n", index, expected[index], actual);
		if (actual != expected[index]) ret = -EIO;
	}
	pe_restore();
	return ret;
release:
	pe_restore();
	return ret;
}

static void __exit live_pe_heartbeat_exit(void) {}
module_init(live_pe_heartbeat_init);
module_exit(live_pe_heartbeat_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 owned RAM PE1 heartbeat experiment");
