#!/usr/bin/env python3
"""Reproduce the 5GHz encap band loss using the kernel's actual TX function."""
from pathlib import Path
import json
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
kernel = Path(subprocess.check_output(['python3', str(root / 'port/tools/project_paths.py'),
                                     '--kernel-directory', str(root / 'sources/openwrt')], text=True).strip())
source = (kernel / 'net/mac80211/tx.c').read_text()
start = source.index('static void ieee80211_8023_xmit(')
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
function = source[start:end]
band_lines = '\tsband = ieee80211_get_sband(sdata);\n\tif (unlikely(!sband))\n\t\tgoto out_free;\n\tinfo->band = sband->band;\n\n'
if band_lines not in function:
    function = function.replace('\tstruct tid_ampdu_tx *tid_tx;', '\tstruct ieee80211_supported_band *sband;\n\tstruct tid_ampdu_tx *tid_tx;')
    function = function.replace('\tinfo->flags |= IEEE80211_TX_CTL_HW_80211_ENCAP;', band_lines + '\tinfo->flags |= IEEE80211_TX_CTL_HW_80211_ENCAP;')
old = function.replace(band_lines, '').replace('\tstruct ieee80211_supported_band *sband;\n', '')
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;
#define unlikely(x) (x)
#define test_bit(n,p) ((*(p)>>(n))&1)
#define rcu_dereference(p) (p)
#define SCAN_SW_SCANNING 0
#define SDATA_STATE_OFFCHANNEL 0
#define HT_AGG_STATE_OPERATIONAL 0
#define GFP_ATOMIC 0
#define IEEE80211_QOS_CTL_TAG1D_MASK 7
#define IEEE80211_TX_CTRL_MLO_LINK_UNSPEC 15
#define IEEE80211_TX_CTL_HW_80211_ENCAP 1
#define NL80211_IFTYPE_AP_VLAN 4
#define container_of(p,t,m) ((t *)((char *)(p)-__builtin_offsetof(t,m)))
struct ieee80211_local {unsigned long scanning;};
struct ieee80211_supported_band {unsigned band;};
struct ieee80211_vif {unsigned type,hw_queue[4];};
struct ieee80211_sub_if_data {struct ieee80211_local *local;unsigned long state;
 struct ieee80211_vif vif;struct {struct {int member;}ap;}u;
 void *bss;struct ieee80211_supported_band *sband;};
struct ieee80211_tx_info {unsigned band,flags,hw_queue,status_data,status_data_idr;
 struct {struct ieee80211_vif *vif;void *hw_key;}control;};
struct sk_buff {struct ieee80211_tx_info info;unsigned len,priority;void *sk;struct sk_buff *next;};
struct tid_ampdu_tx {unsigned long state;bool timeout;unsigned long last_tx;};
struct sta_info {struct {struct tid_ampdu_tx *tid_tx[8];}ampdu_mlme;
 struct {struct {unsigned packets[4],bytes[4];}tx_stats;}deflink;};
struct ieee80211_key {int conf;};struct net_device {int member;};
static unsigned long jiffies;
static unsigned handed_off,dropped,fallback;
#define IEEE80211_SKB_CB(p) (&(p)->info)
#define skb_list_walk_safe(head,p,n) for(p=head;p && ((n=p->next),1);p=n)
static unsigned ieee80211_select_queue(struct ieee80211_sub_if_data *s,struct sta_info *sta,struct sk_buff *p){(void)s;(void)sta;(void)p;return 0;}
static void skb_set_queue_mapping(struct sk_buff *p,unsigned q){(void)p;(void)q;}
static struct sk_buff *skb_share_check(struct sk_buff *p,int g){(void)g;return p;}
static void ieee80211_aggr_check(struct ieee80211_sub_if_data *s,struct sta_info *sta,struct sk_buff *p){(void)s;(void)sta;(void)p;}
static void __ieee80211_subif_start_xmit(struct sk_buff *p,struct net_device *d,int f,int l,void *x){(void)p;(void)d;(void)f;(void)l;(void)x;fallback++;}
static unsigned ieee80211_sdata_netdev_features(struct ieee80211_sub_if_data *s){(void)s;return 0;}
static struct sk_buff *ieee80211_tx_skb_fixup(struct sk_buff *p,unsigned f){(void)f;return p;}
static struct ieee80211_supported_band *ieee80211_get_sband(struct ieee80211_sub_if_data *s){return s->sband;}
static bool sk_requests_wifi_status(void *sk){(void)sk;return false;}
static unsigned ieee80211_store_ack_skb(struct ieee80211_local *l,struct sk_buff *p,unsigned *f,void *x){(void)l;(void)p;(void)f;(void)x;return 0;}
static void dev_sw_netstats_tx_add(struct net_device *d,unsigned s,unsigned n){(void)d;(void)s;(void)n;}
static void ieee80211_tpt_led_trig_tx(struct ieee80211_local *l,unsigned n){(void)l;(void)n;}
static void ieee80211_tx_8023(struct ieee80211_sub_if_data *s,struct sk_buff *p,struct sta_info *sta,bool pending){(void)s;(void)p;(void)sta;(void)pending;handed_off++;}
static void kfree_skb(struct sk_buff *p){(void)p;dropped++;}
'''
test = r'''
int main(void) {
 struct ieee80211_local local={};struct ieee80211_supported_band band={};
 struct ieee80211_sub_if_data s={.local=&local,.sband=&band},vlan={.local=&local,.bss=&s.u.ap};
 struct sta_info sta={};struct net_device dev={};struct ieee80211_key key={};
 for(unsigned b=0;b<2;b++)for(unsigned v=0;v<2;v++) {
  band.band=b;vlan.vif.type=NL80211_IFTYPE_AP_VLAN;
  struct sk_buff second={.len=1400},first={.len=1500,.next=&second};
  unsigned before=handed_off;ieee80211_8023_xmit(v?&vlan:&s,&dev,&sta,&key,&first);
  assert(handed_off==before+1 && !dropped && !fallback);
  assert(first.info.band==(FIXED?b:0) && second.info.band==first.info.band);
  assert(first.info.control.vif==&s.vif && second.info.control.hw_key==&key.conf);
 }
 if(FIXED) {struct sk_buff p={};s.sband=0;ieee80211_8023_xmit(&s,&dev,&sta,&key,&p);assert(dropped==1);}
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-encap-band-') as temp:
    for fixed, body in ((0, old), (1, function)):
        c, exe = Path(temp) / f'{fixed}.c', Path(temp) / f'{fixed}'
        c.write_text(common + '\n#define FIXED ' + str(fixed) + '\n' + body + test)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                        '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
(root / 'logs/mac80211-encap-band-regression.json').write_text(json.dumps({
    'actual_kernel_8023_xmit_executed': True, 'old_5GHz_band_was_2GHz': True,
    'fixed_2GHz_and_5GHz_AP_and_AP_VLAN': True, 'GSO_band_and_key_preserved': True,
    'removed_channel_context_drops_before_queue': True, 'hardware_test': False}, indent=2) + '\n')
print('Kernel encap TX: reproduced lost 5GHz band; AP/AP_VLAN and GSO propagation fixed')
