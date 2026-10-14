#!/usr/bin/env python3
"""Execute the TX release boundary when NI or PE still owns DMA allocations."""
from pathlib import Path
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()
start = re.search(r'^int rtl_ax_tx_free\(', source, re.M).start()
end = source.index('\n}\n', start) + 3
body = source[start:end]
common = r'''
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#define RTL_AX_TX_ENTRIES 256
#define RTL_AX_TX_PAGES 384
#define RTL_AX_TX_WP_BYTES 128
#define RTL_AX_TX_RINGS 6
typedef uint64_t dma_addr_t;
struct device{};struct ieee80211_hw{};struct sk_buff{};
struct pe{void *memory;};struct work{};
struct rtl_ax_tx{struct pe pe;struct {struct work ps_work;} ni;
 struct device *dma_device;struct {struct sk_buff *skb;} slots[256];
 void *headers,*pages,*ring;dma_addr_t headers_dma,pages_dma,ring_dma;};
static int ni_result,pe_result;
static unsigned ni_calls,pe_calls,irq_releases,work_cancels,DMA_frees,PE_frees,object_frees;
static int ni_test_remove(struct rtl_ax_tx *tx){(void)tx;ni_calls++;return ni_result;}
static void pe_event_release(struct rtl_ax_tx *tx){(void)tx;irq_releases++;}
static void cancel_work_sync(struct work *work){(void)work;work_cancels++;}
static int pe_probe_stop(struct pe *pe){(void)pe;pe_calls++;return pe_result;}
static void rtl_ax_tx_unmap(struct rtl_ax_tx *tx,void *slot){(void)tx;(void)slot;assert(0);}
static void ieee80211_free_txskb(struct ieee80211_hw *hw,struct sk_buff *skb){(void)hw;(void)skb;assert(0);}
static void dma_free_coherent(struct device *dev,unsigned n,void *p,dma_addr_t dma){
 (void)dev;(void)n;(void)dma;assert(p);DMA_frees++;}
static void pe_probe_free(struct pe *pe){assert(pe->memory);PE_frees++;}
static void kfree(void *p){assert(p);object_frees++;}
'''
case = r'''
int main(void){
 struct rtl_ax_tx tx={.pe={(void*)1},.headers=(void*)2,.pages=(void*)3,.ring=(void*)4};
 ni_result=-EBUSY;
 assert(rtl_ax_tx_free(&tx,NULL)==-EBUSY);
 assert(ni_calls==1 && pe_calls==0 && DMA_frees==0 && PE_frees==0 && object_frees==0);
 assert(irq_releases==1 && work_cancels==1);
 ni_result=0;pe_result=-ETIMEDOUT;
 assert(rtl_ax_tx_free(&tx,NULL)==-ETIMEDOUT);
 assert(ni_calls==2 && pe_calls==1 && DMA_frees==0 && PE_frees==0 && object_frees==0);
 assert(irq_releases==2 && work_cancels==2);
 pe_result=0;
 assert(rtl_ax_tx_free(&tx,NULL)==0);
 assert(DMA_frees==3 && PE_frees==1 && object_frees==1);
 assert(irq_releases==3 && work_cancels==3);
}
'''
out = ROOT / 'artifacts/pe-stop66'
(out / 'release.c').write_text(common + body + case)
subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(out / 'release.c'), '-o', str(out / 'release')], check=True)
subprocess.run([str(out / 'release')], check=True)
print(json.dumps(dict(actual_TX_release_executed=True, NI_busy_preserves_DMA=True,
                      PE_stop_timeout_preserves_DMA=True, ARM_callbacks_removed_on_each_exit=True,
                      successful_drain_releases_allocations=True,
                      scope='Current TX release function with controlled NI/PE completion and DMA/IRQ callbacks; not a hardware teardown test.'), indent=2))
