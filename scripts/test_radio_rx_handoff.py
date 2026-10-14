#!/usr/bin/env python3
"""Run actual RX ownership handoff callbacks with pending, unready DMA slots."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8; typedef uint32_t u32; typedef uint32_t dma_addr_t;
#define BIT(n) (1U<<(n))
#define SZ_128K 131072
#define DMA_BIDIRECTIONAL 0
#define RTL_AX_IRQ_INDICATION (BIT(29)|BIT(24))
#define RTL_AX_RX_ENTRIES 256
#define RTL_AX_RXQ_BUFFER_BYTES 12288
#define WRITE_ONCE(p,v) ((p)=(v))
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define dma_wmb() ((void)0)
struct rtl_ax_io {void *ctx;void(*write32)(void*,unsigned,u32);};
struct work {int member;};
struct queue {unsigned consumer,tag;dma_addr_t buffer_dma;};
struct rtl_ax_rx {int irq_lock,irq;bool rpq_transfer,rpq_owned;u32 irq_mask;
 const struct rtl_ax_io *io;struct work work;struct queue queues[2];
 void *pe_device,*rpq_memory;dma_addr_t pe_forward_dma;};
static struct rtl_ax_rx *current;
static unsigned synchronized,cancelled,synced,woken;
static void synchronize_irq(int irq) {assert(irq==28 && current->rpq_transfer);synchronized++;}
static void cancel_delayed_work_sync(struct work *work) {
 assert(work==&current->work && synchronized && current->rpq_transfer);cancelled++;}
static void dma_sync_single_for_device(void *device,dma_addr_t dma,unsigned bytes,int dir) {
 (void)device;assert(cancelled && dir==DMA_BIDIRECTIONAL);
 assert((dma==0x6000000 && bytes==SZ_128K) || (dma==0x6800000 && bytes==256*12288));synced++;}
static void put_unaligned_le32(u32 v,void *p) {memcpy(p,&v,4);}
static u32 get(const void *p) {u32 v;memcpy(&v,p,4);return v;}
static void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx) {assert(rx==current && !rx->rpq_transfer);woken++;}
static void write32(void *ctx,unsigned reg,u32 value) {
 (void)ctx;if(reg==0x10b0)assert(!(value&(BIT(2)|BIT(20))));
 else {assert(reg==0x30b0 && value==RTL_AX_IRQ_INDICATION);assert(cancelled && synced);}}
'''
test = r'''
int main(void) {
 unsigned char memory[65536]={};struct rtl_ax_io io={.write32=write32};
 struct rtl_ax_rx rx={.io=&io,.irq=28,.irq_mask=BIT(0)|BIT(2)|BIT(19)|BIT(20),
  .queues={{254,8190,0x6800000},{255,8191,0x6000000}},.pe_forward_dma=0x7000000};
 current=&rx;
 rtl_ax_rx_pe_rpq_start(&rx,memory);
 assert(rx.rpq_owned && !rx.rpq_transfer && rx.rpq_memory==memory && woken==1);
 assert(get(memory+0x2118)==255 && get(memory+0x211c)==8191);
 assert(get(memory+0x2110)==0x6000000 && get(memory+0x2120)==0x7000000);
#ifdef TEST_RXQ
 assert(get(memory+0x2140)==0x6800000 && get(memory+0x2144)==254);
 assert(get(memory+0x2148)==8190 && get(memory+0x214c)==254 && get(memory+0x2150)==254);
 assert(synced==2);
#else
 assert(synced==1);
#endif
}
'''
with tempfile.TemporaryDirectory(prefix='radio-rx-handoff-') as directory:
    path = Path(directory)
    for index, name in enumerate(('live-radio-pe-rpq-5g', 'live-radio-pe-rxq-5g')):
        source = (root / 'artifacts' / name / 'rx.c').read_text()
        start = source.index('void rtl_ax_rx_pe_rpq_start(')
        end = source.index('\n}\n', start) + 3
        body = source[start:end]
        # No draining is needed: an unfinished DMA tag transfers to the PE consumer.
        assert 'rtl_ax_rx_drain(' not in body
        c, binary = path / f'{index}.c', path / f'{index}'
        c.write_text(common + ('\n#define TEST_RXQ\n' if index else '') + body + test)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                        str(c), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=5)
print('RPQ/RXQ handoff: queued DMA consumer/tag transfers without synchronous tag-wait loop')
