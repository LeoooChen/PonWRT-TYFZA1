#!/usr/bin/env python3
"""Execute RPQ handoff and DFS epochs without transferring the ARM RXQ."""
from pathlib import Path
import re, subprocess, tempfile
root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(driver/'rx.c').read_text()
def function(name):
 m=re.search(r'^.*\b'+name+r'\([^;]*?\)\s*\{',source,re.M);a=m.start();b=source.index('{',a)+1;depth=1
 while depth:depth+=(source[b]=='{')-(source[b]=='}');b+=1
 return source[a:b]+'\n'
common=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;typedef uintptr_t dma_addr_t;
#define BIT(n) (1U<<(n))
#define DMA_BIDIRECTIONAL 3
#define RTL_AX_IRQ_INDICATION (BIT(29)|BIT(24))
#define RTL_AX_RX_IRQS (BIT(20)|BIT(19)|BIT(2)|BIT(0))
#define WRITE_ONCE(p,v) ((p)=(v))
#define READ_ONCE(p) (p)
#define smp_store_release(p,v) __atomic_store_n((p),(v),__ATOMIC_RELEASE)
#define dma_wmb() do{}while(0)
#define spin_lock_irqsave(p,f) do{(void)(p);(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(p);(void)(f);}while(0)
#include "rx-geometry.h"
struct device {int id;};
struct rtl_ax_rx_queue {u32 consumer,tag;dma_addr_t buffer_dma;u64 data_bytes;};
struct rtl_ax_io {void *ctx;u32 (*read32)(void *,u32);void (*write32)(void *,u32,u32);};
struct rtl_ax_rx {struct rtl_ax_rx_queue queues[2];struct device *pe_device;struct rtl_ax_io *io;void *rpq_memory;dma_addr_t pe_forward_dma;bool rpq_transfer,rpq_owned,running;u32 irq_mask,dfs_epoch[4];int irq_lock,irq,work;};
static unsigned cancels,syncs,wakes,dev_sync,cpu_sync,drains;
static u32 hw[2],mask,indication;
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static u32 read32(void *ctx,u32 reg){(void)ctx;assert(reg==0x1218||reg==0x121c);return hw[(reg-0x1218)/4];}
static void write32(void *ctx,u32 reg,u32 value){(void)ctx;if(reg==0x10b0)mask=value;else{assert(reg==0x30b0);indication=value;}}
static void synchronize_irq(int irq){assert(irq==28);syncs++;}
static void cancel_delayed_work_sync(int *work){(void)work;cancels++;}
static void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx){(void)rx;wakes++;}
static void dma_sync_single_for_device(struct device *dev,dma_addr_t dma,unsigned size,int dir){assert(dev->id==1&&dma==0x06000000&&size==256*1024&&dir==3);dev_sync++;}
static void dma_sync_single_for_cpu(struct device *dev,dma_addr_t dma,unsigned size,int dir){assert(dev->id==1&&dma==0x06000000&&size==256*1024&&dir==3);cpu_sync++;}
static void rtl_ax_rx_pe_drain(struct rtl_ax_rx *rx){u8 *mb=rx->rpq_memory+0x2000;u32 c=get_unaligned_le32(mb+296);assert(rx->rpq_transfer);if(c!=get_unaligned_le32(mb+292))put_unaligned_le32((c+1)&255,mb+296);drains++;}
'''
case=r'''
int main(void){
 u8 memory[131072]={0};u8 *mb=memory+0x2000;
 struct device dev={1};struct rtl_ax_io io={.read32=read32,.write32=write32};
 struct rtl_ax_rx rx={.pe_device=&dev,.io=&io,.pe_forward_dma=0x07000000,.irq=28,.running=true};
 rx.queues[0].consumer=200;rx.queues[0].tag=4095;rx.queues[1].consumer=29;rx.queues[1].tag=8191;rx.queues[1].buffer_dma=0x06000000;
 rx.dfs_epoch[1]=31|256;put_unaligned_le32(0xdeadbeef,mb+320);
 rtl_ax_rx_pe_rpq_start(&rx,memory);
 assert(rx.rpq_owned&&!rx.rpq_transfer&&rx.rpq_memory==memory&&mask==(RTL_AX_RX_IRQS&~BIT(2)));
 assert(indication==RTL_AX_IRQ_INDICATION&&syncs==1&&cancels==1&&dev_sync==1&&wakes==1);
 assert(get_unaligned_le32(mb+320)==0&&get_unaligned_le32(mb+272)==0x06000000);
 assert(get_unaligned_le32(mb+280)==29&&get_unaligned_le32(mb+284)==8191&&get_unaligned_le32(mb+288)==0x07000000);
 assert(rx.queues[0].consumer==200&&rx.queues[0].tag==4095);
 put_unaligned_le32(42,mb+280);put_unaligned_le32(5,mb+284);put_unaligned_le32(3,mb+292);
 put_unaligned_le32(17,mb+324);hw[0]=203<<16;hw[1]=44<<16;
 rtl_ax_rx_dfs_epoch(&rx);
 assert(rx.dfs_epoch[0]==(203|256)&&rx.dfs_epoch[1]==(44|256)&&rx.dfs_epoch[3]==(3|256));
 assert(get_unaligned_le32(mb+(RTL_AX_PE_DFS_EPOCH+1)*4)==(44|256));
 assert(!rtl_ax_rx_pe_rpq_stop(&rx));
 assert(!rx.rpq_owned&&!rx.rpq_transfer&&!rx.rpq_memory&&mask==RTL_AX_RX_IRQS);
 assert(drains==3&&cpu_sync==1&&cancels==2&&wakes==2);
 assert(get_unaligned_le32(mb+272)==0&&rx.queues[1].consumer==42&&rx.queues[1].tag==5);
 assert(rx.queues[0].consumer==200&&rx.queues[0].tag==4095);
 assert(!rtl_ax_rx_pe_rpq_stop(&rx)&&cpu_sync==1);
 rx.queues[0].data_bytes=(1ULL<<32)+123;
 assert(rtl_ax_rx_traffic_bytes(&rx)==123);
}
'''
with tempfile.TemporaryDirectory(prefix='rx-rpq-handoff-',dir=root/'artifacts') as tmp:
 p=Path(tmp);c=p/'case.c';c.write_text(common+''.join(function(n) for n in ['rtl_ax_rx_pe_rpq_start','rtl_ax_rx_pe_rpq_stop','rtl_ax_rx_dfs_epoch','rtl_ax_rx_traffic_bytes'])+case)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(driver),str(c),'-o',str(p/'case')],check=True)
 subprocess.run([str(p/'case')],check=True)
print('RPQ handoff preserves ARM RXQ cursors; RDU IRQ, DFS epochs and RX traffic accounting passed.')

monitor=(driver/'monitor.h').read_text()
assert 'ieee80211_hw_set(hw, RX_INLINE)' not in monitor
start=monitor.index('case IEEE80211_AMPDU_RX_START:')
stop=monitor.index('case IEEE80211_AMPDU_RX_STOP:',start)
end=monitor.index('\n\treturn -EOPNOTSUPP;\n}',stop)
assert 'rtl_ax_tx_ni_retire_peer' not in monitor[start:end]
txstop=monitor.index('case IEEE80211_AMPDU_TX_STOP_CONT:')
assert 'rtl_ax_tx_ni_retire_peer' in monitor[txstop:start]
print('RX BA changes preserve TX route; TX BA retirement remains enforced.')
print('ARM-only RX does not force inline Ethernet delivery that bypasses RPS.')
