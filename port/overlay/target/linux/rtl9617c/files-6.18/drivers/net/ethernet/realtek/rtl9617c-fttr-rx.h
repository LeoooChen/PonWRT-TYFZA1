/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_RX_H
#define RTL9617C_FTTR_RX_H

#include "rtl9617c-fttr-frame.h"

struct rtl_fttr_rx {
 struct sk_buff *skb;
 unsigned int length, source, epoch;
 u64 deadline;
};

static inline void rtl_fttr_rx_reset(struct rtl_fttr_rx *rx)
{
 if(rx->skb)dev_kfree_skb_any(rx->skb);
 rx->skb=NULL;
}

/* P6 frames fit at most two stock CPU buffers: first payload1592/1584,
 * continuation1600. Each segment is copied before its EPP/BID is returned.
 * 1 means complete, 0 means first segment retained, negative means malformed. */
static inline int rtl_fttr_rx_segment(struct rtl_fttr_rx *rx, struct napi_struct *napi,
 unsigned int command, const u8 *data, unsigned int capacity, unsigned int length,
 unsigned int source, unsigned int epoch, u64 now, struct sk_buff **packet)
{
 bool first=command&(1U<<4), last=command&(1U<<6);
 unsigned int bytes;

 *packet=NULL;
 if(first) {
  rtl_fttr_rx_reset(rx);
  if((source&255)!=6 || last || length<=capacity || length>RTL_FTTR_FRAME_BYTES)
   return -EMSGSIZE;
  rx->skb=napi_alloc_skb(napi,length);
  if(!rx->skb)return -ENOMEM;
  rx->length=length;rx->source=source;rx->epoch=epoch;
  rx->deadline=now+1000000000ULL;
  memcpy(skb_put(rx->skb,capacity),data,capacity);
  return 0;
 }
 if(!rx->skb)return -EBADMSG;
 bytes=rx->length-rx->skb->len;
 if(!last || bytes>capacity || rx->epoch!=epoch || now>rx->deadline) {
  rtl_fttr_rx_reset(rx);
  return -EBADMSG;
 }
 memcpy(skb_put(rx->skb,bytes),data,bytes);
 *packet=rx->skb;rx->skb=NULL;
 return 1;
}
#endif
