#!/usr/bin/env python3
"""Execute real TX pause/open callbacks with an active PPDU and a live H2C lane."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
paths = [root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c',
         root / 'artifacts/live-radio-pe-ni/tx.c',
         root / 'artifacts/live-radio-pe-rpq-5g/tx.c']


def function(source, name):
    start = re.search(r'^(?:int|void) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8; typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_TX_ENTRIES 256
#define dma_wmb() ((void)0)
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define read_poll_timeout(op,var,condition,delay,timeout,sleep,args...) ({var=op(args);(condition)?0:-ETIMEDOUT;})
static inline u32 get_unaligned_le32(const void *p) {u32 v;memcpy(&v,p,4);return v;}
static inline void put_unaligned_le32(u32 v,void *p) {memcpy(p,&v,4);}
struct rtl_ax_io {void *ctx;u32(*read32)(void*,unsigned);u8(*read8)(void*,unsigned);
 void(*write32)(void*,unsigned,u32);void(*delay_us)(void*,unsigned);};
struct rtl_ax_tx {const struct rtl_ax_io *io;int lock;bool running,paused,pe_active;
 bool checksum_enabled,queues_stopped;unsigned occupied,queue_wakes,pe_producer,pe_command;
 u32 scheduler_saved;void *hw;struct {void *memory;} pe;};
struct hardware {u32 dma,scheduler;unsigned delays;bool active,stuck;};
static u32 read32(void *p,unsigned reg) {struct hardware *h=p;
 return reg==0x1010?h->dma:reg==0xc398?h->scheduler:reg==0x8830?1:0;}
static u8 read8(void *p,unsigned reg) {struct hardware *h=p;assert(reg==0xc6ec);
 return h->active?BIT(7):0;}
static void write32(void *p,unsigned reg,u32 value) {struct hardware *h=p;
 if(reg==0x1010) {assert(!h->active);h->dma=value;} else {assert(reg==0xc398);h->scheduler=value;}}
static void delay(void *p,unsigned usec) {struct hardware *h=p;assert(usec==30);
 if(!h->stuck && !h->scheduler && !(h->dma & (BIT(8)|BIT(19))) && ++h->delays==2)h->active=false;}
static void rtl_ax_update32(const struct rtl_ax_io *io,unsigned reg,u32 clear,u32 set) {
 io->write32(io->ctx,reg,(io->read32(io->ctx,reg)&~clear)|set);}
static void ieee80211_wake_queues(void *hw) {(void)hw;}
'''
test = r'''
int main(void) {
 struct hardware h={.dma=0xbff00,.scheduler=0x20021};
 struct rtl_ax_io io={&h,read32,read8,write32,delay};
 unsigned char memory[65536]={};struct rtl_ax_tx tx={.io=&io,.pe={memory}};
 rtl_ax_tx_open(&tx);
 assert(tx.running && !(h.dma & BIT(19)) && !(h.dma & BIT(8)));
 h.scheduler=0x20021;h.active=true;
 assert(!rtl_ax_tx_pause(&tx));
 assert(h.delays==2 && !h.scheduler && (h.dma&BIT(8)) && !(h.dma&BIT(19)));
 assert(!tx.running && tx.paused && tx.scheduler_saved==0x20021);
 assert(!rtl_ax_tx_pause(&tx));
 rtl_ax_tx_open(&tx);
 assert(h.scheduler==(0x20021|BIT(8)) && !(h.dma&BIT(8)) && !(h.dma&BIT(19)));
 h.active=h.stuck=true;h.delays=0;
 assert(rtl_ax_tx_pause(&tx)==-ETIMEDOUT);
 assert(!h.scheduler && !(h.dma&BIT(8)) && !(h.dma&BIT(19)));
#ifdef TEST_NI_CREDIT
 h.active=false;tx.pe_active=true;tx.queues_stopped=true;tx.occupied=49;
 put_unaligned_le32(1,memory+PE_PROBE_MAILBOX+96);
 rtl_ax_tx_open(&tx);assert(tx.queues_stopped && !tx.queue_wakes);
 tx.occupied=48;rtl_ax_tx_open(&tx);assert(!tx.queues_stopped && tx.queue_wakes==1);
#endif
 puts("TX pause: active PPDU drains before DMA stop; H2C stays live; startup opens H2C");
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-tx-pause-') as temp:
    for index, path in enumerate(paths):
        source = path.read_text()
        mask = re.search(r'^#define RTL_AX_TX_STOP_MASK.*$', source, re.M)[0]
        c, binary = Path(temp) / f'{index}.c', Path(temp) / f'{index}'
        c.write_text(common + ('\n#define TEST_NI_CREDIT\n' if index == 2 else '') + mask + '\n' + function(source, 'rtl_ax_tx_pause') +
                     '\n' + function(source, 'rtl_ax_tx_open') + test)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                        str(c), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
