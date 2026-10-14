#!/usr/bin/env python3
"""Check key/BA retirement closes PE admission before FE withdrawal fails."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/ni-test.h').read_text()
a = source.index('int rtl_ax_tx_ni_retire_peer(')
b = source.index('\nint rtl_ax_tx_ni_test(', a)
common = r'''

#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
#define READ_ONCE(p) (p)
#define WRITE_ONCE(p,v) ((p)=(v))
#define dma_wmb() ((void)0)
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define read_poll_timeout(op,val,cond,delay,timeout,sleep,args...) ({(void)(delay);(void)(timeout);(void)(sleep);(val)=op(args);(cond)?0:-ETIMEDOUT;})
#define readl_poll_timeout(address,val,cond,delay,timeout) ({(void)(address);(void)(delay);(void)(timeout);(val)=0;(cond)?0:-ETIMEDOUT;})
struct rtl_ax_rx { bool enabled[128]; };
struct rtl_ax_tx {struct {void *memory;}pe;struct {bool route_peers[128];void *route_dev,*vp;unsigned route_tim[128];}ni;bool peer_offloaded[128];struct rtl_ax_rx *rx;};
static unsigned calls;
static int ni_test_lock;
static u32 get_unaligned_le32(void *p) {u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p) {memcpy(p,&v,4);}
static bool rtl_ax_rx_uplink_active(struct rtl_ax_rx *rx,unsigned id) {return rx->enabled[id];}
static void rtl_ax_rx_uplink_disable(struct rtl_ax_rx *rx,unsigned id) {rx->enabled[id]=false;}
static int rtl9617c_wifi_peer_route(void *dev,void *mac,bool enable) {
 struct rtl_ax_tx *tx=dev;unsigned id=1;(void)mac;assert(!enable);
 assert(!get_unaligned_le32(tx->pe.memory+0x11a00+id*4) && !tx->rx->enabled[id]);calls++;return -ETIMEDOUT;
}
static int rtl_ax_tx_ni_route(struct rtl_ax_tx *tx,void *dev,unsigned id) {
 assert(!id && dev==tx);assert(!get_unaligned_le32(tx->pe.memory+0x11a04) && !tx->rx->enabled[1]);calls++;return -ETIMEDOUT;
}
static int rtl_ax_tx_pause(struct rtl_ax_tx *tx) {(void)tx;return 0;}
static u32 readl(void *p) {(void)p;return 0;}
static void rtl_ax_rx_uplink_retire(struct rtl_ax_rx *rx,unsigned id) {(void)rx;(void)id;}
static void rtl_ax_tx_open(struct rtl_ax_tx *tx) {(void)tx;}
static bool ni_peer_idle(struct rtl_ax_tx *tx,unsigned id) {(void)tx;(void)id;return true;}
static void ni_test_buffered(struct rtl_ax_tx *tx,unsigned id,bool set) {(void)tx;(void)id;(void)set;}
'''
test = r'''
int main(void) {
 unsigned char memory[131072]={};struct rtl_ax_rx rx={};struct rtl_ax_tx tx={.pe={memory},.rx=&rx};tx.ni.route_dev=&tx;
 for(unsigned other=0;other<2;other++){
  tx.ni.route_peers[1]=tx.peer_offloaded[1]=rx.enabled[1]=true;
  tx.ni.route_peers[2]=other;rx.enabled[2]=true;
  put_unaligned_le32(1,memory+0x11a04);put_unaligned_le32(1,memory+0x11a08);
  assert(rtl_ax_tx_ni_retire_peer(&tx,1)==-ETIMEDOUT);
  assert(!get_unaligned_le32(memory+0x11a04) && !rx.enabled[1]);
  assert(get_unaligned_le32(memory+0x11a08)==1 && rx.enabled[2]);
  assert(tx.ni.route_peers[1] && tx.peer_offloaded[1]);
 }
 assert(calls==2);puts("key/BA retirement: single-peer and shared-radio FE failures keep target admission closed; other peer unchanged");
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-ni-retirement-') as folder:
    folder=Path(folder);c,binary=folder/'retire.c',folder/'retire'
    c.write_text(common+source[a:b]+test)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',str(c),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
