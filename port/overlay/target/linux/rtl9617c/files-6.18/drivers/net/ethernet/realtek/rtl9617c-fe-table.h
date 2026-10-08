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
#endif
