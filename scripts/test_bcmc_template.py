#!/usr/bin/env python3
"""Execute actual Linux default-key snapshot and radio BCMC template lifecycle."""
from pathlib import Path
import re,subprocess,tempfile
r=Path(__file__).resolve().parents[1];o=r/'port/overlay/target/linux/rtl9617c/files-6.18';d=o/'drivers/net/wireless/realtek/rtl_ax_ap'
def function(s,name):
 a=re.search(r'^(?:static )?[^\n]*\b'+name+r'\([^;]*?\)\s*\{',s,re.M).start();b=s.index('{',a)+1;n=1
 while n:n+=(s[b]=='{')-(s[b]=='}');b+=1
 return s[a:b]+'\n'
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#include "linux/rtl9617c-mcast-pe.h"
#include "linux/rtl9617c-seq.h"
#include "linux/rtl9617c-wifi-policy.h"
static unsigned rtl_ax_security_type(unsigned c){return c==9?9:c==10?7:c==8?8:6;}
#define BIT(n) (1U<<(n))
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define NL80211_IFTYPE_AP 3
#define WLAN_CIPHER_SUITE_CCMP 4
#define WLAN_CIPHER_SUITE_GCMP 8
#define WLAN_CIPHER_SUITE_CCMP_256 10
#define WLAN_CIPHER_SUITE_GCMP_256 9
#define KEY_FLAG_UPLOADED_TO_HARDWARE 1
#define KEY_FLAG_TAINTED 2
#define IEEE80211_KEY_FLAG_PAIRWISE 1
#define ETH_P_IP 0x800
#define ETH_P_IPV6 0x86dd
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define htons(v) (v)
#define wiphy_dereference(w,p) ((void)(w),(p))
#define lockdep_assert_wiphy(w) assert(w)
#define EXPORT_SYMBOL_GPL(n)
#define IEEE80211_NUM_TIDS 16
#define PE_PROBE_MAILBOX 0x2000
struct net_device {unsigned refs;};
struct rtl_ax_role_identity {unsigned port,mbid,macid;};
struct rtl_ax_vif {struct rtl_ax_role_identity identity;};
struct ieee80211_vif {struct rtl_ax_vif drv_priv[1];unsigned type;bool mld;u8 addr[6];struct {struct net_device *netdev;}wdev;};
struct ieee80211_key_conf {unsigned cipher,flags,hw_key_idx;int keyidx;unsigned rtl9617c_pn;};
struct ieee80211_key {unsigned flags;struct ieee80211_key_conf conf;};
struct wiphy {u32 frag_threshold;};
struct ieee80211_local {struct {struct wiphy *wiphy;}hw;};
struct ieee80211_sub_if_data {struct ieee80211_vif vif;struct {struct {bool multicast_to_unicast;}ap;}u;struct ieee80211_local *local;struct {bool use_4addr;}wdev;u16 control_port_protocol;struct {struct ieee80211_key *default_multicast_key;}deflink;};
static struct ieee80211_sub_if_data *vif_to_sdata(struct ieee80211_vif *v){return (void*)v;}
static bool ieee80211_vif_is_mld(struct ieee80211_vif *v){return v->mld;}
static unsigned rtl9617c_key_pn_token(unsigned *p){return *p;}
static void ether_addr_copy(u8 *a,const u8 *b){memcpy(a,b,6);}
#define ieee80211_vif_to_wdev(v) (&(v)->wdev)
struct rtl_ax_tx {struct net_device *bcmc_dev[12];u64 bcmc_policy_attempt;bool pe_active,bcmc_enabled,bcmc_owned,ni_prepare_failed,bcmc_retiring;unsigned bcmc_generation,bcmc_live,bcmc_domains,bcmc_domain_retiring;
 struct {void *memory;}pe;struct {bool active,retire_quiescing,retire_requested,peer_retiring[128];unsigned cpu;struct net_device *route_dev;}ni;void *pdev;};
