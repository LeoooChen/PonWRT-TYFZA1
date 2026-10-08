// SPDX-License-Identifier: GPL-2.0-or-later
/* Native FE mask transaction test on idle slot63; restore its original value. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include "rtl9617c-fe-table.h"

static void __iomem *base;
static unsigned int reg_read(void *unused, unsigned int reg) { return readl(base + reg); }
static void reg_write(void *unused, unsigned int reg, unsigned int value) { writel(value, base + reg); }
static int reg_access(void *unused, unsigned int reg, unsigned int value)
{
 u32 status;
 writel(value, base + reg);
 return readl_poll_timeout(base + reg, status, !(status & BIT(31)), 1, 100000);
}
static int __init probe_init(void)
{
 const struct rtl_dq_io io = {NULL, reg_read, reg_write, reg_access};
 unsigned int saved[8], expected[8], actual[8];
 int ret, restored;

 base = ioremap(0x4f4300000ULL, 0x10000);
 if (!base)
  return -ENOMEM;
 ret = rtl_fe_mask_read(&io, 63, saved);
 if (ret)
  goto done;
 rtl_fe_ipv4_mask(expected);
 ret = rtl_fe_mask_write(&io, 63, expected);
 if (!ret)
  ret = rtl_fe_mask_read(&io, 63, actual);
 if (!ret && memcmp(actual, expected, sizeof(actual)))
  ret = -EIO;
 restored = rtl_fe_mask_write(&io, 63, saved);
 if (!restored)
  restored = rtl_fe_mask_read(&io, 63, actual);
 if (!restored && memcmp(actual, saved, sizeof(saved)))
  restored = -EIO;
 pr_info("TYFZA1 native FE mask63: lower/upper128 readback=%d original_restored=%d HASH_INI=%08x CACHE_INI=%08x\n",
  ret, restored, readl(base + 0x3834), readl(base + 0x38a0));
 if (!ret)
  ret = restored;
done:
 iounmap(base);
 return ret;
}
static void __exit probe_exit(void) { }
module_init(probe_init);
module_exit(probe_exit);
MODULE_DESCRIPTION("TYFZA1 native FE mask two-bank transaction test");
MODULE_LICENSE("GPL");
