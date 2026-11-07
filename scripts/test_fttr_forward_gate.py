#!/usr/bin/env python3
"""Exercise production FTTR forwarding withdrawal with queued/current CPU packets."""
from pathlib import Path
import subprocess
import tempfile
from test_fttr_cpu_deepq import function

ROOT=Path(__file__).resolve().parents[1]
text=(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c').read_text()
source=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;typedef int netdev_tx_t;
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define ETH_HLEN 14
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define smp_load_acquire(p) (*(p))
#define smp_store_release(p,v) (*(p)=(v))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct device {void *data;};struct device_attribute {int unused;};struct napi_struct {int unused;};
struct net_device {unsigned rtl9617c_fttr_omci;void *data;bool running,stopped;struct {unsigned tx_dropped,rx_dropped,rx_packets,rx_bytes;}stats;};
struct sk_buff {u64 rtl9617c_fttr_generation;unsigned len;bool consumed,vlan,rtl9617c_fttr_ingress;unsigned protocol;u8 bytes[2100];};
struct rtl9617c_ni_frame {int unused;};
struct rtl9617c_ni_endpoint {struct net_device *netdev;};
struct rtl_xram {struct net_device *fttr,*fttr_mc;bool fttr_tx_active,fttr_loading,fttr_forwarding;
 u16 fttr_omci_type;int fttr_forward_error,fttr_forward_cleanup_error,tx_lock,fe,dma;struct {bool configured;}p6;struct {unsigned fttr_epoch;}qm;};
struct rtl_fttr_port {struct rtl9617c_ni_endpoint endpoint;struct rtl_xram *shared;};
#define netdev_priv(d) ((d)->data)
#define dev_get_drvdata(d) ((struct net_device *)(d)->data)
#define netif_running(d) ((d)->running)
static int locked,pe_locked,rtl_fttr_pe_lock,rtnl,submitted,received,failed_fe,purged,synchronized;
static bool qdisc_active=true,fe_enabled=true,fe_requested=true,mc_admitted=true;
static struct rtl_xram shared;static struct net_device parent,lan,root;static struct device device;
static struct rtl_fttr_port port;static struct napi_struct napi;
#define spin_lock_irqsave(p,f) do{(f)=0;if((p)==&rtl_fttr_pe_lock){assert(!pe_locked);pe_locked=1;}else{assert(!locked);locked=1;}}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);if((p)==&rtl_fttr_pe_lock){assert(pe_locked);pe_locked=0;}else{assert(locked);locked=0;}}while(0)
static void rtnl_lock(void){assert(!rtnl);rtnl=1;}
static void rtnl_unlock(void){assert(rtnl);rtnl=0;}
static int kstrtobool(const char *s,bool *v){if(s[0]!='0' && s[0]!='1')return -EINVAL;*v=s[0]=='1';return 0;}
static void netif_stop_queue(struct net_device *d){d->stopped=true;}
static void dev_kfree_skb_any(struct sk_buff *s){assert(!s->consumed);s->consumed=true;}
static bool skb_vlan_tag_present(const struct sk_buff *s){return s->vlan;}
static const void *skb_header_pointer(const struct sk_buff *s,int off,unsigned len,void *buf){
 if(off<0 || off+len>s->len)return NULL;memcpy(buf,s->bytes+off,len);return buf;}
static unsigned get_unaligned_be16(const void *p){const u8 *b=p;return b[0]*256+b[1];}
static unsigned eth_type_trans(struct sk_buff *s,struct net_device *d){(void)d;return get_unaligned_be16(s->bytes+12);}
static void napi_gro_receive(struct napi_struct *n,struct sk_buff *s){(void)n;received++;s->consumed=true;}
static netdev_tx_t rtl_dma_tx_frame(int *dma,struct sk_buff *skb,struct net_device *owner,const struct rtl9617c_ni_frame *f){
 (void)dma;(void)owner;(void)f;assert(locked);submitted++;skb->consumed=true;return 0;}
static void interleave(void);
static void rtl_fttr_mcast_gate(struct net_device *d,bool enabled){(void)d;assert(!locked && rtnl);
 assert(enabled==shared.fttr_forwarding);if(enabled)assert(fe_enabled);mc_admitted=enabled;}
static bool rtl_fttr_mcast_receive(struct net_device *d,struct napi_struct *n,struct sk_buff *s){(void)d;(void)n;(void)s;return false;}
static int rtl_fe_fttr_set(int fe,bool enabled){(void)fe;assert(rtnl);interleave();fe_requested=enabled;if(!failed_fe)fe_enabled=enabled;return failed_fe;}
static int rtl_fe_flush_multicast(int fe,bool fttr_only){(void)fe;assert(rtnl && fttr_only);return failed_fe;}
static void synchronize_net(void){assert(rtnl && !locked);synchronized++;interleave();}
static void rtl_fttr_rx_purge(struct rtl_xram *priv){assert(priv==&shared);synchronize_net();}
static void dev_deactivate(struct net_device *d){assert(d==&parent && rtnl && !locked);qdisc_active=false;purged++;interleave();}
static void dev_activate(struct net_device *d){assert(d==&parent && rtnl && !locked);qdisc_active=true;}
'''
generation=(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/net/core/rtl9617c-fttr.c').read_text()
header=(ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/include/linux/rtl9617c-fttr.h').read_text()
source+=r'''
#define ASSERT_RTNL() assert(rtnl)
#define U64_MAX UINT64_MAX
static u64 rtl_fttr_generation=2;
#define cpu_to_le32(v) (v)
#define dma_wmb() ((void)0)
struct rtl_fttr_pe_generation {u32 low,high;};
static struct rtl_fttr_pe_generation *rtl_fttr_pe_radios[2];
static u64 atomic64_read_acquire(u64 *p){return *p;}
static void atomic64_set_release(u64 *p,u64 v){*p=v;}
'''
source+=function(generation,'rtl9617c_fttr_generation_read')
source+=function(generation,'rtl_fttr_pe_publish')
source+=function(generation,'rtl9617c_fttr_generation_set')
source+=function(header,'rtl9617c_fttr_stale')
source+=function(generation,'rtl9617c_fttr_stamp')
if 'static bool rtl_fttr_management(' in text:
    source+=function(text,'rtl_fttr_management')
source+=function(text,'rtl9617c_ni_transmit')+function(text,'rtl_fttr_receive')
source+=function(text,'fttr_accel_store')
source+=function(text,'fttr_mcast_flush_store')
source+='#define netdev_err(...) ((void)0)\n'
source+=function(text,'rtl_fttr_mcast_quiesce')
if 'static ssize_t fttr_forward_store(' in text:
    source+=function(text,'fttr_forward_store')
else:
    # The preceding implementation only withdraws FE, reproducing the missing CPU gate.
    source+='''static int fttr_forward_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t count){
 (void)dev;(void)attr;rtnl_lock();int ret=rtl_fe_fttr_set(0,buf[0]=='1');rtnl_unlock();return ret?ret:count;}\n'''
source+=r'''
static struct sk_buff packet(unsigned type) {
 struct sk_buff s={.len=64};s.bytes[12]=type>>8;s.bytes[13]=type;s.bytes[19]=10;return s;
}
static void check(unsigned type,bool metadata,bool allowed){
 struct sk_buff s=packet(type);s.vlan=metadata;unsigned before=submitted,drop=parent.stats.tx_dropped;
 assert(rtl9617c_ni_transmit(&device,&parent,&s,NULL)==NETDEV_TX_OK && s.consumed);
 assert(submitted-before==allowed && parent.stats.tx_dropped-drop==!allowed);
 s=packet(type);s.vlan=metadata;before=received;drop=parent.stats.rx_dropped;
 rtl_fttr_receive(&port.endpoint,&napi,&s,6);assert(s.consumed);
 assert(received-before==allowed && parent.stats.rx_dropped-drop==!allowed);
 assert(s.rtl9617c_fttr_ingress==allowed);
}
static void interleave(void){
 assert(!locked);
 assert(!mc_admitted);
 check(0x8100,false,false);check(0x8888,true,false);check(0x0800,false,false);
 check(shared.fttr_omci_type,false,shared.fttr_omci_type!=0);
 struct sk_buff s=packet(0x8100);unsigned before=submitted;
 assert(rtl9617c_ni_transmit(&device,&lan,&s,NULL)==0 && s.consumed && submitted==before+1);
}
int main(void){
 shared=(struct rtl_xram){.fttr=&parent,.fttr_tx_active=true,.fttr_forwarding=true,.fttr_omci_type=0x8888,.p6={true}};
 parent=(struct net_device){.data=&port,.running=true};root.data=&shared;device.data=&root;
 port=(struct rtl_fttr_port){.shared=&shared,.endpoint={&parent}};
 struct sk_buff queued=packet(0x8100);
 assert(rtl9617c_fttr_stamp(&queued));u64 first_generation=queued.rtl9617c_fttr_generation;
 assert(fttr_forward_store(&device,NULL,"0\n",2)==2);
 assert(rtl9617c_fttr_stale(&queued));
 assert(!shared.fttr_forwarding && !mc_admitted && !fe_enabled && qdisc_active && purged==1 && synchronized==1);
 assert(fttr_accel_store(&device,NULL,"1",1)==-EBUSY && !fe_requested);
 assert(fttr_accel_store(&device,NULL,"0",1)==1 && !shared.fttr_forwarding);
 check(0x8100,false,false);check(0x88a8,false,false);check(0x8888,false,true);
 for(unsigned len=0;len<14;len++){
  struct sk_buff s=packet(0x8888);s.len=len;unsigned before=submitted;
  assert(!rtl9617c_ni_transmit(&device,&parent,&s,NULL) && s.consumed && submitted==before);
  s=packet(0x8888);s.len=len;before=received;rtl_fttr_receive(&port.endpoint,&napi,&s,6);
  assert(s.consumed && received==before);
 }
 for(unsigned i=0;i<32;i++){
  unsigned type=i&1?0x8888:0x9999;char command[16];snprintf(command,sizeof(command),"1 %04x\n",type);
  assert(fttr_forward_store(&device,NULL,command,strlen(command))==(int)strlen(command));
  assert(shared.fttr_forwarding && mc_admitted && fe_enabled && shared.fttr_omci_type==type);
  check(0x8100,false,true);check(0x0800,false,true);
  assert(fttr_forward_store(&device,NULL,"0\n",2)==2);check(type,false,true);check(type,true,false);
 }
 failed_fe=-ETIMEDOUT;assert(fttr_forward_store(&device,NULL,"1 8888",6)==-ETIMEDOUT);
 assert(!shared.fttr_forwarding && qdisc_active && !fe_requested);
 assert(shared.fttr_forward_error==-ETIMEDOUT && shared.fttr_forward_cleanup_error==-ETIMEDOUT);interleave();failed_fe=0;
 assert(fttr_forward_store(&device,NULL,"1 8888",6)==6);
 assert(queued.rtl9617c_fttr_generation==first_generation && rtl9617c_fttr_stale(&queued));
 unsigned before=submitted;
 assert(!rtl9617c_ni_transmit(&device,&lan,&queued,NULL) && queued.consumed && submitted==before);
 failed_fe=-EIO;assert(fttr_forward_store(&device,NULL,"0",1)==-EIO);assert(!shared.fttr_forwarding);interleave();failed_fe=0;
 unsigned old=purged;
 for(unsigned i=0;i<7;i++){const char *invalid[]={"","1","1 0000","1 8100","1 88a8","2 8888","0 extra"};
  assert(fttr_forward_store(&device,NULL,invalid[i],strlen(invalid[i]))==-EINVAL);}
 assert(purged==old);
 struct net_device mc={.running=true};shared.fttr_mc=&mc;
 assert(fttr_mcast_flush_store(&device,NULL,"1\n",2)==-EBUSY && !rtnl);
 mc.running=false;failed_fe=-ETIMEDOUT;
 assert(fttr_mcast_flush_store(&device,NULL,"1\n",2)==-ETIMEDOUT && !rtnl);
 failed_fe=0;assert(fttr_mcast_flush_store(&device,NULL,"1\n",2)==2);
 assert(fttr_mcast_flush_store(&device,NULL,"0\n",2)==-EINVAL);
 assert(fttr_mcast_flush_store(&device,NULL,"1 x",3)==-EINVAL);
 /* Closing only the shared endpoint also invalidates queued CPU copies. */
 mc_admitted=false;shared.fttr_forwarding=false;
 rtnl_lock();assert(!rtl9617c_fttr_generation_set(true));rtnl_unlock();
 struct sk_buff before_close=packet(0x8100);assert(rtl9617c_fttr_stamp(&before_close));
 rtnl_lock();rtl_fttr_mcast_quiesce(&parent);rtnl_unlock();
 assert(rtl9617c_fttr_stale(&before_close));struct sk_buff after_close=packet(0x8100);assert(rtl9617c_fttr_stamp(&after_close));
 rtnl_lock();rtl9617c_fttr_generation_set(false);rtl_fttr_mcast_quiesce(&parent);rtnl_unlock();
 assert(rtl9617c_fttr_generation_read()&1);
 shared.fttr_loading=true;assert(fttr_forward_store(&device,NULL,"1 8888",6)==-EBUSY);shared.fttr_loading=false;
 shared.p6.configured=false;assert(fttr_forward_store(&device,NULL,"1 8888",6)==-ENETDOWN);shared.p6.configured=true;
 shared.fttr_tx_active=false;struct sk_buff s=packet(0x8888);
 assert(rtl9617c_ni_transmit(&device,&parent,&s,NULL)==NETDEV_TX_BUSY && !s.consumed);
 shared.fttr_tx_active=true;rtl_fttr_generation=UINT64_MAX;
 assert(fttr_forward_store(&device,NULL,"1 8888",6)==-EOVERFLOW && !shared.fttr_forwarding && !fe_requested);
 puts("PASS: FTTR CPU TX/RX closed before FE/qdisc withdrawal; management and LAN continue; failures cannot reopen forwarding");
}
'''
with tempfile.TemporaryDirectory() as directory:
    path=Path(directory);(path/'test.c').write_text(source)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-parameter',
                    '-Wno-misleading-indentation','-Wno-sign-compare','-fsanitize=address,undefined',str(path/'test.c'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