static bool cpu_pending;
static int ni_test_lock,fail_bind,fail_pause,fail_prepare,fail_release,fail_publish,fail_drop,fail_wait;
static unsigned pauses,opens,prepares,releases,publishes,revokes,drops;static u64 policy_epoch=1;
static struct rtl9617c_wifi_policy last;
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static void dma_wmb(void){}
static void dev_hold(struct net_device *d){d->refs++;}
static void dev_put(struct net_device *d){assert(d&&d->refs);d->refs--;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static int rtl_ax_tx_pause(struct rtl_ax_tx *t){(void)t;pauses++;return fail_pause;}
static void rtl_ax_tx_open(struct rtl_ax_tx *t){(void)t;opens++;}
static void rtl_ax_tx_mcast_revoke(struct rtl_ax_tx *t){(void)t;revokes++;policy_epoch++;}
static u64 rtl9617c_bridge_wifi_policy_generation(void){return policy_epoch;}
static int rtl_ax_mac_drop_bcmc(void *p){(void)p;assert(pauses);drops++;return fail_drop;}
static int rtl_ax_mac_drop_group(void *p,unsigned port,unsigned mbid){assert(port<=4&&mbid<=7);return rtl_ax_mac_drop_bcmc(p);}
static bool rtl_ax_tx_station_pending(struct rtl_ax_tx *t,unsigned id,unsigned tid){(void)t;assert(!id&&tid==16);return cpu_pending;}
static int rtl_ax_tx_wait_station(struct rtl_ax_tx *t,unsigned id,unsigned tid){assert(t&&id<128&&tid==16&&drops);return fail_wait;}
static int ni_mcast_bind(struct rtl_ax_tx *t,struct net_device *n){assert(ni_test_lock&&n&&t);return fail_bind;}
static int ni_test_prepare(struct rtl_ax_tx *t,unsigned id){assert(ni_test_lock&&!id&&pauses);prepares++;t->ni.active=true;return fail_prepare;}
static int ni_test_release(struct rtl_ax_tx *t){assert(ni_test_lock);releases++;if(!fail_release)t->ni.active=false;return fail_release;}
int rtl9617c_wifi_mcast_policy(struct net_device *n,const u8 *a,unsigned dest,unsigned macid,const struct rtl9617c_wifi_policy *p,u64 epoch){
 assert(ni_test_lock&&n&&a[0]==2&&dest==19&&!macid&&epoch==policy_epoch);last=*p;publishes++;return fail_publish;}
'''
code+=function((o/'net/mac80211/rtl9617c-mcast.h').read_text(),'ieee80211_rtl9617c_bcmc_key')
for f,n in (('tx.c','rtl_ax_tx_bcmc_close_domain'),('tx.c','rtl_ax_tx_bcmc_close_vif'),('tx.c','rtl_ax_tx_bcmc_drop_all'),('tx.c','rtl_ax_tx_group_stop'),('tx.c','rtl_ax_tx_bcmc_close'),('tx.c','rtl_ax_tx_bcmc_stop'),('ni-test.h','rtl_ax_tx_bcmc')):code+=function((d/f).read_text(),n)
code+=r'''
int main(void){
 struct wiphy w={.frag_threshold=~0U};struct ieee80211_local l={.hw={&w}};struct net_device dev={},other={};
 struct ieee80211_sub_if_data s={.vif={.type=3,.addr={2,0,0,0,0,1},.wdev={&dev}},.local=&l,.control_port_protocol=0x888e};
 struct ieee80211_key keys[4];struct ieee80211_key_conf *key;
 for(unsigned i=0;i<4;i++)keys[i]=(struct ieee80211_key){.flags=1,.conf={.cipher=(unsigned[]){4,8,9,10}[i],.hw_key_idx=60+i,.keyidx=i,.rtl9617c_pn=10+i}};
 static u8 memory[0x80000];struct rtl_ax_tx t={.pe_active=true,.pe={memory},.ni={.cpu=3}};
 struct rtl_mc_pe_bcmc *b=(void*)(memory+RTL_MC_PE_BCMC_BASE);struct rtl_seq_shared *seq=(void*)(memory+RTL_SEQ_BASE);seq->generation=23;
 for(unsigned i=0;i<4;i++){
  s.deflink.default_multicast_key=&keys[i];assert(!ieee80211_rtl9617c_bcmc_key(&s.vif,&key)&&key==&keys[i].conf);
  assert(!rtl_ax_tx_bcmc(&t,&s.vif,key,4)&&b->generation&&last.bcmc_generation==b->generation);
  assert(last.key==((60+i)<<16|i<<24|((i==1||i==2)?RTL_MC_PE_KEY_GCMP:0)|(i>=2?RTL_MC_PE_KEY_256:0)));assert(last.key_generation==10+i&&last.sequence==23);
  unsigned before=revokes,g=b->generation;assert(!rtl_ax_tx_bcmc(&t,&s.vif,key,4)&&revokes==before&&b->generation==g);
 }
 assert(prepares==1&&dev.refs==1&&t.bcmc_enabled);
 s.deflink.default_multicast_key=NULL;assert(!ieee80211_rtl9617c_bcmc_key(&s.vif,&key)&&!key);
 assert(!rtl_ax_tx_bcmc(&t,&s.vif,key,0)&&!last.key&&!last.key_generation&&last.flags==(BIT(18)|BIT(13)|BIT(14)));
 for(unsigned fault=0;fault<9;fault++){
  struct ieee80211_key k=keys[0];s.deflink.default_multicast_key=&k;
  if(fault==0)k.flags=0;if(fault==1)k.flags|=2;if(fault==2)k.conf.cipher=12;if(fault==3)k.conf.flags=1;
  if(fault==4)k.conf.rtl9617c_pn=0;if(fault==5)s.vif.mld=true;if(fault==6)s.wdev.use_4addr=true;if(fault==7)w.frag_threshold=512;if(fault==8)s.u.ap.multicast_to_unicast=true;
  key=(void*)1;assert(ieee80211_rtl9617c_bcmc_key(&s.vif,&key)==-EOPNOTSUPP&&key==(void*)1);
  s.u.ap.multicast_to_unicast=false;s.vif.mld=s.wdev.use_4addr=false;w.frag_threshold=~0U;
 }
 s.deflink.default_multicast_key=&keys[1];assert(!ieee80211_rtl9617c_bcmc_key(&s.vif,&key));
 fail_publish=-ESTALE;assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-ESTALE&&!b->generation&&!t.bcmc_enabled);fail_publish=0;
 assert(!rtl_ax_tx_bcmc(&t,&s.vif,key,4));fail_pause=-ETIMEDOUT;unsigned drop=drops;
 assert(rtl_ax_tx_bcmc_stop(&t)==-ETIMEDOUT&&!b->generation&&drops==drop);fail_pause=0;fail_drop=-EIO;
 assert(rtl_ax_tx_bcmc_stop(&t)==-EIO&&drops==drop+1);fail_drop=0;fail_wait=-ETIMEDOUT;assert(rtl_ax_tx_bcmc_stop(&t)==-ETIMEDOUT&&t.bcmc_retiring);
 assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-EAGAIN&&!b->generation);
 fail_wait=0;assert(!rtl_ax_tx_bcmc_stop(&t)&&!t.bcmc_retiring);
 t.bcmc_generation=~0U;assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-EOVERFLOW&&!b->generation);
 s.vif.drv_priv[0].identity.mbid=1;assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-ESTALE);
 s.vif.drv_priv[0].identity.mbid=0;
 t.bcmc_generation=90;t.ni.retire_quiescing=true;assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-EAGAIN);t.ni.retire_quiescing=false;
 t.ni.retire_requested=true;assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-EAGAIN);t.ni.retire_requested=false;
 s.vif.wdev.netdev=&other;assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-EBUSY&&!other.refs);s.vif.wdev.netdev=&dev;
 assert(!rtl_ax_tx_bcmc(&t,&s.vif,key,4));
 /* Independent active VIF templates survive another VIF closure/failure. */
 struct ieee80211_sub_if_data second=s;
 second.vif.wdev.netdev=&other;second.vif.drv_priv[0].identity=(struct rtl_ax_role_identity){.macid=41,.mbid=3};
 second.vif.addr[5]=2;
 struct rtl_seq_shared *seq2=(void *)(memory+rtl_seq_offset(3));seq2->generation=27;
 struct rtl_mc_pe_bcmc *b2=(void *)(memory+rtl_mc_bcmc_offset(3));
 unsigned first=b->generation;
 assert(!rtl_ax_tx_bcmc(&t,&second.vif,&keys[0].conf,4));
 assert(b2->generation&&b2->reserved[0]==41&&b2->sequence==(27|3U<<27)&&b->generation==first);
 assert(last.flags>>24==41&&last.rate==(4|3U<<24));
 assert(t.bcmc_dev[3]==&other);
 struct ieee80211_sub_if_data conflict=second;conflict.vif.wdev.netdev=&dev;
 assert(rtl_ax_tx_bcmc(&t,&conflict.vif,&keys[0].conf,4)==-EBUSY);
 conflict.vif.drv_priv[0].identity.mbid=4;
 ((struct rtl_seq_shared *)(memory+rtl_seq_offset(4)))->generation=28;
 assert(rtl_ax_tx_bcmc(&t,&conflict.vif,&keys[0].conf,4)==-EBUSY);
 rtl_ax_tx_bcmc_close_vif(&t,&conflict.vif);assert(b2->generation);

 rtl_ax_tx_bcmc_close_vif(&t,&second.vif);
 assert(!b2->generation&&b->generation==first&&t.bcmc_enabled);
 assert(!rtl_ax_tx_bcmc(&t,&second.vif,&keys[0].conf,4));
 fail_publish=-ESTALE;assert(rtl_ax_tx_bcmc(&t,&second.vif,&keys[1].conf,4)==-ESTALE);
 assert(!b2->generation&&b->generation==first);fail_publish=0;
 assert(!rtl_ax_tx_bcmc(&t,&second.vif,&keys[0].conf,4));
 fail_drop=-EIO;assert(rtl_ax_tx_group_stop(&t,41,0,3)==-EIO&&!b2->generation&&b->generation==first);
 assert(t.bcmc_domain_retiring&8);assert(rtl_ax_tx_bcmc(&t,&second.vif,&keys[0].conf,4)==-EAGAIN);
 fail_drop=0;assert(!rtl_ax_tx_group_stop(&t,41,0,3)&&!(t.bcmc_domain_retiring&8));
 assert(!(t.bcmc_domains&8)&&!t.bcmc_dev[3]);
 /* A recreated interface at the same domain may carry a different self role. */
 second.vif.drv_priv[0].identity.macid=63;
 assert(!rtl_ax_tx_bcmc(&t,&second.vif,&keys[0].conf,4)&&b2->reserved[0]==63);
 assert(!rtl_ax_tx_group_stop(&t,63,0,3)&&!(t.bcmc_domains&8)&&!t.bcmc_dev[3]);
 /* Partial NI preparation retains its netdev and retries cleanup first. */
 t.ni.active=false;dev_put(t.ni.route_dev);t.ni.route_dev=NULL;
 fail_prepare=-EIO;assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-EIO&&t.ni_prepare_failed&&dev.refs==1);
 fail_prepare=0;fail_release=-EAGAIN;unsigned count=prepares;
 assert(rtl_ax_tx_bcmc(&t,&s.vif,key,4)==-EAGAIN&&prepares==count&&dev.refs==1);
 fail_release=0;assert(!rtl_ax_tx_bcmc(&t,&s.vif,key,4)&&!t.ni_prepare_failed&&dev.refs==1&&prepares==count+1);
 t.bcmc_owned=false;cpu_pending=true;unsigned prior=drops;
 fail_wait=-ETIMEDOUT;
 assert(rtl_ax_tx_bcmc_stop(&t)==-ETIMEDOUT&&drops==prior+1&&t.bcmc_retiring);
 cpu_pending=false;fail_wait=0;
 assert(!rtl_ax_tx_bcmc_stop(&t)&&drops==prior+2&&!t.bcmc_retiring);
 assert(!rtl_ax_tx_bcmc_stop(&t)&&drops==prior+2);
 puts("BCMC template: actual Linux default GTK selection, open/CCMP/GCMP, unchanged templates, change/publish failure, stop/pause/drop errors, generation exhaustion and partial NI restart passed");
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'t.c').write_text(code)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined','-I'+str(o/'include'),str(p/'t.c'),'-o',str(p/'t')],check=True)
 subprocess.run([str(p/'t')],check=True)
