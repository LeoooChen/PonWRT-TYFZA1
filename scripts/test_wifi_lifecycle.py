#!/usr/bin/env python3
"""Run production NI attach/detach and FE port reuse against queue/lifetime fixtures."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
xram = (drivers / 'rtl9617c-xram.c').read_text()
fe = (drivers / 'rtl9617c-fe.c').read_text()

def function(source, name):
    start = re.search(r'^int ' + name + r'\(', source, re.M).start()
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
#include <stdlib.h>
#include <string.h>
#include "rtl9617c-wifi-format.h"
typedef unsigned int u32;
#define __rcu
#define ASSERT_RTNL() ((void)0)
#define netdev_priv(p) (p)
#define dev_get_drvdata(d) ((d)->private)
#define rcu_access_pointer(p) (p)
#define rcu_assign_pointer(p,v) ((p)=(v))
struct net_device {unsigned int refs;};
struct rtl9617c_ni_wifi_endpoint {struct net_device *netdev;};
struct rtl_fe_port {struct net_device *netdev;unsigned int wifi_id;};
struct rtl_fe {int lock,flush_error;struct rtl_fe_port ports[64];unsigned int count,flushes;};
struct rtl_qm_rx {struct rtl9617c_ni_wifi_endpoint *wifi[61];};
struct rtl_dma {void *registers;unsigned int vp,write;};
struct rtl_xram {bool dma_tx,qm_rx,removing;struct rtl_fe *fe;struct rtl_qm_rx qm;
 struct rtl_dma dma;unsigned int napi,engine_users,drained;int tx_lock;};
struct device {struct rtl_xram *private;};
static struct rtl_xram *current;
static unsigned int locked,bh_disabled,waited,syncs,rescheduled;
static bool irq_masked;
static void mutex_lock(int *lock){(void)lock;}
static void mutex_unlock(int *lock){(void)lock;}
static struct rtl_fe_port *fe_port(struct rtl_fe *f,struct net_device *d){
 for(unsigned int i=0;i<f->count;i++)if(f->ports[i].netdev==d)return &f->ports[i];return NULL;
}
static int rtl_fe_add_port(struct rtl_fe *f,struct net_device *d,unsigned int source,unsigned int dest,unsigned int llid){
 assert(f->count<64 && source==18 && dest==16 && !llid);f->ports[f->count++].netdev=d;return 0;
}
static int rtl_engine_get(struct rtl_xram *p){p->engine_users++;return 0;}
static void rtl_engine_put(struct rtl_xram *p){assert(p->engine_users);p->engine_users--;}
static void dev_hold(struct net_device *d){d->refs++;}
static void dev_put(struct net_device *d){assert(d->refs>1);d->refs--;}
static int rtl_fe_flush_port(struct rtl_fe *f,struct net_device *d){assert(fe_port(f,d));f->flushes++;return f->flush_error;}
#define spin_lock_irqsave(lock,flags) do{(void)(lock);(flags)=0;assert(!locked);locked=1;}while(0)
#define spin_unlock_irqrestore(lock,flags) do{(void)(lock);(void)(flags);assert(locked);locked=0;}while(0)
static u32 readl(void *p){u32 v;memcpy(&v,p,4);assert(locked);return v;}
#define readl_poll_timeout_atomic(addr,status,cond,delay,timeout) ({status=readl(addr);(cond)?0:-ETIMEDOUT;})
static void usleep_range(unsigned int a,unsigned int b){assert(a==1000 && b==2000);waited++;}
static void napi_disable(unsigned int *n){assert(*n==1);*n=0;irq_masked=true;}
static void napi_enable(unsigned int *n){assert(!*n);*n=1;}
static void napi_schedule(unsigned int *n){assert(*n==1 && irq_masked);rescheduled++;irq_masked=false;}
static void local_bh_disable(void){assert(!bh_disabled);bh_disabled=1;}
static void local_bh_enable(void){assert(bh_disabled);bh_disabled=0;}
static void rtl_qm_rx_poll(struct rtl_qm_rx *q,unsigned int *n,unsigned int budget){
 (void)q;assert(!*n && bh_disabled && budget==1024 && waited);current->drained++;
}
static void synchronize_net(void){assert(!locked && !bh_disabled);syncs++;}
'''
test = r'''
int main(void){
 unsigned int registers[1024]={};struct rtl_fe f={};
 struct rtl_xram p={.dma_tx=true,.qm_rx=true,.fe=&f,.napi=1};
 struct device ni={&p};struct net_device dev[60]={};struct rtl9617c_ni_wifi_endpoint ep[60];
 current=&p;p.dma.registers=registers;p.dma.vp=8;p.dma.write=7;registers[(0x100+8*0xa0+0x1c)/4]=7;
 for(unsigned int i=0;i<60;i++){dev[i].refs=1;ep[i].netdev=&dev[i];}
 int id=rtl9617c_ni_wifi_attach(&ni,&ep[0]);assert(id==1 && dev[0].refs==2 && p.engine_users==1);
 struct rtl_fe_port *block_owner=fe_port(&f,&dev[0]);assert(block_owner && block_owner->wifi_id==1);
 assert(!rtl9617c_ni_wifi_detach(&ni,1) && !p.qm.wifi[1] && dev[0].refs==1 && !p.engine_users);
 assert(rtl9617c_ni_wifi_attach(&ni,&ep[1])==1);
 assert(rtl9617c_ni_wifi_attach(&ni,&ep[0])==2);
 assert(fe_port(&f,&dev[0])==block_owner && block_owner->wifi_id==2);
 registers[(0x100+8*0xa0+0x1c)/4]=6;
 unsigned int old_drained=p.drained;
 assert(rtl9617c_ni_wifi_detach(&ni,2)==-ETIMEDOUT);
 assert(p.qm.wifi[2]==&ep[0] && dev[0].refs==2 && p.engine_users==2 && p.drained==old_drained);
 registers[(0x100+8*0xa0+0x1c)/4]=7;
 f.flush_error=-EIO;assert(rtl9617c_ni_wifi_detach(&ni,2)==-EIO);
 assert(p.qm.wifi[2]==&ep[0] && dev[0].refs==2 && p.drained==old_drained);
 f.flush_error=0;
 assert(!rtl9617c_ni_wifi_detach(&ni,2));assert(!rtl9617c_ni_wifi_detach(&ni,1));
 for(unsigned int i=0;i<60;i++)assert(rtl9617c_ni_wifi_attach(&ni,&ep[i])==(int)i+1);
 assert(rtl9617c_ni_wifi_attach(&ni,&ep[0])==-ENOSPC);
 for(unsigned int i=0;i<60;i++)assert(!rtl9617c_ni_wifi_detach(&ni,i+1));
 assert(!p.engine_users && p.drained==63 && syncs==63 && f.count==60);
 assert(rescheduled==63 && !irq_masked);
 for(unsigned int i=0;i<60;i++)assert(dev[i].refs==1 && !p.qm.wifi[i+1]);
 p.dma_tx=false;assert(rtl9617c_ni_wifi_attach(&ni,&ep[0])==-EOPNOTSUPP && !p.engine_users);
 puts("Wi-Fi lifetime: 60 IDs/capacity; queue timeout retains ID/ref; retry drains before reuse; DOWN/UP preserves flow-block owner");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-lifetime-', dir=root / 'artifacts') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + function(fe, 'rtl_fe_add_wifi_port') +
                               function(xram, 'rtl9617c_ni_wifi_attach') +
                               function(xram, 'rtl9617c_ni_wifi_detach') + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-misleading-indentation', '-g',
                    '-fsanitize=address,undefined', '-I', str(drivers), str(path / 'test.c'),
                    '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
(root / 'logs/wifi-lifecycle-contract.json').write_text(json.dumps({
    'xram_source_sha256': hashlib.sha256(xram.encode()).hexdigest(),
    'FE_source_sha256': hashlib.sha256(fe.encode()).hexdigest(),
    'interfaces': 60, 'NI_drains_and_RCU_grace_periods': 63,
    'queue_timeout_retains_ID_and_netdev': True, 'DOWN_UP_preserves_flow_block': True,
    'IRQ_during_NAPI_disable_followed_by_rescheduled_poll': True,
    'sanitizers': ['ASan', 'UBSan'], 'real_AP_teardown_test': False,
}, indent=2) + '\n')
