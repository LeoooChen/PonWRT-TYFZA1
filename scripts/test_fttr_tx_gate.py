#!/usr/bin/env python3
"""Interleave the real P6 link transition with DMA timer wake and submission."""
from pathlib import Path
import subprocess
import tempfile
from test_fttr_cpu_deepq import function

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c').read_text()
source = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
typedef int netdev_tx_t;
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct device {void *data;};struct device_attribute {int unused;};struct work {int unused;};
struct net_device {void *data;bool running,stopped,carrier;};
struct sk_buff {bool consumed;};struct rtl9617c_ni_frame {int unused;};
struct rtl_fttr_p6 {bool configured;int error;};
struct rtl_xram {struct rtl_fttr_p6 p6;struct net_device *fttr;int fe,dma,tx_lock;bool fttr_tx_active;
 struct {unsigned fttr_epoch;} qm;};
struct rtl9617c_ni_endpoint {struct net_device *netdev;};
struct rtl_fttr_port {struct rtl9617c_ni_endpoint endpoint;struct rtl_xram *shared;struct work link_work;};
#define netdev_priv(d) ((d)->data)
#define dev_get_drvdata(d) ((struct net_device *)(d)->data)
#define netif_running(d) ((d)->running)
#define netif_queue_stopped(d) ((d)->stopped)
static int locks,rtnl,submissions,failed_fe,failed_config,configured,closed,engine_refs;
#define spin_lock_irqsave(p,f) do{(void)(p);(f)=0;assert(!locks);locks++;}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(p);(void)(f);assert(locks==1);locks--;}while(0)
static void rtnl_lock(void){assert(!rtnl);rtnl++;}
static void rtnl_unlock(void){assert(rtnl==1);rtnl--;}
static void netif_stop_queue(struct net_device *d){d->stopped=true;}
static void netif_start_queue(struct net_device *d){d->stopped=false;}
static void netif_wake_queue(struct net_device *d){d->stopped=false;}
static void netif_tx_disable(struct net_device *d){d->stopped=true;}
static void netif_carrier_off(struct net_device *d){d->carrier=false;}
static netdev_tx_t rtl_dma_tx_frame(int *dma,struct sk_buff *skb,struct net_device *owner,
 const struct rtl9617c_ni_frame *frame){(void)dma;(void)owner;(void)frame;assert(locks==1);submissions++;skb->consumed=true;return 0;}
static void cancel_delayed_work_sync(struct work *w){(void)w;}
static void schedule_delayed_work(struct work *w,unsigned delay){(void)w;(void)delay;}
static int rtl_engine_get(struct rtl_xram *p){(void)p;engine_refs++;return 0;}
static void rtl_engine_put(struct rtl_xram *p){(void)p;engine_refs--;}
static int rtl_fe_fttr_open(int fe){(void)fe;return failed_fe;}
static int rtl_fe_fttr_close(int fe){(void)fe;closed++;return failed_fe;}
static void rtl9617c_ni_close(struct device *dev,struct net_device *owner);
static void interleave(void);
static int rtl_fe_fttr_set(int fe,bool enabled){(void)fe;assert(!enabled);interleave();return failed_fe;}
static int rtl_fttr_p6_configure(struct rtl_fttr_p6 *p,unsigned mode,bool nway){
 (void)mode;(void)nway;interleave();p->configured=!failed_config;configured++;return failed_config;
}
static void rtl_fttr_p6_stop(struct rtl_fttr_p6 *p){interleave();p->configured=false;}
'''
# Production endpoint carries p6.dev for close; add it to the modeled state.
source = source.replace('struct rtl_fttr_p6 {bool', 'struct rtl_fttr_p6 {struct device *dev;bool')
for name in ('rtl9617c_ni_transmit', 'rtl_fttr_wake'):
    source += function(text, name)
if 'static void rtl_fttr_tx_gate(' in text:
    source += function(text, 'rtl_fttr_tx_gate')
for name in ('rtl_fttr_open', 'rtl_fttr_stop', 'fttr_link_store'):
    source += function(text, name)
source += r'''
static struct rtl_xram shared;
static struct net_device p6,lan,root;
static struct device device;
static struct rtl_fttr_port port;
static unsigned interleaved;
static void rtl9617c_ni_close(struct device *dev,struct net_device *owner){
 assert(dev==&device && owner==&p6);interleave();engine_refs--;
}
static void interleave(void) {
 /* Shared-engine timer holds tx_lock before it calls endpoint->wake. */
 assert(!locks);locks=1;rtl_fttr_wake(&port.endpoint);locks=0;
 assert(p6.stopped); /* Must stay stopped even while old PCS is configured. */
 struct sk_buff packet={0};
 assert(rtl9617c_ni_transmit(&device,&p6,&packet,NULL)==NETDEV_TX_BUSY);
 assert(!packet.consumed && p6.stopped);
 /* Other users of the shared DMA engine continue to submit. */
 assert(rtl9617c_ni_transmit(&device,&lan,&packet,NULL)==NETDEV_TX_OK && packet.consumed);
 interleaved++;
}
static void setup(void) {
 shared=(struct rtl_xram){.p6={.dev=&device,.configured=true},.fttr=&p6,.fttr_tx_active=true};
 p6=(struct net_device){.data=&port,.running=true,.carrier=true};root=(struct net_device){.data=&shared};
 port=(struct rtl_fttr_port){.shared=&shared,.endpoint={&p6}};device.data=&root;
 locks=rtnl=submissions=failed_fe=failed_config=configured=closed=0;engine_refs=1;interleaved=0;
}
int main(void) {
 for(unsigned scenario=0;scenario<5;scenario++) {
  setup();const char *command=scenario==1?"0 0":"5 1";
  if(scenario==2 || scenario==4)failed_fe=-EIO;
  if(scenario==3)failed_config=-ETIMEDOUT;
  if(scenario==4)command="0 0";
  int ret=fttr_link_store(&device,NULL,command,3);
  assert(ret==(failed_fe?failed_fe:failed_config?failed_config:3));
  assert(!locks && !rtnl && interleaved>=1);
  if(!scenario) {
   struct sk_buff s={0};assert(!p6.stopped && shared.fttr_tx_active);
   assert(rtl9617c_ni_transmit(&device,&p6,&s,NULL)==NETDEV_TX_OK && s.consumed);
  }else interleave();
 }
 setup();rtnl=1;p6.running=false;assert(!rtl_fttr_stop(&p6));assert(engine_refs==0 && closed==1 && p6.stopped);
 p6.running=true;
 assert(!rtl_fttr_open(&p6));assert(engine_refs==1 && !p6.stopped);
 setup();rtnl=1;failed_fe=-EIO;shared.fttr_tx_active=false;p6.stopped=true;engine_refs=0;
 assert(rtl_fttr_open(&p6)==-EIO && !shared.fttr_tx_active && engine_refs==0);
 puts("P6 TX gate: timer cannot reopen during SerDes/FE transitions; rejected packets retained, other DMA owners continue");
}
'''
with tempfile.TemporaryDirectory() as name:
    work = Path(name); (work / 'test.c').write_text(source)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
        '-Wno-unused-function', '-Wno-sign-compare', '-fsanitize=address,undefined', str(work / 'test.c'),
        '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
