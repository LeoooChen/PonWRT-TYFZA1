/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_FE_TABLE_H
#define RTL9617C_FE_TABLE_H
#include "rtl9617c-fe-format.h"
#include "rtl9617c-qm-deepq.h"

/* A 224-bit mask occupies two 128-bit hardware banks. Addr bit6 selects
 * the upper bank; DATA0..3 are arranged in reverse register-address order.
 * The owner serializes these transactions with the FE table mutex. */
static inline int rtl_fe_mask_write(const struct rtl_dq_io *io, unsigned int index,
 const unsigned int words[RTL_FE_MASK_WORDS])
{
 unsigned int bank, word;
 int ret;

 for (bank = 0; bank < 2; bank++) {
  for (word = 0; word < 4; word++)
   io->write(io->context, 0x3920 - word * 4, words[bank * 4 + word]);
  ret = io->indirect(io->context, 0x3910, 0xc0000000U | (bank << 6) | index);
  if (ret)
   return ret;
 }
 return 0;
}
static inline int rtl_fe_mask_read(const struct rtl_dq_io *io, unsigned int index,
 unsigned int words[RTL_FE_MASK_WORDS])
{
 unsigned int bank, word;
 int ret;

 for (bank = 0; bank < 2; bank++) {
  ret = io->indirect(io->context, 0x3910, 0x80000000U | (bank << 6) | index);
  if (ret)
   return ret;
  for (word = 0; word < 4; word++)
   words[bank * 4 + word] = io->read(io->context, 0x3920 - word * 4);
 }
 return 0;
}

/* SWO command completion uses bit0; the provider's indirect callback must
 * wait for that bit, while ordinary table accesses complete on bit31. */
static inline int rtl_fe_crc_hardware(const struct rtl_dq_io *io,
 const unsigned int header[RTL_FE_HEADER_WORDS], unsigned int mask,
 unsigned int *crc32, unsigned short *crc16)
{
 unsigned int i;
 int ret;

 io->write(io->context, 0x38d8, 0);
 for (i = 0; i < RTL_FE_HEADER_WORDS; i++)
  io->write(io->context, 0x38dc, header[i]);
 io->write(io->context, 0x38dc, mask);
 ret = io->indirect(io->context, 0x38e0, 1);
 if (ret)
  return ret;
 io->write(io->context, 0x38d8, 33);
 *crc32 = io->read(io->context, 0x38dc);
 *crc16 = io->read(io->context, 0x38dc) & 0xffff;
 return 0;
}

#define RTL_FE_HASH_ENTRIES 65536
#define RTL_FE_CACHE_ENTRIES 2048
#define RTL_FE_ACTION_BYTES 48
#define RTL_FE_HASH_BYTES (RTL_FE_HASH_ENTRIES * 4)
#define RTL_FE_ACTION_OFFSET RTL_FE_HASH_BYTES
#define RTL_FE_OVERFLOW_OFFSET (RTL_FE_ACTION_OFFSET + RTL_FE_HASH_ENTRIES * RTL_FE_ACTION_BYTES)
#define RTL_FE_DEFAULT_OFFSET (RTL_FE_OVERFLOW_OFFSET + 64 * RTL_FE_ACTION_BYTES)
#define RTL_FE_MEMORY_BYTES ((RTL_FE_DEFAULT_OFFSET + 32 * 16 + 4095) & ~4095U)

/* Original board defaults:64K main entries,32-entry buckets,384-bit slots.
 * The224-bit normal action fits its48-byte slot;defaults use128-bit slots.
 * Cache has2048 entries and its own internal224-bit action SRAM.
 * The DMA provider supplies the board's coherent CCI address alias. */
