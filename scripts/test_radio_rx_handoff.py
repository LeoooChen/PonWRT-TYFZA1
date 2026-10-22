#!/usr/bin/env python3
"""Check ARM/PE RXBD transfer, retained NI payloads and DFS epochs."""
from pathlib import Path
import re, subprocess, tempfile
root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(driver/'rx.c').read_text()
def function(name):
 a=re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{',source,re.M).start();b=source.index('{',a)+1;depth=1
 while depth:depth+=(source[b]=='{')-(source[b]=='}');b+=1
 return source[a:b]+'\n'
common=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint32_t __le32;typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef int32_t s32;typedef uintptr_t dma_addr_t;
#define BIT(n) (1U<<(n))
#define DMA_BIDIRECTIONAL 3
#define RTL_AX_IRQ_INDICATION (BIT(29)|BIT(24))
#define RTL_AX_RX_IRQS (BIT(20)|BIT(19)|BIT(2)|BIT(0))
#define PE_PROBE_MAILBOX 0x2000
#define WRITE_ONCE(p,v) ((p)=(v))
#define READ_ONCE(p) (p)
#define smp_store_release(p,v) (*(p)=(v))
#define dma_wmb() ((void)0)
#define spin_lock_irqsave(p,f) do{(void)(p);(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(p);(void)(f);}while(0)
#include "rx-geometry.h"
struct rtl_ax_rx_buffer {u8 *data;dma_addr_t dma;};
struct rtl_ax_rx_queue {u32 consumer,tag;dma_addr_t buffer_dma,descriptor_dma,addresses_dma;struct rtl_ax_rx_buffer payloads[RTL_AX_RX_BUFFERS];u16 slot_ids[256];unsigned buffer_bytes;};
struct rtl_ax_io {void *ctx;u32 (*read32)(void *,u32);void (*write32)(void *,u32,u32);void (*write16)(void *,u32,u16);};
struct pe_probe {void *memory;dma_addr_t dma;bool attached;};
struct rtl_ax_rx {struct pe_probe pe;u32 pe_command;struct rtl_ax_rx_queue queues[2];void *pe_device,*rpq_memory;struct rtl_ax_io *io;dma_addr_t pe_forward_dma;bool rpq_transfer,rpq_owned,running,pool_initialized;u32 irq_mask,dfs_epoch[4],pe_rdu_seen[2],uplink_wifi_id;int irq_lock,irq,work;};
static int pe_probe_start(struct pe_probe *p,const u32 *program,unsigned bytes){assert(program&&bytes==4);p->attached=true;return 0;}
static int pe_probe_stop(struct pe_probe *p){p->attached=false;return 0;}
static unsigned syncs,cancels,device_syncs,cpu_syncs,delivered;
static u32 mask,hw[2],tags[RTL_AX_RX_BUFFERS];
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void synchronize_irq(int irq){assert(irq==28);syncs++;}
static void cancel_delayed_work_sync(int *p){(void)p;cancels++;}
static void dma_sync_single_for_device(void *d,dma_addr_t a,unsigned n,int dir){(void)d;assert(dir==3);assert((n==12288&&a>=0x08000000)||(n==256*1024&&a==0x06000000));device_syncs++;}
static void dma_sync_single_for_cpu(void *d,dma_addr_t a,unsigned n,int dir){(void)d;assert(dir==3&&n==256*1024&&a==0x06000000);cpu_syncs++;}
static void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx){assert(!rx->rpq_transfer);}
static void rtl_ax_rx_pe_drain(struct rtl_ax_rx *rx){u8 *m=rx->rpq_memory+0x2000;put_unaligned_le32(get_unaligned_le32(m+292),m+296);}
static void rtl_ax_rx_pe_rxq_drain(struct rtl_ax_rx *rx){u8 *m=rx->rpq_memory+0x2000;put_unaligned_le32(get_unaligned_le32(m+376),m+380);}
static void rtl_ax_rx_uplink_sync(struct rtl_ax_rx *rx){assert(rx->rpq_memory);}
static void rtl_ax_rx_rdu_sync(struct rtl_ax_rx *rx){assert(rx->rpq_memory);}
static void rtl_ax_rx_pe_deliver(struct rtl_ax_rx *rx,unsigned slot){put_unaligned_le32(BIT(30),rx->pe.memory+RTL_AX_RX_WORK_STATUS+slot*4);delivered++;}
static u32 read32(void *ctx,u32 reg){(void)ctx;assert(reg==0x1218||reg==0x121c);return hw[(reg-0x1218)/4];}
static void write32(void *ctx,u32 reg,u32 v){(void)ctx;if(reg==0x10b0)mask=v;else assert(reg==0x30b0&&v==RTL_AX_IRQ_INDICATION);}
static void write16(void *ctx,u32 reg,u16 v){(void)ctx;assert(reg==0x1218);hw[0]=(hw[0]&0xffff0000)|v;}
'''
case=r'''
int main(void){
 static u8 memory[131072],tx_memory[131072];u8 *mb=memory+0x2000;const u32 program=0;
 struct rtl_ax_io io={.read32=read32,.write32=write32,.write16=write16};
 struct rtl_ax_rx rx={.pe={.memory=memory,.dma=0x04000000},.io=&io,.irq=28,.running=true,.pe_forward_dma=0x07000000};
 struct rtl_ax_rx_queue *q=&rx.queues[0];
 q->consumer=254;q->tag=8190;q->buffer_bytes=12288;q->descriptor_dma=0x07100000;q->addresses_dma=0x07200000;
 for(unsigned i=0;i<514;i++){q->payloads[i].data=(u8 *)&tags[i];q->payloads[i].dma=0x08000000+((i*73)%514)*16384;}
 for(unsigned i=0;i<256;i++)q->slot_ids[i]=i;
 rx.queues[1].consumer=29;rx.queues[1].tag=8191;rx.queues[1].buffer_dma=0x06000000;
 assert(!rtl_ax_rx_pe_rpq_start(&rx,tx_memory,0x04200000,&program,4));
 assert(syncs==1&&cancels==1&&device_syncs==515&&rx.rpq_owned&&!rx.rpq_transfer&&mask==0);
 assert(get_unaligned_le32(mb+320)==q->addresses_dma&&get_unaligned_le32(mb+134*4)==q->descriptor_dma);
 assert(get_unaligned_le32(mb+324)==254&&get_unaligned_le32(mb+328)==8190);
 assert(get_unaligned_le32(mb+RTL_AX_RX_HW_PRODUCER*4)==254);
 assert(get_unaligned_le32(tx_memory+0x2000+RTL_AX_RX_BACKEND_CONTEXT*4)==0x04002000);
 assert(get_unaligned_le32(mb+177*4)==258);
 for(unsigned i=0;i<256;i++)assert(get_unaligned_le32(memory+RTL_AX_RX_SLOT_MAP+i*4)==i);
 for(unsigned i=0;i<258;i++)assert(get_unaligned_le32(memory+RTL_AX_RX_IDLE_IDS+i*4)==256+i);
 /* PE has swapped the first spare into RXBD17, returning its old payload. */
 put_unaligned_le32(256,memory+RTL_AX_RX_SLOT_MAP+17*4);
 put_unaligned_le32(17,memory+RTL_AX_RX_IDLE_IDS);
 /* Two consumed NI tail buffers stay immutable across an ARM interval. */
 put_unaligned_le32(256,mb+177*4);
 put_unaligned_le32(513,memory+RTL_AX_RX_NI_TAIL);put_unaligned_le32(100,memory+RTL_AX_RX_NI_TAIL+4);
 put_unaligned_le32(514,memory+RTL_AX_RX_NI_TAIL+8);put_unaligned_le32(99,memory+RTL_AX_RX_NI_TAIL+12);
 put_unaligned_le32(100,mb+364);put_unaligned_le32(230,mb+324);put_unaligned_le32(8191,mb+328);
 hw[0]=233<<16;hw[1]=44<<16;
 rtl_ax_rx_dfs_epoch(&rx);
 assert(rx.dfs_epoch[0]==(233|256)&&get_unaligned_le32(mb+172*4)==(233|256));
 assert(!rtl_ax_rx_pe_rpq_stop(&rx));
 assert(!rx.rpq_owned&&!rx.rpq_memory&&!rx.rpq_transfer&&mask==RTL_AX_RX_IRQS);
 assert(!get_unaligned_le32(tx_memory+0x2000+RTL_AX_RX_BACKEND_CONTEXT*4));
 assert(q->consumer==230&&q->tag==8191&&q->slot_ids[17]==256&&cpu_syncs==1);
 assert(get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL)==513&&get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL+8)==514);
 assert(!rtl_ax_rx_pe_rpq_start(&rx,tx_memory,0x04200000,&program,4));
 assert(get_unaligned_le32(mb+177*4)==256&&get_unaligned_le32(memory+RTL_AX_RX_NI_TAIL)==513);
 /* A not-yet-consumed NI submission prevents ownership transfer back. */
 put_unaligned_le32(257,memory+RTL_AX_RX_WORK_IDS);
 put_unaligned_le32(511,memory+RTL_AX_RX_IDLE_IDS+4);put_unaligned_le32(255,mb+177*4);
 put_unaligned_le32(1,mb+RTL_AX_RX_WORK_PROCESS*4);put_unaligned_le32(1,mb+RTL_AX_RX_WORK_WRITE*4);
 put_unaligned_le32(BIT(31)|101,memory+RTL_AX_RX_WORK_STATUS);
 assert(rtl_ax_rx_pe_rpq_stop(&rx)==-EBUSY&&rx.rpq_owned&&rx.rpq_memory==tx_memory);
 assert(delivered==0);
 /* A sorted frame can own NI beyond the contiguous parser cursor. */
 put_unaligned_le32(0,mb+RTL_AX_RX_WORK_PROCESS*4);
 put_unaligned_le32(2,mb+RTL_AX_RX_WORK_WRITE*4);
 put_unaligned_le32(0,memory+RTL_AX_RX_WORK_STATUS);
 put_unaligned_le32(258,memory+RTL_AX_RX_WORK_IDS+4);
 put_unaligned_le32(BIT(31)|101,memory+RTL_AX_RX_WORK_STATUS+4);
 put_unaligned_le32(254,mb+177*4);
 assert(rtl_ax_rx_pe_rpq_stop(&rx)==-EBUSY&&delivered==0);
 put_unaligned_le32(103,mb+364);
 assert(!rtl_ax_rx_pe_rpq_stop(&rx)&&delivered==1);
 assert(get_unaligned_le32(mb+RTL_AX_RX_WORK_PROCESS*4)==2);
 assert(get_unaligned_le32(mb+RTL_AX_RX_WORK_READ*4)==2);
}
'''
with tempfile.TemporaryDirectory(prefix='rx-pe-handoff-',dir=root/'artifacts') as tmp:
 p=Path(tmp);c=p/'case.c'
 names=['rtl_ax_rx_idle_put','rtl_ax_rx_tail_recycle','rtl_ax_rx_work_recycle','rtl_ax_rx_pe_rpq_start','rtl_ax_rx_pe_rpq_stop','rtl_ax_rx_dfs_epoch']
 c.write_text(common+''.join(function(n) for n in names)+case)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(driver),str(c),'-o',str(p/'case')],check=True)
 subprocess.run([str(p/'case')],check=True)
print('RX DMA cursors, address directory, separate PE0/PE1 contexts, NI tail retention, restart, DFS and pending-NI handoff fence passed.')
monitor=(driver/'monitor.h').read_text()
assert 'ieee80211_hw_set(hw, RX_INLINE)' in monitor
start=monitor.index('case IEEE80211_AMPDU_RX_START:');end=monitor.index('\n\treturn -EOPNOTSUPP;\n}',start)
assert monitor[start:end].count('rtl_ax_tx_rx_retire')==2
assert 'rtl_ax_tx_ni_retire_peer' not in monitor[start:end]
print('RX BA invalidates the receive shortcut while preserving the PE TX route.')
