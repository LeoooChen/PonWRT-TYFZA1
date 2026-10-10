// SPDX-License-Identifier: GPL-2.0-or-later
/* Reversible RAM application of the four-LAN FE fix without restarting NI/PON. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include "rtl9617c-fe-pipeline.h"

static void __iomem *ni;
static unsigned int saved_ctrl[4], saved_cls[7], saved_stage0, saved_demux;
static unsigned int read_reg(void *ctx, unsigned int reg) { return readl(ni + reg); }
static void write_reg(void *ctx, unsigned int reg, unsigned int value) { writel(value, ni + reg); }
static int access_reg(void *ctx, unsigned int reg, unsigned int value)
{
    u32 status;
    writel(value, ni + reg);
    return readl_poll_timeout(ni + reg, status, !(status & BIT(31)), 1, 100000);
}
static const struct rtl_dq_io io = { NULL, read_reg, write_reg, access_reg };

static void state(const char *phase)
{
    unsigned int p, cls[7];
    if (rtl_fe_classifier_read(&io, 1024, cls)) return;
    for (p = 0; p < 4; p++)
        pr_info("TYFZA1 %s LAN%u RXctrl=%08x NI TX=%08x scheduler=%08x\n",
            phase, p, readl(ni + 0xa5f8 + 0x90 * p),
            readl(ni + 0xa5d4 + 0x90 * p), readl(ni + 0x2304 + p * 12));
    pr_info("TYFZA1 %s source-control=%08x LAN-classifier=%08x post-PE-demux=%08x\n",
        phase, readl(ni + 0x3400), cls[6], readl(ni + 0xa1e0));
}
static void restore(void)
{
    unsigned int p;
    /* Remove all four physical entries before restoring the old pipeline. */
    for (p = 0; p < 4; p++) writel(saved_ctrl[p], ni + 0xa5f8 + p * 0x90);
    writel(saved_stage0, ni + 0x3400);
    writel(saved_demux, ni + 0xa1e0);
    if (rtl_fe_classifier_write(&io, 1024, saved_cls))
        pr_err("TYFZA1 four-LAN classifier restore failed\n");
}
static int __init fix_init(void)
{
    unsigned int cls[7], p;
    int ret;
    ni = ioremap(0x4f4300000ULL, 0x10000);
    if (!ni) return -ENOMEM;
    if (!readl(ni + 0x383c) || (readl(ni + 0x3834) & 0xff) != 0x9f ||
        (readl(ni + 0x38c4) & 0xe0000000) != 0xa0000000) {
        iounmap(ni); return -ENODEV;
    }
    ret = rtl_fe_classifier_read(&io, 1024, saved_cls);
    if (ret) { iounmap(ni); return ret; }
    for (p = 0; p < 4; p++) saved_ctrl[p] = readl(ni + 0xa5f8 + p * 0x90);
    saved_stage0 = readl(ni + 0x3400); saved_demux = readl(ni + 0xa1e0);
    state("before");
    memcpy(cls, saved_cls, sizeof(cls));
    rtl_fe_bits_set(cls, 208, 1, 0);
    ret = rtl_fe_classifier_write(&io, 1024, cls);
    if (ret) { restore(); iounmap(ni); return ret; }
    writel(saved_stage0 | BIT(19), ni + 0x3400);
    writel(saved_demux | 0xff, ni + 0xa1e0);
    for (p = 0; p < 4; p++)
        writel((saved_ctrl[p] & ~(63U << 16)) | (25U << 16), ni + 0xa5f8 + p * 0x90);
    state("fixed");
    pr_info("TYFZA1 four-LAN FE RAM fix applied; original registers retained for unload\n");
    return 0;
}
static void __exit fix_exit(void)
{
    restore(); state("restored"); iounmap(ni);
}
module_init(fix_init);
module_exit(fix_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 reversible RAM application of native four-LAN FE fix");
