#!/usr/bin/env python3
"""Execute ARM pool handoff, queued payload dispatch, NI tail and RDU persistence."""
from pathlib import Path
import json,re,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(D/'rx.c').read_text()
def function(text,name):
 a=re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{',text,re.M).start();b=text.index('{',a)+1;depth=1
 while depth:depth+=(text[b]=='{')-(text[b]=='}');b+=1
 return text[a:b]+'\n'
common=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;typedef int32_t s32;typedef uint32_t __le32;typedef uint32_t dma_addr_t;
#include "receive.h"
#define BIT(n) (1U<<(n))
#define SZ_128K 131072
#define RTL_AX_RX_IRQS 0x00180005
#define RTL_AX_IRQ_INDICATION 0x21000000
#define DMA_BIDIRECTIONAL 0
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define WRITE_ONCE(p,v) ((p)=(v))
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define dma_wmb() ((void)0)
#define dma_rmb() ((void)0)
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static uint16_t get_unaligned_le16(const void *p){uint16_t v;memcpy(&v,p,2);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
struct rtl_ax_rx_buffer {u8 *data;dma_addr_t dma;};
struct rtl_ax_rx_queue {u8 *descriptors,*buffers;u32 descriptor_dma,buffer_dma;
 struct rtl_ax_rx_buffer *payloads;__le32 *addresses;dma_addr_t addresses_dma;unsigned allocated_payloads;u16 buffer_ids[256];
 unsigned consumer,tag,buffer_bytes;};
struct rtl_ax_rx {bool rpq_transfer,rpq_owned,buffer_pool_initialized,running;unsigned irq_mask,uplink_wifi_id;int irq_lock,irq,work;
 const struct rtl_ax_io *io;void *pe_device;u8 *rpq_memory,*uplink_states,*pe_forward;u32 pe_forward_dma,dfs_epoch[4],uplink_seen[1];
 struct rtl_ax_rx_queue queues[2];u64 pe_rdu[2];u32 pe_rdu_seen[2];
 u64 pe_rx_data,pe_rx_data_bytes,pe_rx_pm_data,pe_rx_delivered,pe_rx_malformed;
 int (*handler)(void*,const struct rtl_ax_rx_packet*);void *handler_ctx;};
static unsigned wakes,dispatches,copy_syncs;static bool seeded;
static void synchronize_irq(int irq){(void)irq;}
static void cancel_delayed_work_sync(int *work){(void)work;}
static void dma_sync_single_for_device(void *dev,u32 dma,unsigned bytes,int dir){(void)dev;(void)dma;(void)bytes;(void)dir;}
static void dma_sync_single_for_cpu(void *dev,u32 dma,unsigned bytes,int dir){(void)dev;(void)dma;(void)bytes;(void)dir;copy_syncs++;}
static u32 read32(void *ctx,unsigned reg){(void)ctx;assert(reg==0x1218 || reg==0x121c);return 2U<<16|2;}
static void write32(void *ctx,unsigned reg,u32 v){(void)ctx;assert((reg==0x10b0 && v==0) || (reg==0x30b0 && v==RTL_AX_IRQ_INDICATION));}
static void write16(void *ctx,unsigned reg,unsigned short v){(void)ctx;assert(reg==0x1218 && v==2);}
#define lower_32_bits(v) (v)
#define cpu_to_le32(v) (v)
static void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx){assert(!rx->rpq_transfer);wakes++;}
static void rtl_ax_rx_pe_drain(struct rtl_ax_rx *rx){(void)rx;}
static void rtl_ax_rx_uplink_sync(struct rtl_ax_rx *rx){(void)rx;}
static void rtl_ax_rx_uplink_seed(struct rtl_ax_rx *rx,const struct rtl_ax_rx_packet *p){(void)rx;(void)p;seeded=true;}
static int handler(void *ctx,const struct rtl_ax_rx_packet *p){(void)ctx;assert(p->type==8 && p->payload[0]==5);dispatches++;return 0;}
#define PE_PROBE_MAILBOX 0x2000
struct rtl_ax_tx {struct {u8 *memory;}pe;};
'''
names=['rtl_ax_rx_slot_buffer','rtl_ax_rx_dfs_epoch','rtl_ax_rx_dispatch','rtl_ax_rx_pe_deliver','rtl_ax_rx_pe_rxq_drain','rtl_ax_rx_rdu_sync','rtl_ax_rx_pe_rpq_start','rtl_ax_rx_idle_put','rtl_ax_rx_tail_recycle','rtl_ax_rx_work_recycle','rtl_ax_rx_pe_rpq_stop']
case=r'''
int main(void){
 u8 descriptors[256*8]={},memory[131072]={},states[131072]={};
 struct rtl_ax_rx_buffer payloads[RTL_AX_RX_BUFFERS]={};__le32 addresses[RTL_AX_RX_BUFFERS]={};
 for(unsigned id=0;id<RTL_AX_RX_BUFFERS;id++){
  payloads[id].data=calloc(1,12288);payloads[id].dma=0x6000000+((id*73)%514)*16384;addresses[id]=payloads[id].dma;
 }
 struct rtl_ax_io io={.read32=read32,.write32=write32,.write16=write16};
 struct rtl_ax_rx rx={.io=&io,.uplink_states=states,.pe_forward_dma=0x8000000,.handler=handler,
 .queues={{.descriptors=descriptors,.descriptor_dma=0x9000000,.payloads=payloads,.addresses=addresses,.addresses_dma=0x7100000,
   .consumer=254,.tag=8190,.buffer_bytes=12288},
  {.buffer_dma=0x7000000,.consumer=255,.tag=8191,.buffer_bytes=1024}}};
 for(unsigned i=0;i<256;i++){rx.queues[0].buffer_ids[i]=256+i;put_unaligned_le32(payloads[256+i].dma,descriptors+i*8+4);}
 for(unsigned i=0;i<256;i++)assert(rtl_ax_rx_slot_buffer(&rx,0,i).data==payloads[256+i].data);
 rtl_ax_rx_pe_rpq_start(&rx,memory);
 assert(get_unaligned_le32(memory+0x2000+RTL_AX_RX_IDLE_COUNT*4)==258);
 assert(get_unaligned_le32(memory+0x2140)==rx.queues[0].addresses_dma);
 put_unaligned_le32(254,memory+0x2000+RTL_AX_RX_WORK_READ*4);
 put_unaligned_le32(2,memory+0x2000+RTL_AX_RX_WORK_WRITE*4);
 rtl_ax_rx_dfs_epoch(&rx);
 for(unsigned i=0;i<4;i++)assert(get_unaligned_le32(memory+RTL_AX_RX_WORK_IDS+((254+i)&255)*4)&RTL_AX_RX_WORK_OLD);

 unsigned count=0;
 for(unsigned id=6;id<256;id++)put_unaligned_le32(id,memory+RTL_AX_RX_IDLE_IDS+count++*4);
 for(unsigned id=512;id<514;id++)put_unaligned_le32(id,memory+RTL_AX_RX_IDLE_IDS+count++*4);
 assert(count==252);put_unaligned_le32(count,memory+0x2000+RTL_AX_RX_IDLE_COUNT*4);
 unsigned slots[]={254,255,0,1,2,3};u32 owners[]={1U<<30,(1U<<31)|98,(1U<<31)|100,(1U<<31)|101,0,0};
 for(unsigned i=0;i<6;i++){
  put_unaligned_le32(i|(i==4?RTL_AX_RX_WORK_OLD:0),memory+RTL_AX_RX_WORK_IDS+slots[i]*4);
  put_unaligned_le32(owners[i],memory+RTL_AX_RX_WORK_STATUS+slots[i]*4);
  put_unaligned_le32(24|0xc000,payloads[i].data);put_unaligned_le32((8U<<24)|4,payloads[i].data+4);payloads[i].data[20]=i;
 }
 put_unaligned_le32(254,memory+0x2000+RTL_AX_RX_WORK_READ*4);
 put_unaligned_le32(2,memory+0x2000+RTL_AX_RX_WORK_PROCESS*4);
 put_unaligned_le32(4,memory+0x2000+RTL_AX_RX_WORK_WRITE*4);
 put_unaligned_le32(2,memory+0x2144);put_unaligned_le32(1234,memory+0x2148);
 put_unaligned_le32(5,memory+0x2000+RTL_AX_RX_RDU*4);put_unaligned_le32(7,memory+0x2000+RTL_AX_RPQ_RDU*4);
 put_unaligned_le32(100,memory+0x216c);
 assert(rtl_ax_rx_pe_rpq_stop(&rx)==-EBUSY && rx.rpq_owned && rx.rpq_memory==memory);
 assert(!dispatches && get_unaligned_le32(memory+0x2000+RTL_AX_RX_IDLE_COUNT*4)==252);
 assert(rx.pe_rdu[0]==5 && rx.pe_rdu[1]==7);
 put_unaligned_le32(101,memory+0x216c);
 assert(!rtl_ax_rx_pe_rpq_stop(&rx) && !rx.rpq_owned && !rx.rpq_memory);
 assert(dispatches==1 && seeded && copy_syncs==3 && rx.pe_rx_delivered==2);
 assert(rx.queues[0].consumer==2 && rx.queues[0].tag==1234);
 for(unsigned i=0;i<256;i++)assert(rx.queues[0].buffer_ids[i]==256+i);
 for(unsigned i=0;i<256;i++)assert(rtl_ax_rx_slot_buffer(&rx,0,i).dma==payloads[256+i].dma);
 assert(get_unaligned_le32(payloads[2].data)!=0 && get_unaligned_le32(payloads[3].data)!=0);
 assert(get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL)==3 && get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL+8)==4);
 assert(get_unaligned_le32(memory+0x2000+RTL_AX_RX_IDLE_COUNT*4)==256);
 rtl_ax_rx_pe_rpq_start(&rx,memory);
 assert(get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL)==3 && get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL+8)==4);
 assert(get_unaligned_le32(memory+0x2000+RTL_AX_RX_IDLE_COUNT*4)==256);
 assert(rx.pe_rdu[0]==5 && rx.pe_rdu[1]==7);
 struct rtl_ax_tx tx={.pe={memory}};uplink_counter_start(&tx,4);
 assert(get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL+4)==3 && get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL+12)==4);
 rx.pe_rdu[0]=100;rx.pe_rdu_seen[0]=0xfffffffe;put_unaligned_le32(1,memory+0x2000+RTL_AX_RX_RDU*4);
 rtl_ax_rx_rdu_sync(&rx);assert(rx.pe_rdu[0]==103);rtl_ax_rx_rdu_sync(&rx);assert(rx.pe_rdu[0]==103);
 for(unsigned id=0;id<RTL_AX_RX_BUFFERS;id++)free(payloads[id].data);
}
'''
with tempfile.TemporaryDirectory(prefix='rx-pool-handoff-',dir=ROOT/'artifacts') as temp:
 p=Path(temp);c=p/'case.c'
 c.write_text(common+''.join(function(source,n) for n in names)+function((D/'ni-test.h').read_text(),'uplink_counter_start')+case)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-I',str(D),str(c),'-o',str(p/'case')],check=True)
 subprocess.run([str(p/'case')],check=True)
print(json.dumps({'scope':'Actual ARM pool start/stop, dispatch and RDU functions with DMA/IRQ/upper-stack callbacks; no hardware execution.',
 'unconsumed_NI_preserves_ownership':True,'queued_old_DFS_discarded_new_DFS_delivered':True,'DFS_epoch_marks_pending_work_across_wrap':True,'NI_tail_retained_across_restart_and_epoch_rebase':True,
 'physical_RX_cursor_not_software_work_cursor':True,'noncontiguous_DMA_and_CPU_payload_addresses':True,'RDU_persists_across_handoff':True,'RDU_32bit_wrap_accumulates_once':True},indent=2))
