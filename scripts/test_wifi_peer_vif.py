#!/usr/bin/env python3
"""Run real peer lookup, offload, BA completion and atomic RC callbacks for two BSSes."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
fixture = (root / 'scripts/test_wifi_offload_transaction.py').read_text()
setup = fixture[:fixture.index("cases = r'''")]
ns = {'__file__': str(root / 'scripts/test_wifi_offload_transaction.py')}
exec(compile(setup, ns['__file__'], 'exec'), ns)
common, function = ns['common'], ns['function']
monitor = ns['read']('wireless/realtek/rtl_ax_ap/monitor.h')
common = common.replace('struct ieee80211_hw {', 'struct ieee80211_link_sta {struct ieee80211_sta *sta;};struct ieee80211_hw {', 1)
common = common.replace('struct rtl_ax_ap {', 'struct rtl_ax_ap {unsigned long ra_pending[2];struct wiphy_work ra_update_work;', 1)
common = common.replace('static struct ieee80211_key_conf active_key;', 'static struct ieee80211_key_conf active_key,keys[2];static struct ieee80211_vif *expected_vif[128];static unsigned key_calls[128],route_calls[128],policy_calls[128],ba_starts[128];static struct ieee80211_sta peers[2];static bool wrong_sta;')
a = common.index('static int ieee80211_rtl9617c_unicast_key(')
b = common.index('\nstruct ieee80211_tx_info', a)
common = common[:a] + '''static int ieee80211_rtl9617c_unicast_key(struct ieee80211_vif *v,const u8 *a,struct ieee80211_key_conf **k){
 assert(a[0]==2);unsigned i=v==expected_vif[7]?0:1;assert(v==expected_vif[i?8:7]);key_calls[i?8:7]++;*k=&keys[i];return 0;}
''' + common[b:]
a = common.index('static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(')
b = common.index('static struct rtl_ax_ap *active_radio;', a)
common = common[:a] + '''static struct ieee80211_sta *ieee80211_find_sta_by_ifaddr(struct ieee80211_hw *h,const u8 *a,const u8 *b){
 assert(h&&a[0]==2&&b);unsigned i=b[5]==1?0:1;assert(!memcmp(expected_vif[i?8:7]->addr,b,6));return &peers[wrong_sta?!i:i];}
static int ieee80211_start_tx_ba_session(struct ieee80211_sta *s,unsigned tid,unsigned timeout){assert(!tid&&!timeout);ba_starts[s->drv_priv[0].macid]++;return 0;}
''' + common[b:]
common = common.replace('return 0;}\nstatic struct ieee80211_sta *ieee80211_find_sta_by_ifaddr', 'return BIT(0);}\nstatic struct ieee80211_sta *ieee80211_find_sta_by_ifaddr', 1)
common = common.replace('(void)t;(void)v;(void)id;return 0;}\nstatic unsigned fail_stage', '(void)t;assert(expected_vif[id]==v);policy_calls[id]++;return 0;}\nstatic unsigned fail_stage')
common = common.replace('static unsigned ba_callbacks;', 'static unsigned ba_callbacks;static struct ieee80211_vif *callback_vif;')
common = common.replace('(void)vif;(void)addr;assert(tid==1);ba_callbacks++;', '(void)addr;assert(tid==1);callback_vif=vif;ba_callbacks++;')
common += r'''
#define IEEE80211_RC_BW_CHANGED 1
#define IEEE80211_RC_NSS_CHANGED 2
#define IEEE80211_RC_SMPS_CHANGED 4
#define IEEE80211_RC_SUPP_RATES_CHANGED 8
static void set_bit(unsigned n,unsigned long *p){p[n/64]|=1UL<<(n%64);}
static int rtl_ax_tx_key_refresh(struct rtl_ax_tx *t){(void)t;return 0;}
static int rtl_ax_tx_ni_retire_peer(struct rtl_ax_tx *t,unsigned id){(void)t;(void)id;return 0;}
static int rtl_ax_tx_ni_retire_ba(struct rtl_ax_tx *t,unsigned id){(void)t;(void)id;return 0;}
static bool rtl_ax_tx_peer_security(struct rtl_ax_tx *t,unsigned id,unsigned cam,unsigned keyid){(void)t;assert(cam==(id==7?3:5)&&keyid==(id==7?0:1));return true;}
static int rtl_ax_tx_ni_peer(struct rtl_ax_tx *t,struct net_device *d,unsigned id){(void)t;assert(d==expected_vif[id]->wdev.netdev);route_calls[id]++;return 0;}
static void rtl_ax_offload_ready(struct rtl_ax_ap *r){(void)r;}
'''
code = common + ''.join(function(monitor, n) for n in ('rtl_ax_peer_vif', 'rtl_ax_find_peer', 'rtl_ax_tx_ba_finish', 'rtl_ax_offload_work', 'rtl_ax_link_sta_rc_update'))
code += r'''
int main(void){
 struct net_device devices[2]={};struct ieee80211_vif vifs[2]={};
 struct rtl_ax_tx tx={};struct wiphy w={};struct ieee80211_hw hw={&w};
 struct rtl_ax_ap r={.tx=&tx,.hw=&hw,.ap_vif=&vifs[0],.ap_started=true};active_radio=&r;active_tx=&tx;
 for(unsigned i=0;i<2;i++){
  unsigned id=i+7,self=i?41:0;
  vifs[i].type=NL80211_IFTYPE_AP;vifs[i].addr[0]=2;vifs[i].addr[5]=i+1;vifs[i].wdev.netdev=&devices[i];
  vifs[i].drv_priv[0]=(struct rtl_ax_vif){.identity={.macid=self,.mbid=i,.type=i?3:2},.generation=10+i};
  r.identities[id]=vifs[i].drv_priv[0].identity;r.identities[id].macid=id;
  r.role_owner[id]=self;r.vif_owner[self]=&vifs[i];r.peer_vif_generation[id]=10+i;
  r.tx_ba[id].address[0]=2;r.peer_authorized[id]=r.ra_registered[id]=true;
  peers[i].drv_priv[0].macid=id;peers[i].wme=true;peers[i].deflink.ht_cap.ht_supported=true;
  keys[i]=(struct ieee80211_key_conf){.hw_key_idx=i?5:3,.keyidx=i,.cipher=4};
  r.security_keys[keys[i].hw_key_idx]=&keys[i];r.security_roles[id].valid=1;
  r.security_roles[id].cam[0]=keys[i].hw_key_idx;r.security_roles[id].keyid[0]=i;expected_vif[id]=&vifs[i];
 }
 rtl_ax_offload_work(&w,&r.offload_work);assert(ba_starts[7]==1&&ba_starts[8]==1);
 for(unsigned id=7;id<=8;id++)r.tx_ba[id].aggregation[0]=64;
 rtl_ax_offload_work(&w,&r.offload_work);
 assert(key_calls[7]==1&&key_calls[8]==1&&route_calls[7]==1&&route_calls[8]==1&&policy_calls[7]==1&&policy_calls[8]==1);
 for(unsigned id=7;id<=8;id++){
  r.tx_ba[id].stopping=BIT(1);assert(!rtl_ax_tx_ba_finish(&r,id,1));assert(callback_vif==expected_vif[id]);
 }
 /* A returned station from the other BSS is never accepted, even with matching TA. */
 wrong_sta=true;assert(!rtl_ax_find_peer(&r,7)&&!rtl_ax_find_peer(&r,8));wrong_sta=false;
 /* Removal severs the only VIF pointer before deferred hardware cleanup. */
 r.vif_owner[41]=NULL;unsigned completed=ba_callbacks;
 r.tx_ba[8].stopping=BIT(1);assert(!rtl_ax_tx_ba_finish(&r,8,1)&&ba_callbacks==completed&&!r.tx_ba[8].stopping);
 rtl_ax_offload_work(&w,&r.offload_work);assert(route_calls[7]==2&&route_calls[8]==1);
 /* A new VIF at the same self-MACID has a different instance number. */
 vifs[1].drv_priv[0].generation++;r.vif_owner[41]=&vifs[1];assert(!rtl_ax_peer_vif(&r,8));
 struct ieee80211_link_sta link={.sta=&peers[1]};
 rtl_ax_link_sta_rc_update(&hw,&vifs[1],&link,IEEE80211_RC_BW_CHANGED);assert(!r.ra_pending[0]);
 r.peer_vif_generation[8]++;assert(rtl_ax_find_peer(&r,8)==&peers[1]);
 /* Atomic callback relies only on its supplied live VIF, even with no registry. */
 r.vif_owner[41]=NULL;rtl_ax_link_sta_rc_update(&hw,&vifs[1],&link,IEEE80211_RC_BW_CHANGED);
 assert(r.ra_pending[0]==BIT(8)&&queued==&r.ra_update_work);r.vif_owner[41]=&vifs[1];
 for(unsigned field=0;field<4;field++){
  struct rtl_ax_role_identity save=vifs[1].drv_priv[0].identity;((u8*)&vifs[1].drv_priv[0].identity)[field]^=1;
  assert(!rtl_ax_peer_vif(&r,8));vifs[1].drv_priv[0].identity=save;
 }
 r.role_owner[8]=255;assert(!rtl_ax_peer_vif(&r,8));assert(!rtl_ax_peer_vif(&r,128));
 puts("Two BSSes/same STA MAC: distinct keys/routes/BA callbacks, stale lookup/MACID, removed/recreated VIF, atomic RC generation and identity checks PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-peer-vif-') as tmp:
    p = Path(tmp)
    (p / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable', '-fsanitize=address,undefined', '-I'+str(root/ns['base']/'ethernet/realtek'), str(p/'test.c'), '-o', str(p/'test')], check=True)
    subprocess.run([str(p/'test')], check=True)
