#!/usr/bin/env python3
"""Run native policy compiler against actual mac80211 classification and writers."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
# Reuse the existing Linux byte-frame/WME environment, without executing tests.
setup = (root / 'scripts/test_bridge_mcast_qos.py').read_text().split("code += r'''\nstatic struct ieee80211_sub_if_data", 1)[0]
scope = {'__file__': str(root / 'scripts/test_bridge_mcast_qos.py')}
exec(compile(setup, 'linux-wme-fixture', 'exec'), scope)
code, function, args = scope['code'], scope['function'], scope['args']
code = code.replace('writing', 'vlan_writing')
code = code.replace('#define CONFIG_ARCH_RTL9617C 1', '#define CONFIG_ARCH_RTL9617C 1\n#define IEEE80211_NUM_ACS 4\n#define ETH_ALEN 6\n#define NL80211_IFTYPE_AP 3\n#define NL80211_IFTYPE_STATION 2\n#define ETH_P_8021Q 0x8100\n#define ETH_P_8021AD 0x88a8\n#define GFP_KERNEL 0\n#define WLAN_STA_AUTHORIZED 1\n#define EXPORT_SYMBOL_GPL(v)')
code = code.replace('struct ieee80211_if_managed {', 'struct wiphy {u32 frag_threshold;};\nstruct ieee80211_local {struct {unsigned queues;struct wiphy *wiphy;}hw;};\nstruct ieee80211_vif {unsigned type;u8 addr[6];bool mld;};\nstruct ieee80211_if_managed {')
code = code.replace('struct {unsigned admitted_time,up;}tx_tspec', 'struct {unsigned admitted_time,up,tsid;}tx_tspec')
code = code.replace('struct {unsigned type;}vif;unsigned wmm_acm;', 'struct ieee80211_vif vif;struct ieee80211_local *local;struct {bool use_4addr;}wdev;struct net_device *dev;unsigned noack_map;unsigned wmm_acm;')
code = code.replace('struct ieee80211_if_managed mgd;', 'struct ieee80211_if_managed mgd;struct {bool multicast_to_unicast;}ap;')
code = code.replace('struct sta_info {struct {bool wme;}sta;', 'struct sta_info {bool uploaded,authorized;struct {bool wme;}sta;')
code += r'''
#include "linux/rtl9617c-wifi-policy.h"
static unsigned rcu,writer,notified,allocations;
static bool fail_alloc,missing;
static struct sta_info peer={.uploaded=true,.authorized=true,.sta={true},.reserved_tid=16};
static struct ieee80211_sub_if_data *vif_to_sdata(struct ieee80211_vif *v){return (void *)v;}
static void lockdep_assert_wiphy(struct wiphy *w){assert(w);}
static bool ieee80211_vif_is_mld(struct ieee80211_vif *v){return v->mld;}
static void ether_addr_copy(u8 *d,const u8 *s){memcpy(d,s,6);}
static struct sk_buff *alloc_skb(unsigned n,int f){assert(!rcu&&!writer&&n<=128&&!f);if(fail_alloc)return NULL;allocations++;return calloc(1,sizeof(struct sk_buff));}
static u8 *skb_put_zero(struct sk_buff *s,unsigned n){s->len=n;memset(s->data,0,n);return s->data;}
static void skb_reset_mac_header(struct sk_buff *s){(void)s;}
static void skb_set_network_header(struct sk_buff *s,unsigned n){(void)s;assert(n==14);}
static void rcu_read_lock(void){rcu++;}
static void rcu_read_unlock(void){assert(rcu);rcu--;}
static struct sta_info *sta_info_get(struct ieee80211_sub_if_data *s,const u8 *a){(void)s;assert(a[0]==2&&rcu);return missing?NULL:&peer;}
static bool test_sta_flag(struct sta_info *p,unsigned f){assert(f==1);return p->authorized;}
static void kfree_skb(struct sk_buff *s){assert(!rcu&&allocations);allocations--;free(s);}
static int dev_queue_xmit(struct sk_buff *s){(void)s;abort();}
'''
overlay = root / 'port/overlay/target/linux/rtl9617c/files-6.18'
code += function((overlay / 'net/mac80211/rtl9617c-mcast.h').read_text(), 'ieee80211_rtl9617c_mcast_policy')
code += r'''
static struct ieee80211_sub_if_data *writing;
static unsigned old_noack,old_tid;
static const struct rtl9617c_bridge_mcast_ops *ieee80211_mcast_policy_begin(struct ieee80211_sub_if_data *s){
 assert(!writer&&!rcu);assert(s==writing);assert(s->noack_map==old_noack&&peer.reserved_tid==old_tid);writer++;return &change_ops;
}
static void ieee80211_mcast_policy_end(struct ieee80211_sub_if_data *s,const struct rtl9617c_bridge_mcast_ops *t){
 assert(s==writing&&writer&&t==&change_ops);writer--;notified++;
}
#define IEEE80211_DEV_TO_SUB_IF(d) writing
#define sdata_dereference(p,s) (p)
#define wiphy_priv(p) writing->local
#define TX_TSPEC_ACTION_STOP_DOWNGRADE 2
#define rcu_assign_pointer(p,v) do{assert(writer);(p)=(v);}while(0)
static void *kzalloc(unsigned n,int f){assert(!writer&&!f);return calloc(1,n);}
static void free_map(void *p){assert(!writer);free(p);}
#define kfree_rcu(p,m) free_map(p)
static void ieee80211_check_fast_xmit_iface(struct ieee80211_sub_if_data *s){assert(s==writing&&!writer);}
static void synchronize_net(void){assert(!writer);}
static void ieee80211_flush_queues(struct ieee80211_local *l,struct ieee80211_sub_if_data *s,bool drop){assert(!writer&&l==s->local&&!drop);}
static void ieee80211_sta_handle_tspec_ac_params(struct ieee80211_sub_if_data *s){assert(!writer&&s==writing);}
'''
cfg = (args.kernel / 'net/mac80211/cfg.c').read_text()
code += function(cfg, 'ieee80211_set_multicast_to_unicast')
code += function(cfg, 'ieee80211_set_qos_map') + function(cfg, 'ieee80211_set_noack_map')
code = code.replace('struct {unsigned admitted_time,up,tsid;}tx_tspec', 'struct ieee80211_sta_tx_tspec {unsigned admitted_time,up,tsid,action;bool downgraded;}tx_tspec')
code += function(cfg, 'ieee80211_add_tx_ts') + function(cfg, 'ieee80211_del_tx_ts')
code += r'''
int main(void){
 struct wiphy w={.frag_threshold=(u32)-1};struct ieee80211_local local={.hw={4,&w}};
 struct ieee80211_sub_if_data s={.vif={.type=NL80211_IFTYPE_AP,.addr={2,0,0,0,0,1}},.local=&local};
 struct rtl9617c_wifi_policy out,old;const u8 address[6]={2,1,2,3,4,5};unsigned cases=0;
 struct mac80211_qos_map mapping={.qos_map={.num_des=1,.dscp_exception={{46,1}}}};
 for(unsigned qos=0;qos<2;qos++)for(unsigned acm=0;acm<256;acm++)for(unsigned reserved=0;reserved<9;reserved++)for(unsigned mapped=0;mapped<2;mapped++) {
  peer.sta.wme=qos;peer.reserved_tid=reserved==8?16:reserved;s.wmm_acm=acm;s.qos_map=mapped?&mapping:NULL;s.noack_map=acm;
  assert(!ieee80211_rtl9617c_mcast_policy(&s.vif,address,&out)&&!allocations&&!rcu);
  assert(out.qos==qos&&out.noack_map==acm);
  for(unsigned ds=0;ds<64;ds++)for(unsigned family=0;family<2;family++) {
   struct sk_buff packet={.len=54,.protocol=htons(family?ETH_P_IPV6:ETH_P_IP),.priority=ds&7};
   memcpy(packet.data,address,6);packet.data[14]=family?0x60|(ds>>2):0x45;packet.data[15]=family?(ds&3)<<6:ds<<2;
   ieee80211_select_queue(&s,&peer,&packet);assert(out.dscp_tid[ds]==packet.priority);cases++;
  }
  for(unsigned prio=0;prio<8;prio++) {
   struct sk_buff packet={.len=54,.protocol=htons(ETH_P_8021Q),.priority=256+prio};memcpy(packet.data,address,6);
   ieee80211_select_queue(&s,&peer,&packet);assert(out.priority_tid[prio]==packet.priority);
  }
 }
 s.wmm_acm=0;s.qos_map=NULL;peer.reserved_tid=16;old=out;
 for(unsigned fault=0;fault<8;fault++) {
  s.vif.type=NL80211_IFTYPE_AP;s.vif.mld=false;s.wdev.use_4addr=false;local.hw.queues=4;w.frag_threshold=(u32)-1;missing=false;peer.uploaded=peer.authorized=true;fail_alloc=false;
  if(fault==0)s.vif.type=NL80211_IFTYPE_MESH_POINT;
  if(fault==1)s.vif.mld=true;if(fault==2)s.wdev.use_4addr=true;if(fault==3)local.hw.queues=2;
  if(fault==4)w.frag_threshold=1024;if(fault==5)missing=true;if(fault==6)peer.authorized=false;if(fault==7)fail_alloc=true;
  assert(ieee80211_rtl9617c_mcast_policy(&s.vif,address,&out)==(fault==7?-ENOMEM:-EOPNOTSUPP));
  assert(!memcmp(&out,&old,sizeof(out))&&!allocations&&!rcu);
 }
 fail_alloc=false;peer.authorized=true;s.vif.type=NL80211_IFTYPE_STATION;s.u.mgd.tx_tspec[0].admitted_time=1;
 assert(ieee80211_rtl9617c_mcast_policy(&s.vif,address,&out)==-EOPNOTSUPP);s.u.mgd.tx_tspec[0].admitted_time=0;
 unsigned control[]={ETH_P_IP,ETH_P_IPV6,ETH_P_8021Q,0x88a8};
 for(unsigned c=0;c<4;c++){s.control_port_protocol=htons(control[c]);assert(ieee80211_rtl9617c_mcast_policy(&s.vif,address,&out)==-EOPNOTSUPP);}
 s.control_port_protocol=htons(0x888e);
 writing=&s;old_noack=s.noack_map;old_tid=peer.reserved_tid;
 assert(!ieee80211_set_noack_map(&w,NULL,17)&&s.noack_map==17&&!writer&&notified==1);old_noack=17;
 assert(!ieee80211_set_qos_map(&w,NULL,&mapping.qos_map)&&s.qos_map&&notified==2);
 assert(!ieee80211_set_qos_map(&w,NULL,NULL)&&!s.qos_map&&notified==3&&!writer);
 s.wmm_acm=BIT(6);unsigned notices=notified;
 assert(ieee80211_add_tx_ts(&w,NULL,1,address,5,20)==-EINVAL&&notified==notices);
 assert(!ieee80211_add_tx_ts(&w,NULL,1,address,6,20)&&notified==notices+1);
 assert(s.u.mgd.tx_tspec[0].admitted_time==640&&s.u.mgd.tx_tspec[0].up==6);
 assert(ieee80211_rtl9617c_mcast_policy(&s.vif,address,&out)==-EOPNOTSUPP);
 assert(!ieee80211_del_tx_ts(&w,NULL,1,address)&&notified==notices+3&&!s.u.mgd.tx_tspec[0].admitted_time&&!writer);
 unsigned prior=notified;
 assert(!ieee80211_set_multicast_to_unicast(&w,NULL,true)&&s.u.ap.multicast_to_unicast&&notified==prior+1);
 assert(!ieee80211_set_multicast_to_unicast(&w,NULL,true)&&notified==prior+1);
 assert(!ieee80211_set_multicast_to_unicast(&w,NULL,false)&&!s.u.ap.multicast_to_unicast&&notified==prior+2);
 printf("mac80211 multicast policy: %u IPv4/IPv6/COS comparisons, all ACM/reserved TID/mapping combinations, rejection and actual noack/QoS writers passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='mcast-wifi-policy-') as folder:
    path = Path(folder)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-Wno-unused-parameter', '-Wno-misleading-indentation', '-fsanitize=address,undefined',
                    '-I' + str(overlay / 'include'), str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)

# Every mutable input used by the compiler must close admission before write.
# Check bounded production statements, not allocation/synchronize_net regions.
writers = {
    'net/mac80211/cfg.c': ('sdata->u.ap.multicast_to_unicast = enabled;', 'sdata->noack_map = noack_map;', 'rcu_assign_pointer(sdata->qos_map, new_qos_map);',
        'sta->sta.wme = set & BIT(NL80211_STA_FLAG_WME);', 'ifmgd->tx_tspec[ac].admitted_time = 32 * admitted_time;',
        'tx_tspec->up = -1;', 'memset(tx_tspec, 0, sizeof(*tx_tspec));'),
    'net/mac80211/mlme.c': ('sdata->wmm_acm = 0;', 'sdata->wmm_acm |= BIT(1) | BIT(2);',
        'sdata->wmm_acm |= BIT(4) | BIT(5);', 'sdata->wmm_acm |= BIT(6) | BIT(7);',
        'sdata->wmm_acm |= BIT(0) | BIT(3);', 'memset(ifmgd->tx_tspec, 0, sizeof(ifmgd->tx_tspec));',
        'sta->sta.wme = (elems->wmm_param'),
    'net/mac80211/tx.c': ('sta->reserved_tid = tid;', 'sta->reserved_tid = IEEE80211_TID_UNRESERVED;'),
    'net/wireless/nl80211.c': ('rdev->wiphy.frag_threshold = frag_threshold;', 'rdev->wiphy.frag_threshold = old_frag_threshold;'),
}
count = 0
for name, statements in writers.items():
    text = (args.kernel / name).read_text()
    for statement in statements:
        pos = text.index(statement)
        begin, end = text.rfind('{', 0, pos), text.index('}', pos)
        block = text[begin:end]
        assert 'policy_begin(' in block[:pos-begin] and ('policy_end(' in block[pos-begin:] or 'bridge_mcast_end(' in block[pos-begin:]), (name, statement)
        assert 'synchronize_net(' not in block and 'GFP_KERNEL' not in block, (name, statement)
        count += 1
print(f'PASS: {count} actual policy publication sites revoke old admission before mutation')
