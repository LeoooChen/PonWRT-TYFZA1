/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_QM_H
#define RTL9617C_FTTR_QM_H
#include "rtl9617c-qm-deepq.h"

#define RTL_FTTR_DQ_BUFFERS 512
#define RTL_FTTR_DQ_BYTES (RTL_FTTR_DQ_BUFFERS * RTL_DQ_BUFFER_BYTES)

/* Original P6: QM port14/VOQ112..119, EQ profile0 (private0/shared2),
 * CB profile2. Native coherent DRAM replaces stock reserved SRAM. The
 * shared EQ2 and its BID range are owned by rtl_qm_deepq_prepare(). */
static inline int rtl_fttr_qm_prepare(const struct rtl_dq_io *io,
                                    unsigned int address, unsigned int bid)
{
 const unsigned int port=14, profile=2, count=RTL_FTTR_DQ_BUFFERS;
 const unsigned int low=RTL_DQ_BUFFERS*2/3;
 unsigned int voq;
 int ret;

 /* Called with QM receive/dequeue disabled and before loading BID caches. */
 io->write(io->context,0x61e8,address|1U);
 io->write(io->context,0x61ec,(count<<16)|bid);
 io->write(io->context,0x61f0,5); /* 2KiB hardware-owned buffers, no CPU refill. */
 io->write(io->context,0x61f4,0x61008060);
 io->write(io->context,0x61f8,2);
 io->write(io->context,0x6128,RTL_DQ_EQ_SHARED<<4);
 io->write(io->context,0x6148+port*4,0);
 /* Profile0 private/shared pool-selection thresholds from stock SCFG.
  * This is the profile pair; PON uses the separate profile1 at 0x9564. */
 io->write(io->context,0x9560,0x000c1000);
 io->write(io->context,0x9580+profile*4,low|(low*2<<16));
 io->write(io->context,0x9590+profile*4,0x3fff3fff);
 io->write(io->context,0x95a0,(io->read(io->context,0x95a0)&~(3U<<(port*2)))|
           (profile<<(port*2)));
 io->write(io->context,0x95ac,count|(1U<<14)|(RTL_DQ_BUFFERS<<16)|(1U<<30));
 ret=io->indirect(io->context,0x95a8,0xc0000000U|port);
 if(ret)return ret;
 /* Preserve stock private=25 and disabled group thresholds. Occupancy
  * thresholds use the shared native pool size, like the stock DQ builder. */
 io->write(io->context,0x95b4,0);
 io->write(io->context,0x95b8,25U<<24);
 io->write(io->context,0x95bc,low|(RTL_DQ_BUFFERS<<14));
 ret=io->indirect(io->context,0x95b0,0xc0000000U|profile);
 if(ret)return ret;
 /* Stock CB initialization programs port profile0 and all eight VOQ
  * backpressure profiles. Its profile-selector APIs are no-ops on 9617C;
  * do not invent per-P6 selector writes from another chip's register map. */
 io->write(io->context,0x963c,1000U<<16);
 for(voq=0;voq<8;voq++) {
  io->write(io->context,0x9658,768);
  ret=io->indirect(io->context,0x9654,0xc0000000U|voq);
  if(ret)return ret;
 }
 for(voq=port*8;voq<port*8+8;voq++) {
  io->write(io->context,0x95c4,profile);
  ret=io->indirect(io->context,0x95c0,0xc0000000U|voq);
  if(ret)return ret;
 }
 io->write(io->context,0x63c4+port*4,255);
 io->write(io->context,0x6444+port*8,0x01010101);
 io->write(io->context,0x6448+port*8,0x01010101);
 /* Stock QM return selects L2TM by logical destination; preserve all others. */
 io->write(io->context,0xa1c8,io->read(io->context,0xa1c8)|(1U<<6));
 return 0;
}
#endif
