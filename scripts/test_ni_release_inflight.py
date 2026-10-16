#!/usr/bin/env python3
"""Run native NI release with empty EPPs but an outstanding aggregate WP."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h'
source = path.read_text()
start = source.index('static int ni_test_release(')
end = source.index('\nstatic int ni_test_prepare(', start)
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_TX_RINGS 6
#define SZ_256K 262144
struct rtl_ax_tx {
 void *dma_device,*rx;
 unsigned producer[6];
 struct {void *memory;} pe;
 struct {bool active,vp_active;void *vp,*dma,*regs,*test_memory;
  unsigned cpu,vp_index,vp_saved[5],test_dma;int ps_work;} ni;
};
static unsigned released,stopped,restored;
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static u32 readl(const void *p){return get_unaligned_le32(p);}
static void writel(u32 v,void *p){put_unaligned_le32(v,p);}
#define readl_poll_timeout(a,v,c,d,t) ({(void)(d);(void)(t);v=readl(a);(c)?0:-ETIMEDOUT;})
static bool ni_epps_empty(struct rtl_ax_tx *tx){(void)tx;return true;}
static int rtl_ax_tx_pause(struct rtl_ax_tx *tx){(void)tx;stopped++;return 0;}
static int rtl_ax_rx_pe_rpq_stop(void *rx){(void)rx;return 0;}
static void pe_event_release(struct rtl_ax_tx *tx){(void)tx;}
static void cancel_work_sync(void *work){(void)work;}
static void dma_wmb(void){}
static void ni_restore_registers(struct rtl_ax_tx *tx){(void)tx;restored++;}
static void rtl_ax_tx_open(struct rtl_ax_tx *tx){(void)tx;}
static void dma_free_coherent(void *dev,unsigned size,void *memory,unsigned dma){(void)dev;(void)size;(void)memory;(void)dma;released++;}
static void iounmap(void *p){(void)p;released++;}
'''
test = r'''
int main(void){
 unsigned char memory[0x20000]={0},registers[0x10000]={0},dma[0x1000]={0};
 struct rtl_ax_tx tx={.pe={memory},.ni={.active=true,.cpu=3,.regs=registers,.dma=dma}};
 put_unaligned_le32(1,memory+0x2078);
 put_unaligned_le32(0x6000000,memory+0xc400);
 int ret=ni_test_release(&tx);
#ifdef EXPECT_OLD
 assert(ret==0 && !tx.ni.active && released==2 && restored==1);
 assert(get_unaligned_le32(memory+0xc400)==0x6000000);
 puts("REPRODUCED: release succeeded and unmapped NI with an unrecycled WP/BID");
#else
 assert(ret==-EBUSY && tx.ni.active && !released && !stopped && !restored);
 assert(get_unaligned_le32(memory+0xc400)==0x6000000);
 /* A completed WP whose last BID still awaits EQ credit must also remain. */
 put_unaligned_le32(BIT(31),memory+0xc404);
 assert(ni_test_release(&tx)==-EBUSY && !released);
 /* Actual RPQ/EQ completion permits normal release. */
 put_unaligned_le32(0,memory+0x2078);put_unaligned_le32(0,memory+0xc400);
 assert(!ni_test_release(&tx) && !tx.ni.active && released==2 && restored==1);
 puts("NI release retains in-flight or partially recycled WPs; teardown follows only after their last BID returns");
#endif
}
'''
with tempfile.TemporaryDirectory(prefix='ni-release-', dir=ROOT / 'artifacts') as tmp:
    tmp = Path(tmp)
    (tmp / 'test.c').write_text(common + source[start:end] + test)
    command = ['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined']
    if '--before' in sys.argv: command.append('-DEXPECT_OLD')
    subprocess.run(command + [str(tmp / 'test.c'), '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True)
