// SPDX-License-Identifier: GPL-2.0-only
/* Read the two PE controls and ATU staging registers without changing them. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>

static bool tables;
module_param(tables, bool, 0400);

static int pe_atu_table(void __iomem *atu, unsigned int cpu)
{
	u32 saved[8], state;
	unsigned int index, offset;
	int ret = 0;

	for (offset = 0; offset < 8; offset++)
		saved[offset] = readl(atu + offset * 4);
	for (index = 0; index < 8; index++) {
		writel((saved[0] & 255) | index << 24 | BIT(30), atu);
		ret = readl_poll_timeout(atu, state, !(state & BIT(30)), 1, 10000);
		if (ret) break;
		pr_info("native PE state CPU%u ATU_entry=%u enabled=%u input=%04x mask=%04x output=%05x attr=%08x read_user=%08x write_user=%08x\n",
			cpu, index, !!(state & BIT(index)), readl(atu + 4), readl(atu + 8),
			readl(atu + 12), readl(atu + 16), readl(atu + 20), readl(atu + 24));
	}
	for (offset = 1; offset < 8; offset++)
		writel(saved[offset], atu + offset * 4);
	writel(saved[0] & ~(BIT(30) | BIT(31)), atu);
	return ret;
}

static int __init live_pe_state_init(void)
{
	void __iomem *reset, *atu, *control;
	unsigned int cpu, offset;

	reset = ioremap(0xf4320098, 0x1b0);
	atu = ioremap(0x90300000, 0x120);
	control = ioremap(0x90303000, 0x80);
	if (!reset || !atu || !control) {
		if (control) iounmap(control);
		if (atu) iounmap(atu);
		if (reset) iounmap(reset);
		return -ENOMEM;
	}
	pr_info("native PE state GLOBAL_BLOCK_RESET=%08x\n", readl(reset));
	pr_info("native PE state GLOBAL_CONFIG=%08x FABRIC_RESET=%08x PE_DSS=%08x\n",
		readl(reset + 0x28), readl(reset + 0xc), readl(reset + 0x94));
	for (offset = 0x200; offset <= 0x244; offset += 4)
		pr_info("native PE state GLOBAL offset=%03x value=%08x\n",
			offset, readl(reset + offset - 0x98));
	for (offset = 0; offset <= 0x78; offset += 4)
		pr_info("native PE state control offset=%02x value=%08x\n", offset, readl(control + offset));
	for (cpu = 0; cpu < 2; cpu++) {
		pr_info("native PE state CPU%u RAM_control=%08x DSS=%08x\n", cpu,
			readl(control + 0x6c + cpu * 8), readl(control + 0x40 + cpu * 8));
		for (offset = 0; offset < 0x20; offset += 4)
			pr_info("native PE state CPU%u ATU_staging offset=%02x value=%08x\n",
				cpu, offset, readl(atu + cpu * 0x100 + offset));
		if (tables)
			pr_info("native PE state CPU%u ATU_read_result=%d\n", cpu,
				pe_atu_table(atu + cpu * 0x100, cpu));
	}
	iounmap(control);
	iounmap(atu);
	iounmap(reset);
	return 0;
}

static void __exit live_pe_state_exit(void) {}
module_init(live_pe_state_init);
module_exit(live_pe_state_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 PE control register reader");
