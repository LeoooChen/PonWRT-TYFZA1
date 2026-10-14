#!/usr/bin/env python3
"""Check the current ARM-to-PE handoff with original and rotated RXBD addresses."""
from pathlib import Path
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx.c').read_text()


def function(name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8; typedef uint32_t u32;
#define RTL_AX_RX_ENTRIES 256
#define RTL_AX_RX_SPARES 2
#define RTL_AX_RXQ_BUFFER_BYTES 12288
#define RTL_AX_IRQ_INDICATION 0x21000000
#define SZ_128K 131072
#define DMA_BIDIRECTIONAL 0
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define WRITE_ONCE(p,v) ((p)=(v))
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define dma_wmb() ((void)0)
struct rtl_ax_io {void *ctx;void (*write32)(void*,unsigned,u32);};
struct rtl_ax_rx_queue {u8 *descriptors;u32 descriptor_dma,buffer_dma;unsigned consumer,tag;};
struct rtl_ax_rx {bool rpq_transfer,rpq_owned;unsigned irq_mask;int irq_lock,irq,work;
 bool buffer_pool_initialized;
 const struct rtl_ax_io *io;void *pe_device,*rpq_memory,*uplink_states;
 u32 pe_forward_dma;struct rtl_ax_rx_queue queues[2];u32 uplink_seen[1];};
static unsigned sync_count,stopped,cancelled,woken;
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void synchronize_irq(int irq){(void)irq;assert(stopped);}
static void cancel_delayed_work_sync(int *work){(void)work;cancelled++;}
static void dma_sync_single_for_device(void *dev,u32 dma,unsigned bytes,int dir){
 (void)dev;(void)dir;assert(cancelled);
 assert((dma==0x6000000 && bytes==258*12288) || (dma==0x7000000 && bytes==SZ_128K));sync_count++;}
static void write32(void *ctx,unsigned reg,u32 value){
 (void)ctx;if(reg==0x10b0){assert(value==0);stopped++;}
 else assert(reg==0x30b0 && value==RTL_AX_IRQ_INDICATION);}
static void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx){assert(!rx->rpq_transfer);woken++;}
#define PE_PROBE_MAILBOX 0x2000
struct rtl_ax_tx {struct {u8 *memory;} pe;};
'''
case = r'''
int main(void){
 u8 descriptors[256*8]={},memory[131072]={},states[131072]={};
 struct rtl_ax_io io={.write32=write32};
 struct rtl_ax_rx rx={.io=&io,.uplink_states=states,.pe_forward_dma=0x8000000,
  .queues={{descriptors,0x9000000,0x6000000,254,8190},{0,0,0x7000000,255,8191}}};
 for(unsigned rotated=0;rotated<2;rotated++){
  rx.buffer_pool_initialized=false;
  for(unsigned slot=0;slot<256;slot++){
   unsigned id=rotated && slot<2?256+slot:slot;
   put_unaligned_le32(0x6000000+id*12288,descriptors+slot*8+4);
  }
  rtl_ax_rx_pe_rpq_start(&rx,memory);
  assert(rx.rpq_owned && !rx.rpq_transfer && rx.rpq_memory==memory);
  assert(get_unaligned_le32(memory+0x2000+134*4)==0x9000000);
  for(unsigned slot=0;slot<256;slot++){
   unsigned id=rotated && slot<2?256+slot:slot;
   assert(get_unaligned_le32(memory+0x17400+slot*4)==id);
   assert(rtl_ax_rx_buffer_offset(&rx.queues[0],slot)==id*12288);
  }
  assert(get_unaligned_le32(memory+0x17800)==(rotated?1:257));
  assert(get_unaligned_le32(memory+0x17804)==(rotated?2:258));
  assert(get_unaligned_le32(memory+0x17810)==0 && get_unaligned_le32(memory+0x17818)==0);
  assert(get_unaligned_le32(memory+0x2144)==254 && get_unaligned_le32(memory+0x2148)==8190);
 }
 /* This is a real resume of the rotated pool, with both spare buffers
  * retained by NI rather than merely absent from the RXBD ring. */
 put_unaligned_le32(0,memory+0x17800);put_unaligned_le32(0,memory+0x17804);
 put_unaligned_le32(1,memory+0x17810);put_unaligned_le32(100,memory+0x17814);
 put_unaligned_le32(2,memory+0x17818);put_unaligned_le32(101,memory+0x1781c);
 put_unaligned_le32(101,memory+0x216c);
 rtl_ax_rx_pe_rpq_start(&rx,memory);
 assert(get_unaligned_le32(memory+0x17800)==0 && get_unaligned_le32(memory+0x17804)==0);
 assert(get_unaligned_le32(memory+0x17810)==1 && get_unaligned_le32(memory+0x17818)==2);
 struct rtl_ax_tx tx={.pe={memory}};
 uplink_counter_start(&tx,4);
 assert(get_unaligned_le32(memory+0x17814)==3 && get_unaligned_le32(memory+0x1781c)==4);
 assert(get_unaligned_le32(memory+0x2168)==4 && get_unaligned_le32(memory+0x216c)==4);
 assert(sync_count==6 && woken==3);
}
'''
out = ROOT / 'artifacts/pe-owner64'
rx_code = function('rtl_ax_rx_buffer_offset') + function('rtl_ax_rx_pe_rpq_start')
source = (ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h').read_text()
(out / 'handoff.c').write_text(common + rx_code + function('uplink_counter_start') + case)
subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(out / 'handoff.c'), '-o', str(out / 'handoff')], check=True)
subprocess.run([str(out / 'handoff')], check=True)
print(json.dumps(dict(current_ARM_handoff_executed=True, original_and_rotated_addresses=True,
                      all_256_slot_addresses_and_two_unused_buffers_match=True,
                      RX_tag_and_consumer_preserved=True, retained_tail_survives_resume=True,
                      held_sequence_rebased_with_original_distance=True,
                      scope='Current handoff and queue-sequence initializer with DMA/IRQ callbacks; no hardware execution.'), indent=2))