static inline void rtl_fe_dma_tables(const struct rtl_dq_io *io,
 unsigned int address, unsigned int address_high)
{
 io->write(io->context, 0x38c4, 0);
 io->write(io->context, 0x3838, address_high);
 io->write(io->context, 0x383c, address);
 io->write(io->context, 0x3840, address_high);
 io->write(io->context, 0x3844, address + RTL_FE_ACTION_OFFSET);
 io->write(io->context, 0x384c, address_high);
 io->write(io->context, 0x3850, address + RTL_FE_OVERFLOW_OFFSET);
 io->write(io->context, 0x3858, address_high);
 io->write(io->context, 0x385c, address + RTL_FE_DEFAULT_OFFSET);
 io->write(io->context, 0x3834,
  (io->read(io->context, 0x3834) & ~0x300ffU) | 3 | (7 << 2) | (4 << 5));
 io->write(io->context, 0x3848, 4);
 io->write(io->context, 0x3854, 1);
 io->write(io->context, 0x38a0, (1 << 18) | (1 << 16) | (3 << 8) | 4);
}
/* Stock cache initialization and FC lifetime management:stage3 allocation
 * with replacement, full cache age units, and no main aging countdown.
 * The owner saves/restores all five configuration registers. */
static inline void rtl_fe_cache_start(const struct rtl_dq_io *io)
{
 io->write(io->context,0x3924,0);
 io->write(io->context,0x38b8,0xffffffffU);
 io->write(io->context,0x38bc,0xffffffffU);
 io->write(io->context,0x38c4,0xa0000000U);
}
static inline unsigned int rtl_fe_hash_bucket(unsigned short crc16)
{
 /* GPL crc16_mask[32-entry bucket][64K table] is0xffe0. */
 return crc16&0xffe0U;
}

/* Group16+18+19 CPU miss action in its128-bit slot. It preserves packet
 * bytes and bypasses later route lookup before entering CPU16. */
static inline void rtl_fe_cpu_miss(unsigned int words[4])
{
 unsigned int i;
 for (i = 0; i < 4; i++)
  words[i] = 0;
 rtl_fe_bits_set(words, 22 + 4, 1, 1); /* group18 dpid_vld */
 rtl_fe_bits_set(words, 22 + 5, 1, 1); /* dpid_pri */
 rtl_fe_bits_set(words, 22 + 6, 1, 1); /* permit */
 rtl_fe_bits_set(words, 22 + 8, 10, 16);
 rtl_fe_bits_set(words, 41 + 13, 1, 1); /* keep_orig_pkt_vld */
 rtl_fe_bits_set(words, 41 + 14, 1, 1); /* keep_orig_pkt */
 rtl_fe_bits_set(words, 41 + 35, 4, 15); /* t2_ctrl bypass */
 rtl_fe_bits_set(words, 41 + 34, 1, 1); /* t2_ctrl_vld */
}

/* Each age row owns32 slots, each four bits. Main indices are linear
 * bucket/slot values; keep the other31 slots when installing/removing a flow. */
static inline int rtl_fe_main_age(const struct rtl_dq_io *io,unsigned int index,unsigned int age)
{
 unsigned int row=index>>5,slot=index&31,reg=0x3938-(slot>>3)*4,value;
 int ret=io->indirect(io->context,0x3928,0x80000000U|row);
 if(ret)return ret;
 value=io->read(io->context,reg);
 value=(value&~(15U<<((slot&7)*4)))|((age&15)<<((slot&7)*4));
 io->write(io->context,reg,value);
 return io->indirect(io->context,0x3928,0xc0000000U|row);
}
static inline int rtl_fe_main_age_read(const struct rtl_dq_io *io,
 unsigned int index,unsigned int *age)
{
 unsigned int slot=index&31;
 int ret=io->indirect(io->context,0x3928,0x80000000U|(index>>5));

 if(ret)return ret;
 *age=(io->read(io->context,0x3938-(slot>>3)*4)>>((slot&7)*4))&15;
 return 0;
}
static inline int rtl_fe_cache_command(const struct rtl_dq_io *io,unsigned int index,
 unsigned short crc16,unsigned int command)
{
 io->write(io->context,0x38ac,(index&31)|((unsigned int)crc16<<5)|(3U<<26)|(command<<30));
 return io->indirect(io->context,0x38b0,1);
}
static inline int rtl_fe_cache_read(const struct rtl_dq_io *io,unsigned int index,int fib,unsigned int words[7])
{
 unsigned int i;
 int ret=io->indirect(io->context,0x38e4,0x80000000U|index|(fib?1U<<11:0));
 if(ret)return ret;
 for(i=0;i<7;i++)words[i]=io->read(io->context,0x3900-i*4);
 return 0;
}
#endif
