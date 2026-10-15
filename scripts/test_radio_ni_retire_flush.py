#!/usr/bin/env python3
"""Run production NI retirement with a sleeping MAC holding submitted WPs."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'


def function(source, name):
    start = re.search(r'^(?:static )?(?:inline )?(?:int|void|bool) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define PE_PROBE_MAILBOX 0x2000
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define dma_wmb() ((void)0)
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define RTL_AX_H2C_CMD 0
#define RTL_AX_AP_TRANSPORT_H
static void rtl_ax_store32(void *p,u32 v) {memcpy(p,&v,4);}
struct net_device {int unused;};
struct pci_dev {void *radio;};
struct rtl_ax_rx {bool enabled[128];};
struct rtl_ax_tx {
 bool pe_active,paused,peer_authorized[128],peer_offloaded[128];
 unsigned occupied,ni_reports,ni_failed;
 struct pci_dev *pdev;
 struct rtl_ax_rx *rx;
 struct {void *memory;} pe;
 struct {bool active,route_peers[128];void *vp;struct net_device *route_dev;
  unsigned route_macid,route_tim[128];int ps_work,cpu;} ni;
};
struct rtl_ax_ap {struct rtl_ax_tx *tx;};
static struct rtl_ax_tx *current;
static unsigned drop_mask[128],pending[128],withdrawn[128],drop_calls;
static int ni_test_lock,fail_drop,fail_withdraw;
static bool withhold_rpq;
static u32 get_unaligned_le32(const void *p) {u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p) {memcpy(p,&v,4);}
static void *pci_get_drvdata(struct pci_dev *p) {return p->radio;}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,
 unsigned fn,const u8 *data,unsigned bytes,bool ack) {
 assert(r->tx==current && q==0 && cat==1 && cls==9 && fn==27 && bytes==24 && !ack);
 unsigned word=get_unaligned_le32(data),id=(word>>8)&255,ac=word&255;
 assert(id==1 && ac<4 && withdrawn[id]);
 assert(!get_unaligned_le32(current->pe.memory+0x11a00+id*4));
 assert(get_unaligned_le32(current->pe.memory+0x20f8)==1 || get_unaligned_le32(current->pe.memory+0x20f8)==128);
 assert(!current->paused);
 drop_calls++;
 if(fail_drop) return -EIO;
 drop_mask[id]|=BIT(ac);return 0;
}
static void tick(void) {
 u32 total=0;
 for(unsigned id=1;id<128;id++) {
  if(drop_mask[id]==15 && !withhold_rpq) pending[id]=0;
  total+=pending[id];
 }
 put_unaligned_le32(total,current->pe.memory+0x2078);
}
#define read_poll_timeout(op,val,cond,delay,timeout,sleep,args...) ({ \
 (void)(delay);(void)(timeout);(void)(sleep);tick();val=op(args);(cond)?0:-ETIMEDOUT;})
#define readl_poll_timeout(address,val,cond,delay,timeout) ({ \
 (void)(address);(void)(delay);(void)(timeout);val=0;(cond)?0:-ETIMEDOUT;})
#define wait_event_timeout(w,cond,t) ((void)(t),(cond)?1:0)
#define msecs_to_jiffies(v) (v)
static bool rtl_ax_rx_uplink_active(struct rtl_ax_rx *rx,unsigned id) {return rx->enabled[id];}
static void rtl_ax_rx_uplink_disable(struct rtl_ax_rx *rx,unsigned id) {rx->enabled[id]=false;}
static void rtl_ax_rx_uplink_retire(struct rtl_ax_rx *rx,unsigned id) {(void)rx;(void)id;}
static int rtl9617c_wifi_peer_route(struct net_device *dev,void *mac,unsigned route) {
 (void)dev;unsigned id=*(u8 *)mac;
 assert(!route && !get_unaligned_le32(current->pe.memory+0x11a00+id*4));
 if(fail_withdraw) return -EIO;
 withdrawn[id]=1;return 0;
}
static int uplink_release(struct rtl_ax_tx *tx) {(void)tx;return 0;}
static int rtl_ax_tx_pause(struct rtl_ax_tx *tx) {tx->paused=true;return 0;}
static void rtl_ax_tx_open(struct rtl_ax_tx *tx) {tx->paused=false;}
static bool ni_epps_empty(struct rtl_ax_tx *tx) {(void)tx;return true;}
static bool ni_peer_idle(struct rtl_ax_tx *tx,unsigned id) {(void)tx;return !pending[id];}
static u32 readl(void *p) {(void)p;return 0;}
static void ni_test_buffered(struct rtl_ax_tx *tx,unsigned id,unsigned mask) {(void)tx;(void)id;(void)mask;}
static void cancel_work_sync(void *p) {(void)p;}
static void dev_put(struct net_device *dev) {(void)dev;}
static void dev_hold(struct net_device *dev) {(void)dev;}
static void schedule_work(void *p) {(void)p;}
static int ni_test_prepare(struct rtl_ax_tx *tx,unsigned id) {(void)tx;(void)id;return 0;}
static int ni_test_release(struct rtl_ax_tx *tx) {assert(!pending[1]);tx->ni.active=false;return 0;}
int rtl_ax_mac_drop_peer(struct pci_dev *pdev,unsigned macid);
'''

test = r'''
int main(void) {
 for(unsigned other=0;other<2;other++) for(unsigned failure=0;failure<4;failure++) {
  u8 memory[0x20000]={0};struct rtl_ax_rx rx={0};
  struct net_device dev={0};struct pci_dev pdev={0};
  struct rtl_ax_tx tx={.pe_active=true,.pe={memory},.pdev=&pdev,.rx=&rx};
  struct rtl_ax_ap radio={.tx=&tx};pdev.radio=&radio;current=&tx;
  memset(pending,0,sizeof(pending));memset(drop_mask,0,sizeof(drop_mask));memset(withdrawn,0,sizeof(withdrawn));
  drop_calls=0;fail_drop=failure==1;withhold_rpq=failure==2;fail_withdraw=failure==3;
  tx.ni.active=true;tx.ni.route_dev=&dev;tx.ni.route_macid=1;
  tx.ni.route_peers[1]=tx.peer_offloaded[1]=rx.enabled[1]=true;
  tx.ni.route_peers[2]=other;rx.enabled[2]=other;
  memory[0xe408]=1;memory[0xe410]=2;
  put_unaligned_le32(1,memory+0x11a04);put_unaligned_le32(other,memory+0x11a08);
  pending[1]=5;tick();
  int ret=rtl_ax_tx_ni_retire_peer(&tx,1);
  if(!failure) {
   assert(ret==0 && drop_calls==4 && pending[1]==0);
   assert(!tx.ni.route_peers[1] && !tx.peer_offloaded[1]);
  } else {
   assert(ret==(failure==2?-ETIMEDOUT:-EIO) && pending[1]==5);
   assert(tx.ni.route_peers[1] && tx.peer_offloaded[1]);
   assert(drop_calls==(failure==3?0:failure==1?1:4));
  }
  assert(!get_unaligned_le32(memory+0x11a04) && !rx.enabled[1]);
  assert(get_unaligned_le32(memory+0x11a08)==other && rx.enabled[2]==other && !drop_mask[2]);
 }
 puts("NI retirement: drop sleeping hardware WPs before RPQ wait; failed commands or missing RPQ retain ownership; other peer untouched");
}
'''

ni = (driver / 'ni-test.h').read_text()
pci = (driver / 'pci.c').read_text()
drop = function(pci, 'rtl_ax_mac_drop_peer') if 'int rtl_ax_mac_drop_peer(' in pci else ''
with tempfile.TemporaryDirectory(prefix='ni-retire-flush-') as directory:
    source, binary = Path(directory) / 'test.c', Path(directory) / 'test'
    source.write_text(common + function((driver / 'role.h').read_text(), 'rtl_ax_station_drop') + drop +
                      function(ni, 'rtl_ax_tx_ni_route') + function(ni, 'rtl_ax_tx_ni_retire_peer') + test)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=10)
