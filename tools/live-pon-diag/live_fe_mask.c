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
 return readl_poll_timeout(base + reg, status, !(status & (reg == 0x38e0 ? 1 : BIT(31))), 1, 100000);
}
static int check_crc(const struct rtl_dq_io *io)
{
 unsigned int vector, word, header[RTL_FE_HEADER_WORDS], tuple[RTL_FE_KEY_WORDS];
 unsigned int software32, hardware32, key_words[RTL_FE_KEY_WORDS];
 unsigned short software16, hardware16;
 int ret;

 for (vector = 0; vector < 128; vector++) {
  struct rtl_fe_ipv4_key key = {
   .source = 0x0a010200 + vector, .destination = 0xc0000201 + vector,
   .source_port = 53000 + vector, .destination_port = 443 + vector,
   .protocol = vector & 1 ? 6 : 17, .ingress = vector & 1 ? 7 : 0,
   .llid = 0, .profile = 0, .mask = 63,
  };
  rtl_fe_ipv4_key(key_words, &key);
  rtl_fe_key_set(key_words, RTL_FE_KEY_HKEY_ID, 0);
  rtl_fe_key_set(key_words, RTL_FE_KEY_TABLE_ID, 0);
  rtl_fe_key_set(key_words, RTL_FE_KEY_ORIG_LSPID, 0);
  rtl_fe_key_crc(key_words, &software32, &software16);
  rtl_fe_header_ipv4(header, &key);
  ret = rtl_fe_crc_hardware(io, header, 63, &hardware32, &hardware16);
  if (ret)
   return ret;
  for (word = 0; word < RTL_FE_KEY_WORDS; word++) {
   tuple[word] = io->read(io->context, 0x38dc);
   if (tuple[word] != key_words[word]) {
    pr_info("TYFZA1 FE tuple vector%u word%u SW=%08x HW=%08x\n",vector,word,key_words[word],tuple[word]);
    return -EIO;
   }
  }
  if (software32 != hardware32 || software16 != hardware16)
   return -EIO;
 }
 pr_info("TYFZA1 native FE CRC:128 IPv4 TCP/UDP LAN/PON vectors match ASIC tuple and CRC16/32\n");
 return 0;
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
 if (!ret)
  ret = check_crc(&io);
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
