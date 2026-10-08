// SPDX-License-Identifier: GPL-2.0-or-later
/* Native FE mask/CRC transaction on a selected idle slot; restore its value. */
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include "rtl9617c-fe-table.h"

static void __iomem *base;
static unsigned int mask_slot=60;
module_param(mask_slot,uint,0600);
static bool ipv6,wide_mask;
static unsigned int address_prefix=128;
static unsigned int packet_type=6;
module_param(packet_type,uint,0600);
module_param(address_prefix,uint,0600);
module_param(ipv6,bool,0600);
module_param(wide_mask,bool,0600);
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
 bool mismatch;
 int ret;

 for (vector = 0; vector < 128; vector++) {
  struct rtl_fe_ipv4_key key = {
   .source = 0x0a010200 + vector, .destination = 0xc0000201 + vector,
   .source_port = 53000 + vector, .destination_port = 443 + vector,
   .protocol = vector & 1 ? 6 : 17, .ingress = vector & 1 ? 7 : 0,
   .llid = 0, .profile = 0, .mask = mask_slot,
  };
  if(ipv6) {
   struct rtl_fe_ipv6_key key6={.source={0x20010db8,vector,0x12345678,0xabcdef01+vector},
    .destination={0x20010db8,vector+1,0xaabbccdd,0x01020304+vector},
    .source_port=key.source_port,.destination_port=key.destination_port,
    .protocol=key.protocol,.ingress=key.ingress,.mask=mask_slot};
   rtl_fe_ipv6_key(key_words,&key6);
   rtl_fe_header_ipv6(header,&key6);
   rtl_fe_bits_set(header,489,3,packet_type);
  } else {
   rtl_fe_ipv4_key(key_words, &key);
   rtl_fe_header_ipv4(header, &key);
  }
  rtl_fe_key_set(key_words, RTL_FE_KEY_HKEY_ID, 0);
  rtl_fe_key_set(key_words, RTL_FE_KEY_TABLE_ID, 0);
  rtl_fe_key_set(key_words, RTL_FE_KEY_ORIG_LSPID, 0);
  rtl_fe_key_crc(key_words, &software32, &software16);
  ret = rtl_fe_crc_hardware(io, header, mask_slot, &hardware32, &hardware16);
  if (ret)
   return ret;
  mismatch=false;
  for (word = 0; word < RTL_FE_KEY_WORDS; word++) {
   tuple[word] = io->read(io->context, 0x38dc);
   if (tuple[word] != key_words[word]) {
    pr_info("TYFZA1 FE tuple vector%u word%u SW=%08x HW=%08x\n",vector,word,key_words[word],tuple[word]);
    mismatch=true;
   }
  }
  if(mismatch) {
   pr_info("TYFZA1 FE failed CRC SW=%08x/%04x HW=%08x/%04x\n",software32,software16,hardware32,hardware16);
   return -EIO;
  }
  if (software32 != hardware32 || software16 != hardware16)
   return -EIO;
 }
 pr_info("TYFZA1 native FE CRC:128 IPv%u TCP/UDP LAN/PON vectors match ASIC tuple and CRC16/32;mask%u full128=%u\n",ipv6?6:4,mask_slot,ipv6||wide_mask);
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
 ret = rtl_fe_mask_read(&io, mask_slot, saved);
 if (ret)
  goto done;
 if(ipv6||wide_mask)rtl_fe_ipv6_mask(expected);
 else rtl_fe_ipv4_mask(expected);
 if(ipv6||wide_mask) {
  rtl_fe_bits_set(expected,58,9,address_prefix);
  rtl_fe_bits_set(expected,67,9,address_prefix);
 }
 ret = rtl_fe_mask_write(&io, mask_slot, expected);
 if (!ret)
  ret = rtl_fe_mask_read(&io, mask_slot, actual);
 if (!ret && memcmp(actual, expected, sizeof(actual)))
  ret = -EIO;
 if (!ret)
  ret = check_crc(&io);
 restored = rtl_fe_mask_write(&io, mask_slot, saved);
 if (!restored)
  restored = rtl_fe_mask_read(&io, mask_slot, actual);
 if (!restored && memcmp(actual, saved, sizeof(saved)))
  restored = -EIO;
 pr_info("TYFZA1 native FE mask%u: lower/upper128 readback=%d original_restored=%d HASH_INI=%08x CACHE_INI=%08x\n",
  mask_slot,ret, restored, readl(base + 0x3834), readl(base + 0x38a0));
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
