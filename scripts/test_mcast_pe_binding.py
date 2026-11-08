#!/usr/bin/env python3
"""Execute FE/radio multicast bindings and real TX teardown with DMA faults."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
overlay = root / 'port/overlay/target/linux/rtl9617c/files-6.18'
ethernet = overlay / 'drivers/net/ethernet/realtek'
radio = overlay / 'drivers/net/wireless/realtek/rtl_ax_ap'


def function(text, name):
    match = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^;]*?\)\s*\{', text, re.M)
    start = match.start()
    end, depth = text.index('{', start) + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "linux/rtl9617c-mcast-pe.h"
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#include "linux/rtl9617c-wifi-policy.h"
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,n) ((v)=(n))
#define dma_wmb() ((void)0)
#define BIT(n) (1U<<(n))
struct net_device {unsigned refs;};
static void dev_hold(struct net_device *d){d->refs++;}
static void dev_put(struct net_device *d){assert(d->refs);d->refs--;}
'''
fe = common + r'''
#define spin_lock_irqsave(p,f) do{assert(!*(p));*(p)=1;(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{assert(*(p)&&!(f));*(p)=0;}while(0)
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
struct rtl_fe_port {struct net_device *dev;unsigned wifi_id;};
struct rtl_fe_wifi_peer {struct rtl_fe_port *port;unsigned destination,macid;u8 address[6];u64 policy_generation;struct rtl9617c_wifi_policy policy;};
static u64 policy_generation=1;
static u64 rtl9617c_bridge_wifi_policy_generation(void){return policy_generation;}
#define ether_addr_equal(a,b) (!memcmp(a,b,6))
#define list_for_each_entry(p,h,m) for(unsigned pi=0;pi<2&&((p)=&f->peers[pi]);pi++)
struct rtl_fe {int lock,mc_lock;unsigned changes;
 struct {unsigned writers;}mc_gate;
 struct rtl_fe_wifi_peer peers[2];
 struct {struct rtl_mc_pe_table *table;struct net_device *netdev;struct rtl9617c_wifi_policy bcmc;u64 bcmc_policy_generation;}mc_pe[2];
 struct rtl_fe_port ports[2];};
static struct rtl_fe_port *fe_port(struct rtl_fe *f,struct net_device *d){
 for(unsigned i=0;i<2;i++)if(f->ports[i].dev==d)return &f->ports[i];return NULL;
}
static void fe_mcast_pe_close(struct rtl_fe *f);
static void fe_mcast_invalidate(struct rtl_fe *f){
 assert(f->lock&&!f->mc_lock);f->changes++;f->mc_lock=1;fe_mcast_pe_close(f);f->mc_lock=0;
}
'''
fe += (ethernet / 'rtl9617c-fe-mcast-pe.h').read_text()
fe = fe.replace('&f->peers[pi]', '&fe->peers[pi]')
fe += r'''
int main(void){
 struct net_device a={},b={},foreign={};struct rtl_fe f={.ports={{&a,1},{&b,2}}};
 struct rtl_mc_pe_table ta={},tb={},other={};
 assert(rtl_fe_wifi_mcast_table(&f,&foreign,18,&ta,true)==-ENODEV);
 assert(rtl_fe_wifi_mcast_table(&f,&a,17,&ta,true)==-EINVAL);
 assert(rtl_fe_wifi_mcast_table(&f,&a,20,&ta,true)==-EINVAL);
 ta.epoch=7;assert(rtl_fe_wifi_mcast_table(&f,&a,18,&ta,true)==-EBUSY);ta.epoch=0;
 assert(!rtl_fe_wifi_mcast_table(&f,&a,18,&ta,true)&&a.refs==1);
 ta.epoch=9;unsigned before=f.changes;
 assert(!rtl_fe_wifi_mcast_table(&f,&a,18,&ta,true)&&ta.epoch==9&&f.changes==before&&a.refs==1);
 assert(rtl_fe_wifi_mcast_table(&f,&b,18,&tb,true)==-EBUSY);
 assert(rtl_fe_wifi_mcast_table(&f,&a,18,&other,false)==-EPERM&&ta.epoch==9);
 assert(!rtl_fe_wifi_mcast_table(&f,&b,19,&tb,true)&&!ta.epoch&&b.refs==1);
 ta.epoch=tb.epoch=11;f.lock=1;fe_mcast_invalidate(&f);f.lock=0;
 assert(!ta.epoch&&!tb.epoch);
 ta.epoch=tb.epoch=12;
 ta.record[9].epoch=12;ta.record[9].control=123;
 assert(!rtl_fe_wifi_mcast_table(&f,&a,18,&ta,false)&&!a.refs&&!ta.epoch&&!tb.epoch);
 assert(!ta.record[9].epoch&&ta.record[9].control==123);
 assert(!rtl_fe_wifi_mcast_table(&f,&a,18,&ta,false)&&!a.refs);
 /* Port removal revokes only its borrowed pointer; other radio stays owned. */
 assert(!rtl_fe_wifi_mcast_table(&f,&a,18,&ta,true));ta.epoch=tb.epoch=13;
 ta.record[10].epoch=13;ta.record[10].control=124;
 f.lock=1;fe_mcast_pe_forget_port(&f,&a);f.lock=0;
 assert(!ta.epoch&&!f.mc_pe[0].table&&!a.refs&&tb.epoch==13&&b.refs==1);
 assert(!ta.record[10].epoch&&ta.record[10].control==124);
 f.lock=1;fe_mcast_pe_forget_port(&f,&b);f.lock=0;
 assert(!tb.epoch&&!b.refs&&!f.mc_pe[1].table);
 assert(!rtl_fe_wifi_mcast_table(&f,&a,18,&ta,true));
 assert(!rtl_fe_wifi_mcast_table(&f,&b,19,&tb,true));ta.epoch=tb.epoch=14;
 /* A retained radio table can be rebound by a fresh FE whose epoch count
  * restarts. None of its old records may become current again. */
 ta.epoch=0;ta.record[9].epoch=1;f.lock=1;fe_mcast_pe_forget_port(&f,&a);f.lock=0;
 assert(!rtl_fe_wifi_mcast_table(&f,&a,18,&ta,true));ta.epoch=1;
 assert(!ta.record[9].epoch&&ta.record[9].control==123);
 struct rtl9617c_wifi_policy policy={.qos=1,.tagged_tid=2,.noack_map=128,.flags=BIT(16)|BIT(18)};
 const u8 mac[6]={2,1,2,3,4,5};f.peers[0]=(struct rtl_fe_wifi_peer){.port=&f.ports[0],.destination=18};
 memcpy(f.peers[0].address,mac,6);
 for(unsigned i=0;i<64;i++)policy.dscp_tid[i]=i%8;
 for(unsigned i=0;i<8;i++)policy.priority_tid[i]=7-i;
 assert(!rtl_fe_wifi_mcast_policy(&f,&a,mac,18,1,&policy,1));
 assert(!ta.epoch&&!tb.epoch);
 assert(f.peers[0].macid==1&&f.peers[0].policy_generation==1&&!memcmp(&f.peers[0].policy,&policy,sizeof(policy)));
 assert(rtl_fe_wifi_mcast_policy(&f,&b,mac,19,1,&policy,1)==-ENOLINK);
 assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,19,1,&policy,1)==-ENOLINK);
 assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,18,0,&policy,1)==-EINVAL);
 policy_generation=2;assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,18,1,&policy,1)==-ESTALE);
 f.mc_gate.writers=1;assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,18,1,&policy,2)==-ESTALE);f.mc_gate.writers=0;
 assert(!rtl_fe_wifi_mcast_policy(&f,&a,mac,18,1,&policy,2));
 policy.dscp_tid[63]=8;assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,18,1,&policy,2)==-EINVAL);policy.dscp_tid[63]=7;
 policy.priority_tid[7]=8;assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,18,1,&policy,2)==-EINVAL);policy.priority_tid[7]=0;
 ta.epoch=tb.epoch=15;policy.noack_map=1;
 assert(!rtl_fe_wifi_mcast_policy(&f,&a,mac,18,2,&policy,2)&&!ta.epoch&&!tb.epoch);
 assert(f.peers[0].macid==2&&f.peers[0].policy.noack_map==1);
 struct rtl9617c_wifi_policy group={.flags=BIT(18)|BIT(13)|BIT(14)|BIT(15),.key=63<<16|2<<24,.bcmc_generation=29,.sequence=23,.key_generation=11,.rate=4};
 assert(!rtl_fe_wifi_mcast_policy(&f,&a,mac,18,0,&group,2));
 assert(f.mc_pe[0].bcmc_policy_generation==2&&!memcmp(&f.mc_pe[0].bcmc,&group,sizeof(group)));
 group.sequence=0;assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,18,0,&group,2)==-EINVAL);group.sequence=23;
 assert(rtl_fe_wifi_mcast_policy(&f,&b,mac,18,0,&group,2)==-ENOLINK);
 assert(rtl_fe_wifi_mcast_policy(&f,&a,mac,18,0,&group,1)==-ESTALE);
 rtl_fe_wifi_mcast_detach(&f);
 assert(!ta.epoch&&!tb.epoch&&!a.refs&&!b.refs&&!f.mc_pe[0].table&&!f.mc_pe[1].table);
 puts("FE multicast PE binding: two radios, ownership conflicts, idempotence, invalidation and port detachment passed");
}
'''
tx = common + r'''
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_TX_ENTRIES 2
#define RTL_AX_TX_PAGES 2
#define RTL_AX_TX_WD_PAGES 2
#define RTL_AX_TX_WP_BYTES 128
#define RTL_AX_TX_RINGS 2
struct ieee80211_hw {int unused;};
struct pe_probe {void *memory;};
struct rtl_ax_tx {struct pe_probe pe;struct {unsigned cpu;int ps_work;bool route_peers[128];}ni;
 int lock,fttr_work;bool fttr_registered,mcast_cpu_registered,pe_active,peer_authorized[128];u64 mcast_policy_attempt[128];
 struct rtl_mc_pe_table *mcast_table;struct net_device *mcast_dev;void *dma_device;
 uintptr_t mcast_dma,headers_dma,pages_dma,ring_dma;void *headers,*pages,*ring;
 struct {void *skb;u64 fttr_generation;}slots[2];};
static unsigned bind_calls,unbind_calls,stop_calls,dma_frees,tx_frees,pe_frees,remove_calls;
static int fail_bind,fail_unbind,fail_remove,fail_stop;
static bool bound,joined;static unsigned canceled,unregistered,cpu_unregistered;
static u64 generation=7,seen_generation;
static unsigned compiler_calls,publish_calls;
static int compiler_error,publish_error;
struct ieee80211_vif {struct {struct net_device *netdev;}wdev;};
#define BIT(n) (1U<<(n))
#define spin_lock_irqsave(p,f) do{assert(!*(p));*(p)=1;(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{assert(*(p)&&!(f));*(p)=0;}while(0)
#define ether_addr_equal(a,b) (!memcmp(a,b,6))
#define ETH_ALEN 6
#define ieee80211_vif_to_wdev(v) (&(v)->wdev)
static void ether_addr_copy(u8 *d,const u8 *s){memcpy(d,s,6);}
static u64 rtl9617c_bridge_wifi_policy_generation(void){return generation;}
int ieee80211_rtl9617c_mcast_policy(struct ieee80211_vif *v,const u8 *a,struct rtl9617c_wifi_policy *p){
 assert(v&&a[0]==2);compiler_calls++;if(compiler_error)return compiler_error;memset(p,0,sizeof(*p));p->dscp_tid[46]=5;return 0;
}
int rtl9617c_wifi_mcast_policy(struct net_device *d,const u8 *a,unsigned dest,unsigned id,const struct rtl9617c_wifi_policy *p,u64 g){
 assert(d&&a[0]==2&&dest==19&&id==1&&p->dscp_tid[46]==5);seen_generation=g;publish_calls++;return publish_error;
}
static int ni_test_lock;
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static u32 get_unaligned_le32(void *p){u32 v;memcpy(&v,p,4);return v;}
static int rtl9617c_wifi_mcast_table(struct net_device *d,unsigned dest,struct rtl_mc_pe_table *t,bool bind){
 assert(d&&dest==19&&t);
 if(bind){bind_calls++;if(fail_bind)return -EIO;bound=true;}
 else {unbind_calls++;assert(!t->epoch);if(fail_unbind)return -EIO;bound=false;}
 return 0;
}
static int ni_test_remove(struct rtl_ax_tx *t){assert(!bound&&!t->mcast_dev);remove_calls++;return fail_remove?-EIO:0;}
static void pe_event_release(struct rtl_ax_tx *t){(void)t;}
static void cancel_work_sync(int *p){(void)p;}
static void cancel_delayed_work_sync(int *p){(void)p;assert(joined);canceled++;}
static void rtl9617c_fttr_pe_unregister(struct rtl_fttr_pe_generation *p){assert(joined&&canceled&&p);unregistered++;}
static void rtl9617c_bridge_mcast_pe_unregister(unsigned int *p){assert(joined&&canceled&&p);cpu_unregistered++;}
static int pe_probe_stop(struct pe_probe *p){assert(p->memory&&!bound);stop_calls++;if(fail_stop)return -ETIMEDOUT;joined=true;return 0;}
static unsigned payload_unmapped,payload_freed,fttr_puts;
static void rtl9617c_fttr_tx_put(u64 generation){assert(joined&&generation==2&&payload_freed==1&&payload_unmapped==1);fttr_puts++;}
static void rtl_ax_tx_unmap(struct rtl_ax_tx *t,void *p){assert(joined&&canceled&&p==&t->slots[0]&&!payload_unmapped);payload_unmapped++;}
static void ieee80211_free_txskb(struct ieee80211_hw *h,void *p){(void)h;assert(p&&payload_unmapped&&!payload_freed);payload_freed++;}
static void dma_free_coherent(void *d,unsigned n,void *p,uintptr_t a){
 (void)d;(void)a;assert(n==sizeof(struct rtl_mc_pe_table)&&p&&joined&&!bound&&unregistered&&cpu_unregistered);dma_frees++;
}
static void pe_probe_free(struct pe_probe *p){assert(joined&&dma_frees);(void)p;pe_frees++;}
#define kfree(p) do{assert(joined&&dma_frees&&pe_frees);(void)(p);tx_frees++;}while(0)
'''
tx += (radio / 'ni-mcast.h').read_text()
tx += function((radio / 'tx.c').read_text(), 'rtl_ax_tx_free')
tx += r'''
int main(void){
 unsigned char memory[131072]={};struct rtl_mc_pe_table table={};struct net_device a={},b={};
 struct rtl_ax_tx t={.pe={memory},.ni={.cpu=3},.mcast_table=&table,.fttr_registered=true,.mcast_cpu_registered=true};
 assert(ni_mcast_bind(&t,&a)==-EOPNOTSUPP&&!bind_calls);
 u32 cap=RTL_MC_PE_CAPABILITY;memcpy(memory+PE_PROBE_MAILBOX+RTL_MC_PE_CAP_MAILBOX*4,&cap,4);
 fail_bind=1;assert(ni_mcast_bind(&t,&a)==-EIO&&!t.mcast_dev&&!a.refs);fail_bind=0;
 assert(!ni_mcast_bind(&t,&a)&&bound&&a.refs==1);
 assert(!ni_mcast_bind(&t,&a)&&a.refs==1);
 unsigned before=bind_calls;assert(!ni_mcast_bind(&t,&b)&&bind_calls==before&&!b.refs);
 struct ieee80211_vif vif={.wdev={&a}};memory[0xe408]=2;
 assert(rtl_ax_tx_mcast_policy(&t,&vif,1)==-ENOLINK&&!compiler_calls);
 t.pe_active=t.ni.route_peers[1]=t.peer_authorized[1]=true;
 u32 template[8]={0,0,24U<<16,0,BIT(16)|BIT(18),0,0,0};memcpy(memory+0xd020,template,sizeof(template));
 compiler_error=-EOPNOTSUPP;assert(rtl_ax_tx_mcast_policy(&t,&vif,1)==-EOPNOTSUPP&&!publish_calls&&t.mcast_policy_attempt[1]==7);
 generation=8;compiler_error=-ENOMEM;assert(rtl_ax_tx_mcast_policy(&t,&vif,1)==-ENOMEM&&t.mcast_policy_attempt[1]==7);
 compiler_error=0;publish_error=-ESTALE;assert(rtl_ax_tx_mcast_policy(&t,&vif,1)==-ESTALE&&t.mcast_policy_attempt[1]==7);
 publish_error=0;assert(!rtl_ax_tx_mcast_policy(&t,&vif,1)&&seen_generation==8&&t.mcast_policy_attempt[1]==8);
 vif.wdev.netdev=&b;before=compiler_calls;
 assert(rtl_ax_tx_mcast_policy(&t,&vif,1)==-ENOLINK&&compiler_calls==before);
 t.slots[0].skb=memory;t.slots[0].fttr_generation=2;
 table.epoch=7;fail_unbind=1;
 assert(rtl_ax_tx_free(&t,NULL)==-EIO&&!table.epoch&&bound&&a.refs==1&&!stop_calls&&!dma_frees&&!remove_calls);
 fail_unbind=0;fail_remove=1;
 assert(rtl_ax_tx_free(&t,NULL)==-EIO&&!bound&&!a.refs&&!stop_calls&&!dma_frees);
 fail_remove=0;fail_stop=1;
 assert(rtl_ax_tx_free(&t,NULL)==-ETIMEDOUT&&!dma_frees&&!tx_frees&&!canceled&&!unregistered&&!cpu_unregistered&&!fttr_puts&&!payload_unmapped);
 fail_stop=0;assert(!rtl_ax_tx_free(&t,NULL)&&dma_frees==1&&tx_frees==1&&pe_frees==1&&fttr_puts==1);
 puts("Radio multicast PE binding: capability, failed ownership/NI drain/context join retain DMA; detach-before-join-before-free passed");
}
'''
supplier = common + r'''
struct device {unsigned owned;};
struct rtl_wifi_port {unsigned unused;};
static struct device ni,*wifi_ni=&ni;
static int wifi_mcast_lock,wifi_notifier,rtnl;
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void unregister_netdevice_notifier(int *p){assert(p==&wifi_notifier&&!rtnl);}
static void rtnl_lock(void){assert(!rtnl);rtnl=1;}
static void rtnl_unlock(void){assert(rtnl&&!wifi_ni&&!ni.owned);rtnl=0;}
#define list_for_each_entry_safe(p,n,h,m) for(p=NULL,n=NULL;p;p=n)
static void wifi_del(struct rtl_wifi_port *p){(void)p;assert(false);}
static void rtl9617c_ni_wifi_mcast_detach(struct device *d){assert(d==wifi_ni&&wifi_mcast_lock&&rtnl);d->owned=0;}
static int rtl9617c_ni_wifi_mcast_table(struct device *d,struct net_device *n,unsigned dest,struct rtl_mc_pe_table *t,bool bind){
 assert(d==wifi_ni&&wifi_mcast_lock&&dest==19&&t&&n);d->owned=bind;return 0;
}
'''
wifi = (ethernet / 'rtl9617c-wifi.c').read_text()
supplier += function(wifi, 'rtl_wifi_stop') + function(wifi, 'rtl9617c_wifi_mcast_table')
supplier += r'''
int main(void){
 struct net_device d={};struct rtl_mc_pe_table t={};
 assert(!rtl9617c_wifi_mcast_table(&d,19,&t,true)&&ni.owned);
 rtl_wifi_stop();
 assert(!wifi_ni&&!ni.owned);
 assert(!rtl9617c_wifi_mcast_table(&d,19,&t,false));
 assert(rtl9617c_wifi_mcast_table(&d,19,&t,true)==-ENODEV);
 dev_hold(&d);dev_put(&d);
 puts("NI supplier stop: binding lock joins FE borrowers before absence permits radio allocation release");
}
'''
with tempfile.TemporaryDirectory(prefix='mcast-pe-binding-') as folder:
    work = Path(folder)
    for name, code in (('fe', fe), ('radio', tx), ('supplier', supplier)):
        source, binary = work / (name + '.c'), work / name
        source.write_text(code)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-misleading-indentation',
                        '-fsanitize=address,undefined', '-I' + str(overlay / 'include'),
                        str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
