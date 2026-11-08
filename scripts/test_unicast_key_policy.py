#!/usr/bin/env python3
"""Execute the native current-key snapshot against mac80211 key selection."""
from pathlib import Path
import subprocess, tempfile

root = Path(__file__).resolve().parents[1]
overlay = root / 'port/overlay/target/linux/rtl9617c/files-6.18'
source = (overlay / 'net/mac80211/rtl9617c-mcast.h').read_text()
start = source.index('int ieee80211_rtl9617c_unicast_key(')
end = source.index('EXPORT_SYMBOL_GPL(ieee80211_rtl9617c_unicast_key)', start)
code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_STATION 2
#define WLAN_STA_AUTHORIZED 0
#define WLAN_STA_USES_ENCRYPTION 1
#define KEY_FLAG_UPLOADED_TO_HARDWARE 1
#define KEY_FLAG_TAINTED 2
#define WLAN_CIPHER_SUITE_CCMP 4
#define WLAN_CIPHER_SUITE_GCMP 8
#define WLAN_CIPHER_SUITE_CCMP_256 10
#define WLAN_CIPHER_SUITE_GCMP_256 9
#define IEEE80211_KEY_FLAG_PAIRWISE 1
typedef uint8_t u8;
struct ieee80211_key_conf {unsigned cipher,flags;};
struct ieee80211_key {unsigned flags;struct ieee80211_key_conf conf;};
struct ieee80211_vif {unsigned type;bool mld;};
struct ieee80211_local {struct {int wiphy;}hw;};
struct ieee80211_sub_if_data {struct ieee80211_vif vif;struct ieee80211_local *local;
 struct {bool use_4addr;}wdev;struct ieee80211_key *default_unicast_key;};
struct sta_info {bool uploaded,flags[2];unsigned ptk_idx;struct ieee80211_key *ptk[4];};
static struct sta_info sta;static bool missing;static unsigned rcu;
#define vif_to_sdata(v) ((struct ieee80211_sub_if_data *)(v))
#define lockdep_assert_wiphy(w) assert(w)
#define ieee80211_vif_is_mld(v) ((v)->mld)
#define rcu_dereference(p) (assert(rcu),(p))
static void rcu_read_lock(void){rcu++;}
static void rcu_read_unlock(void){assert(rcu);rcu--;}
static bool test_sta_flag(struct sta_info *s,unsigned bit){assert(rcu);return s->flags[bit];}
static struct sta_info *sta_info_get(struct ieee80211_sub_if_data *s,const u8 *a){assert(rcu&&s&&a[0]==2);return missing?NULL:&sta;}
'''
code += source[start:end]
code += r'''
int main(void){
 struct ieee80211_local local={.hw={1}};
 struct ieee80211_sub_if_data s={.vif={3},.local=&local};
 struct ieee80211_key keys[3];const u8 address[6]={2};unsigned cases=0;
 for(unsigned alg=0;alg<4;alg++)for(unsigned type=2;type<=3;type++)for(unsigned selected=0;selected<4;selected++)
 for(unsigned ptk=0;ptk<4;ptk++)for(unsigned def=0;def<4;def++)for(unsigned used=0;used<2;used++) {
  unsigned cipher=(unsigned[]){4,8,9,10}[alg];
  keys[0]=(struct ieee80211_key){1,{cipher,1}}; /* current uploaded CCMP */
  keys[1]=(struct ieee80211_key){0,{8,1}}; /* software GCMP */
  keys[2]=(struct ieee80211_key){3,{4,1}}; /* tainted old CCMP */
  sta=(struct sta_info){.uploaded=true,.flags={true,used},.ptk_idx=selected};
  sta.ptk[selected]=ptk?&keys[ptk-1]:NULL;
  /* Another installed key must not override the selected index. */
  sta.ptk[(selected+1)%4]=&keys[0];
  s.vif.type=type;s.default_unicast_key=def?&keys[def-1]:NULL;
  struct ieee80211_key *chosen=sta.ptk[selected]?sta.ptk[selected]:s.default_unicast_key;
  bool allowed=chosen?chosen==&keys[0]:!used;
  struct ieee80211_key_conf *result=(void*)1;
  int ret=ieee80211_rtl9617c_unicast_key(&s.vif,address,&result);
  assert(!rcu&&ret==(allowed?0:-EOPNOTSUPP));
  assert(result==(allowed?(chosen?&chosen->conf:NULL):(void*)1));cases++;
 }
 sta=(struct sta_info){.uploaded=true,.flags={true,false},.ptk_idx=2};s.default_unicast_key=NULL;
 for(unsigned fault=0;fault<7;fault++) {
  s.vif.type=3;s.vif.mld=false;s.wdev.use_4addr=false;missing=false;sta.uploaded=sta.flags[0]=true;
  sta.ptk[2]=NULL;s.default_unicast_key=NULL;
  if(fault==0)missing=true;if(fault==1)sta.uploaded=false;if(fault==2)sta.flags[0]=false;
  if(fault==3)s.vif.type=7;if(fault==4)s.vif.mld=true;if(fault==5)s.wdev.use_4addr=true;
  if(fault==6){keys[0].conf.flags=0;s.default_unicast_key=&keys[0];}
  struct ieee80211_key_conf *result=(void*)1;
  assert(ieee80211_rtl9617c_unicast_key(&s.vif,address,&result)==-EOPNOTSUPP&&result==(void*)1&&!rcu);cases++;
 }
 printf("Current unicast key: %u selected/default/software/tainted/open/authorization cases passed\n",cases);
}
'''
with tempfile.TemporaryDirectory() as folder:
    p=Path(folder);(p/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
                    '-Wno-missing-field-initializers','-fsanitize=address,undefined',
                    str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
