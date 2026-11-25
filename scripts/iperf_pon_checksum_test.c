/* Diagnostic self-test: real kernel skb/checksum APIs, no NI or MMIO access. */
#include <linux/module.h>
#include <linux/skbuff.h>
#include <linux/etherdevice.h>
#include <linux/ip.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <linux/unaligned.h>
#include <net/checksum.h>
#include "copy-payload.h"
static unsigned cases,zero_cases,invalid_cases;
static int result;
module_param(result,int,0400);
module_param(cases,uint,0400);
module_param(zero_cases,uint,0400);
module_param(invalid_cases,uint,0400);
static unsigned char original[1600],frame[1600],expected[1600];
static int run_case(unsigned payload,bool udp,unsigned split,bool shared,bool partial,bool zero)
{
 unsigned transport=udp?8:20, length=14+20+transport+payload, head=split?split:length;
 struct sk_buff *skb=alloc_skb(head+32,GFP_KERNEL);struct page *page=NULL;
 __be32 src=htonl(0xc612ff01),dst=htonl(0xc0a801ab);
 unsigned offset=udp?6:16,at=34+offset;
 if(!skb)return -ENOMEM;
 memset(original,0,sizeof(original));memset(frame,0xa5,sizeof(frame));
 for(unsigned n=0;n<length;n++)original[n]=(n*17+payload)&255;
 original[14]=0x45;original[23]=udp?17:6;
 memcpy(original+26,&src,4);memcpy(original+30,&dst,4);
 put_unaligned((__sum16)~csum_tcpudp_magic(src,dst,length-34,udp?17:6,0),(__sum16*)(original+at));
 if(zero){
  unsigned i;
  for(i=0;i<65536;i++){
   put_unaligned_be16(i,original+length-2);
   if(!csum_fold(csum_partial(original+34,length-34,0)))break;
  }
  if(i==65536){kfree_skb(skb);return -EINVAL;}
 }
 memcpy(expected,original,length);
 if(partial){
  __sum16 checksum=csum_fold(csum_partial(expected+34,length-34,0));
  if(!checksum)checksum=CSUM_MANGLED_0;
  put_unaligned(checksum,(__sum16*)(expected+at));
 }
 skb_reserve(skb,16);memcpy(skb_put(skb,head),original,head);
 if(head<length){
  page=alloc_page(GFP_KERNEL);if(!page){kfree_skb(skb);return -ENOMEM;}
  memcpy(page_address(page),original+head,length-head);
  skb_add_rx_frag(skb,0,page,0,length-head,PAGE_SIZE);
  if(shared)skb_shinfo(skb)->flags|=SKBFL_SHARED_FRAG;
 }
 skb->ip_summed=partial?CHECKSUM_PARTIAL:CHECKSUM_NONE;
 skb->csum_start=skb_headroom(skb)+34;skb->csum_offset=offset;
 result=copy_payload(skb,frame);
 if(result)goto out;
 if(memcmp(frame+22,expected+14,length-14)) {result=-EBADMSG;goto out;}
 if(partial&&csum_tcpudp_magic(src,dst,length-34,udp?17:6,csum_partial(frame+42,length-34,0))) {result=-EILSEQ;goto out;}
 if(frame[21]!=0xa5||frame[length+8]!=0xa5) {result=-EOVERFLOW;goto out;}
 if(skb_copy_bits(skb,0,expected,length)||memcmp(expected,original,length)) {result=-EUCLEAN;goto out;}
 cases++;if(zero)zero_cases++;
out:kfree_skb(skb);return result;
}
static int __init begin(void)
{
 unsigned sizes[]={2,3,63,64,65,1000,1438};
 for(unsigned protocol=0;protocol<2;protocol++)for(unsigned n=0;n<ARRAY_SIZE(sizes);n++)
  for(unsigned kind=0;kind<3;kind++)for(unsigned partial=0;partial<2;partial++){
   unsigned head=kind?(protocol?43:55):0;
   if((result=run_case(sizes[n],protocol,head,kind==2,partial,n==0&&partial)))return 0;
  }
 for(unsigned kind=0;kind<3;kind++){
  struct sk_buff *s=alloc_skb(100,GFP_KERNEL);if(!s){result=-ENOMEM;return 0;}
  memset(skb_put(s,100),0,100);s->ip_summed=CHECKSUM_PARTIAL;
  s->csum_start=kind==0?12:kind==1?100:34;s->csum_offset=kind==2?66:6;
  int ret=copy_payload(s,frame);kfree_skb(s);
  if(ret!=-EINVAL){result=-EINVAL;return 0;}invalid_cases++;
 }
 result=0;pr_info("checksum_selftest: %u cases, %u zero, %u invalid passed\n",cases,zero_cases,invalid_cases);return 0;
}
static void __exit end(void){}
module_init(begin);module_exit(end);MODULE_LICENSE("GPL");MODULE_DESCRIPTION("Actual skb checksum/copy contract without hardware access");
