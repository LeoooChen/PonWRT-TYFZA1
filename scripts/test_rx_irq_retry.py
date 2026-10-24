#!/usr/bin/env python3
"""Run actual RX work/IRQ handlers with RDU arriving while interrupts are masked."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx.c'
parser = argparse.ArgumentParser()
parser.add_argument('--revision')
args = parser.parse_args()
source = (subprocess.check_output(['git', 'show', f'{args.revision}:{relative}'], cwd=root, text=True)
          if args.revision else (root / relative).read_text())


def function(name):
    at = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', at) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[at:end]


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;typedef int irqreturn_t;
#define BIT(n) (1U<<(n))
#define IRQ_NONE 0
#define IRQ_HANDLED 1
#define RTL_AX_IRQ_INDICATION (BIT(29)|BIT(24))
#define READ_ONCE(x) (x)
#define smp_load_acquire(p) (*(p))
#define dev_err(...) ((void)0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct work_struct {int unused;};
struct delayed_work {struct work_struct work;};
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
struct rtl_ax_io {void *ctx;u32 (*read32)(void *,u32);void (*write32)(void *,u32,u32);};
struct rtl_ax_rx {
 bool running,rpq_transfer,rpq_owned;void *rpq_memory;struct {u8 *memory;} pe;
 struct delayed_work work;unsigned irq_lock;const struct rtl_ax_io *io;
 u32 irq_mask,halt_error;struct {unsigned unavailable;} queues[2];unsigned interrupts;
};
static u32 registers[0x4000/4];
static struct rtl_ax_rx *active;
static unsigned scheduled,progress,writes;
static bool inject,ready;
static void *system_wq;
#define spin_lock_irqsave(p,f) do{assert(!*(p));*(p)=1;(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{assert(*(p));*(p)=0;(void)(f);}while(0)
static u32 read32(void *ctx,u32 reg){assert(ctx==active);return registers[reg/4];}
static void write32(void *ctx,u32 reg,u32 value){
 assert(ctx==active&&active->irq_lock);writes++;
 if(reg==0x10b4){registers[reg/4]&=~value;if(value&BIT(19))ready=true;}
 else registers[reg/4]=value;
}
static u32 get_unaligned_le32(const void *p){u32 value;memcpy(&value,p,4);return value;}
static void mod_delayed_work(void *queue,struct delayed_work *work,unsigned delay){
 assert(work==&active->work&&delay==0&&active->running&&active->irq_lock);scheduled++;
}
static bool rtl_ax_rx_drain(struct rtl_ax_rx *rx,unsigned number){
 if(number)return false;
 if(inject){registers[0x10b4/4]|=BIT(19)|BIT(7);inject=false;}
 if(!ready)return true;
 progress++;return false;
}
static void rtl_ax_rx_pe_drain(struct rtl_ax_rx *rx){}
static void rtl_ax_rx_pe_rxq_drain(struct rtl_ax_rx *rx){}
static void rtl_ax_rx_uplink_sync(struct rtl_ax_rx *rx){}
static void rtl_ax_rx_rdu_sync(struct rtl_ax_rx *rx){}
'''

checks = r'''
int main(void){
 u8 memory[0x3000]={0};struct rtl_ax_rx rx={.running=true,.irq_mask=BIT(0)|BIT(2)|BIT(19)|BIT(20)};
 struct rtl_ax_io io={.ctx=&rx,.read32=read32,.write32=write32};rx.io=&io;active=&rx;
 rx.pe.memory=rx.rpq_memory=memory;
 registers[0x30b4/4]=BIT(29);registers[0x10b4/4]=BIT(0);
 assert(rtl_ax_rx_interrupt(28,&rx)==IRQ_HANDLED && !registers[0x30b0/4]);
 assert(rx.interrupts==1 && !rx.queues[0].unavailable);
 inject=true;scheduled=0;rtl_ax_rx_work(&rx.work.work);
 assert(scheduled==1 && !progress);
 /* An unready tag must not hide the RDU that arrived during the drain. */
 assert(registers[0x30b0/4]==RTL_AX_IRQ_INDICATION);
 assert(rtl_ax_rx_interrupt(28,&rx)==IRQ_HANDLED);
 assert(rx.queues[0].unavailable==1 && registers[0x10b4/4]==BIT(7));
 scheduled=0;rtl_ax_rx_work(&rx.work.work);
 assert(progress==1 && !scheduled && registers[0x30b0/4]==RTL_AX_IRQ_INDICATION);
 /* PE ownership leaves its RDU accounting alone. */
 rx.rpq_owned=true;rx.irq_mask=0;registers[0x10b4/4]=BIT(19);
 rtl_ax_rx_work(&rx.work.work);
 assert(registers[0x10b4/4]==BIT(19) && rx.queues[0].unavailable==1);
 /* Closing still wins over both work rearming and a late IRQ. */
 rx.running=false;registers[0x30b0/4]=0;unsigned saved=writes;
 rtl_ax_rx_work(&rx.work.work);assert(rtl_ax_rx_interrupt(28,&rx)==IRQ_NONE);
 assert(writes==saved && !registers[0x30b0/4]);
}
'''

with tempfile.TemporaryDirectory(prefix='rx-irq-retry-') as directory:
    path = Path(directory)
    program = common + '\n'.join(function(n) for n in ('rtl_ax_rx_work', 'rtl_ax_rx_interrupt')) + checks
    (path / 'test.c').write_text(program)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
print('RX IRQ: late RDU,unready-tag retry,W1C accounting,PE ownership and close passed')
