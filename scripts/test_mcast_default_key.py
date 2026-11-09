#!/usr/bin/env python3
"""Execute default GTK selection/publication through the native FE/PE gate."""
import argparse
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', type=Path, required=True)
parser.add_argument('--wifi', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import (UC_ARM64_REG_X19, UC_ARM64_REG_X20,
                                UC_ARM64_REG_X21, UC_ARM64_REG_W27, UC_ARM64_REG_PC)

def boundary(name, values):
    raise AssertionError((name, values))

# The target inlines update_attrib_sec_info into update_attrib. Execute its
# bounded cipher/key-index branch, with the preceding live registers explicit.
# Authentication, packet parsing and crypto are outside this comparison.
stock = ModuleOracle(args.wifi, ['update_attrib'], boundary)
adapter, station, attrib = 0x6000000, 0x6100000, stock.scratch
stock.uc.mem_map(adapter, 0x10000); stock.uc.mem_map(station, 0x10000)
begin = stock.functions['update_attrib'] + 0x35c
end = stock.functions['update_attrib'] + 0x880
assert stock.unpack(begin, 'I')[0] == 0x1200037b
assert stock.unpack(end, 'I')[0] == 0x39416e60
for auth in range(4):
    for default in range(4):
        for group in (False, True):
            for off, value in ((0x81d0, auth), (0x81d4, 4),
                               (0x81d8, (default + 1) & 3), (0x82bc, 4), (0x82c0, default)):
                stock.uc.mem_write(adapter + off, struct.pack('<I', value))
            stock.uc.mem_write(station + 0x4654, struct.pack('<I', 4))
            stock.uc.mem_write(attrib, bytes(256))
            for reg, value in ((UC_ARM64_REG_X19, attrib), (UC_ARM64_REG_X20, adapter),
                               (UC_ARM64_REG_X21, station), (UC_ARM64_REG_W27, int(group))):
                stock.uc.reg_write(reg, value)
            stock.uc.emu_start(begin, end, count=1000)
            assert stock.uc.reg_read(UC_ARM64_REG_PC) == end
            assert stock.unpack(attrib + 91, 'B')[0] == 4
            expected = (default + 1) & 3 if auth != 2 else default if group else 0
            assert stock.unpack(attrib + 128, 'B')[0] == expected
print('Original inlined key selection:32 auth/group/default-ID branches passed')

# Reuse the actual native MMIO gate/change callbacks, not a fake close flag.
fixture = root / 'scripts/test_bridge_mcast_gate.py'
scope = {'__file__': str(fixture), '__name__': 'default_key_gate_fixture'}
argv = sys.argv
sys.argv = [str(fixture), '--kernel', str(args.kernel)]
exec(compile(fixture.read_text().split('code+=private[', 1)[0], str(fixture), 'exec'), scope)
sys.argv = argv
code, function, drivers = (scope[n] for n in ('code', 'function', 'DRIVERS'))
overlay = root / 'port/overlay/target/linux/rtl9617c/files-6.18'
# Replace the fixture's dispatch shim with the real core RCU dispatch.
start = code.index('const struct rtl9617c_bridge_mcast_ops *rtl9617c_bridge_mcast_begin(')
core = (overlay / 'net/core/rtl9617c-bridge.c').read_text()
core_code = r'''
static const struct rtl9617c_bridge_mcast_ops *rtl_bridge_mcast=&ops;
static u64 rtl_wifi_policy_generation=1;
static void rcu_read_lock(void){rcu++;}
static void rcu_read_unlock(void){assert(rcu);rcu--;}
#define rcu_dereference(p) (p)
#define atomic64_inc(p) (++*(p))
#define EXPORT_SYMBOL_GPL(n)
'''
for name in ('rtl9617c_bridge_mcast_begin', 'rtl9617c_bridge_wifi_policy_begin', 'rtl9617c_bridge_mcast_end'):
    core_code += function(core, name)
code = code[:start] + core_code
code = code.replace('#include <stdint.h>', '#include <stdint.h>\ntypedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;')
code += r'''
#define NUM_DEFAULT_KEYS 4
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_AP_VLAN 4
#define IEEE80211_TX_CTRL_MLO_LINK 15
#define IEEE80211_LINK_UNSPECIFIED 15
#define u32_get_bits(v,m) ((v)&(m))
struct ieee80211_key {unsigned identity;};
struct ieee80211_vif {unsigned type;};
struct ieee80211_hw {void *wiphy;};
struct ieee80211_ops {void (*rtl9617c_mcast_policy_changed)(struct ieee80211_hw *,struct ieee80211_vif *);void (*rtl9617c_key_changed)(struct ieee80211_hw *,struct ieee80211_vif *);};
struct ieee80211_local {struct ieee80211_hw hw;const struct ieee80211_ops *ops;};
struct ieee80211_sub_if_data;
struct ieee80211_link_data {struct ieee80211_sub_if_data *sdata;struct ieee80211_key *gtk[4],*default_multicast_key,*default_mgmt_key;};
struct ieee80211_sub_if_data {struct ieee80211_local *local;struct net_device *dev;struct ieee80211_vif vif;
 struct ieee80211_key *keys[4],*default_unicast_key;struct ieee80211_link_data deflink,*link[15];};
struct ieee80211_hdr {u16 frame_control;u8 addr1[6];};
struct ieee80211_tx_info {struct {unsigned flags;}control;};
struct sk_buff {u8 *data;struct ieee80211_tx_info info;bool privacy,robust;};
struct ieee80211_tx_data {struct sk_buff *skb;struct ieee80211_sub_if_data *sdata;};
#define IEEE80211_SKB_CB(s) (&(s)->info)
static bool ieee80211_is_mgmt(u16 f){return !(f&12);}
static bool is_multicast_ether_addr(const u8 *a){return *a&1;}
static bool ieee80211_is_group_privacy_action(struct sk_buff *s){return s->privacy;}
static bool ieee80211_is_robust_mgmt_frame(struct sk_buff *s){return s->robust;}
#define wiphy_dereference(w,p) ((void)(w),(p))
#define lockdep_assert_wiphy(w) assert(w)
static unsigned notified,unicast_changes,debug_updates,key_closures;
static bool key_admitted;
static bool changing_group,changing_unicast;
static struct ieee80211_key *ptk[4];
static unsigned selected_ptk;
static bool changing_ptk;
static struct ieee80211_link_data *changing;
static struct ieee80211_key *expected;
static struct rtl_mc_pe_table radios[2];
static void publish(struct ieee80211_key **p,struct ieee80211_key *key){
 if(p==&changing->default_multicast_key){
  assert(!key_admitted);
  assert(changing_group&&rcu==1&&state.mc_gate.writers==1&&!state.mc_gate.active);
  assert((state.profile&15)==3&&!radios[0].epoch&&!radios[1].epoch&&key==expected);
 }
 if(p==&changing->sdata->default_unicast_key){
  assert(!key_admitted);
  assert(changing_unicast&&rcu==1&&state.mc_gate.writers==1&&!state.mc_gate.active);
  assert((state.profile&15)==3&&!radios[0].epoch&&!radios[1].epoch&&key==expected);
 }
 if(changing_ptk){
  assert(!key_admitted);
  assert(p>=ptk&&p<ptk+4&&rcu==1&&state.mc_gate.writers==1&&!state.mc_gate.active);
  assert(!radios[0].epoch&&!radios[1].epoch&&key==expected);
 }
 *p=key;
}
#define rcu_assign_pointer(p,v) publish(&(p),(v))
static void changed(struct ieee80211_hw *h,struct ieee80211_vif *v){
 assert(h&&v&&!rcu&&!state.mc_gate.writers);notified++;
}
static void key_changed(struct ieee80211_hw *h,struct ieee80211_vif *v){assert(h&&v&&!rcu);key_admitted=false;key_closures++;}
static void ieee80211_check_fast_xmit_iface(struct ieee80211_sub_if_data *s){assert(s&&!rcu);unicast_changes++;}
static void drv_set_default_unicast_key(struct ieee80211_local *l,struct ieee80211_sub_if_data *s,int i){assert(l&&s&&!rcu);(void)i;unicast_changes++;}
static void ieee80211_debugfs_key_update_default(struct ieee80211_sub_if_data *s){assert(s&&!rcu);debug_updates++;}
'''
wme = (args.kernel / 'net/mac80211/wme.h').read_text()
code += 'static inline const struct rtl9617c_bridge_mcast_ops *\n' + function(wme, 'ieee80211_mcast_policy_begin') + function(wme, 'ieee80211_mcast_policy_end')
code += function(wme, 'ieee80211_rtl9617c_key_changed')
code += function((args.kernel / 'net/mac80211/key.c').read_text(), '__ieee80211_set_default_key')
key_source = (args.kernel / 'net/mac80211/key.c').read_text()
# Execute the actual publication blocks, using the same native FE callbacks
# as default-key selection. Remaining key installation is exercised separately.
ptk_start = key_source.index('\t\t\tconst struct rtl9617c_bridge_mcast_ops *token =', key_source.index('static int ieee80211_key_replace('))
ptk_end = key_source.index('\n\t\t\tif (new &&', ptk_start)
ptk_block = key_source[ptk_start:ptk_end].replace('sta->ptk[idx]', 'ptk[idx]')
index_start = key_source.index('\t{\n\t\tconst struct rtl9617c_bridge_mcast_ops *token =', key_source.index('static int _ieee80211_set_tx_key('))
index_end = key_source.index('\n\t}', index_start) + 3
index_block = key_source[index_start:index_end].replace('key->sdata', 'sdata').replace('sta->ptk_idx', 'selected_ptk').replace('key->conf.keyidx', 'idx')
assert 'if (pairwise)\n\t\tieee80211_rtl9617c_key_changed(sdata);' in key_source
assert 'ieee80211_rtl9617c_key_changed(key->sdata);\n\tset_sta_flag' in key_source
code += 'static void publish_ptk(struct ieee80211_sub_if_data *sdata,unsigned idx,struct ieee80211_key *new){ieee80211_rtl9617c_key_changed(sdata);\n' + ptk_block + '\n}\n'
code += 'static void select_ptk(struct ieee80211_sub_if_data *sdata,unsigned idx){ieee80211_rtl9617c_key_changed(sdata);\n' + index_block + '\n}\n'
code += 'static struct ieee80211_key *\n' + function((args.kernel / 'net/mac80211/tx.c').read_text(), 'ieee80211_select_link_key')
code += r'''
static void select_default(struct ieee80211_link_data *link,int idx,bool uni,bool multi,struct ieee80211_key *key){
 unsigned before=notified,updates=debug_updates;u64 epoch=state.mc_gate.epoch,policy=rtl_wifi_policy_generation;
 changing=link;expected=key;changing_group=multi&&link->default_multicast_key!=key;changing_unicast=uni;
 key_admitted=true;
 state.profile=4;state.mc_gate.active=1;radios[0].epoch=radios[1].epoch=7;
 __ieee80211_set_default_key(link,idx,uni,multi);
 assert(!rcu&&!state.mc_gate.writers&&debug_updates==updates+1);
 assert(notified==before+changing_group+changing_unicast&&state.mc_gate.epoch==epoch+2*(changing_group+changing_unicast));
 assert(rtl_wifi_policy_generation==policy+changing_group+changing_unicast);
 if(!changing_group&&!changing_unicast)assert(state.mc_gate.active&&radios[0].epoch==7&&radios[1].epoch==7);
 if(multi)assert(link->default_multicast_key==key);
 if(uni)assert(link->sdata->default_unicast_key==key);
 struct ieee80211_hdr h={.frame_control=8,.addr1={1}};
 struct sk_buff skb={.data=(u8 *)&h,.info={.control={.flags=15}}};
 struct ieee80211_tx_data tx={.skb=&skb,.sdata=link->sdata};
 if(link!=&link->sdata->deflink){link->sdata->link[2]=link;skb.info.control.flags=2;}
 assert(ieee80211_select_link_key(&tx)==link->default_multicast_key);
 h.addr1[0]=2;assert(!ieee80211_select_link_key(&tx));
}
int main(void){
 struct ieee80211_ops callbacks={.rtl9617c_mcast_policy_changed=changed,.rtl9617c_key_changed=key_changed};
 struct ieee80211_local local={.hw={(void*)1},.ops=&callbacks};struct net_device dev;
 struct ieee80211_sub_if_data s={.local=&local,.dev=&dev,.vif={3}};
 struct ieee80211_key keys[8]={};struct ieee80211_link_data extra={.sdata=&s};s.deflink.sdata=&s;
 state.bridge=&owner;state.mc_pe[0].table=&radios[0];state.mc_pe[1].table=&radios[1];
 unsigned cases=0;
 for(unsigned linked=0;linked<2;linked++){
  struct ieee80211_link_data *link=linked?&extra:&s.deflink;
  for(unsigned n=0;n<4;n++){link->gtk[n]=&keys[n];s.keys[n]=NULL;}
  for(unsigned n=0;n<4;n++){
   select_default(link,n,false,true,&keys[n]);cases++;
   select_default(link,n,false,true,&keys[n]);cases++;
   select_default(link,(n+1)%4,true,false,&keys[(n+1)%4]);cases++;
   s.keys[n]=&keys[4+n];select_default(link,n,true,true,&keys[4+n]);cases++;
   s.keys[n]=NULL;select_default(link,n,false,true,&keys[n]);cases++;
   link->gtk[n]=&keys[4+n];select_default(link,n,false,true,&keys[4+n]);cases++;
   select_default(link,-1,false,true,NULL);cases++;
   link->gtk[n]=NULL;select_default(link,n,false,true,NULL);cases++;
   link->gtk[n]=&keys[n];
  }
  select_default(link,4,false,true,NULL);cases++;
 }
 assert(unicast_changes&&state.mc_flush&&!rcu);
 /* The policy begin helper must close unicast before the new QoS field is published. */
 for(unsigned i=0;i<8;i++) {
  key_admitted=true;unsigned before=key_closures;
  const struct rtl9617c_bridge_mcast_ops *token=ieee80211_mcast_policy_begin(&s);
  assert(!key_admitted&&key_closures==before+1&&state.mc_gate.writers);
  ieee80211_mcast_policy_end(&s,token);
 }
 changing=&s.deflink;changing_group=changing_unicast=false;changing_ptk=true;
 for(unsigned idx=0;idx<4;idx++)for(unsigned key=0;key<5;key++) {
  unsigned before=notified;u64 policy=rtl_wifi_policy_generation;
  expected=key<4?&keys[key]:NULL;state.profile=4;state.mc_gate.active=1;radios[0].epoch=radios[1].epoch=7;
  key_admitted=true;
  publish_ptk(&s,idx,expected);
  assert(ptk[idx]==expected&&!state.mc_gate.active&&!rcu&&notified==before+1&&rtl_wifi_policy_generation==policy+1);
  state.profile=4;state.mc_gate.active=1;radios[0].epoch=radios[1].epoch=7;
  key_admitted=true;
  select_ptk(&s,idx);
  assert(!key_admitted&&selected_ptk==idx&&!state.mc_gate.active&&!radios[0].epoch&&!radios[1].epoch&&!rcu&&notified==before+2);
  cases+=2;
 }
 printf("Default GTK:%u actual Linux selection/change cases, dual-radio FE/PE closure before publication, stable defaults and same-slot replacement passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='mcast-default-key-') as tmp:
    tmp = Path(tmp); (tmp / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', '-I'+str(drivers), '-I'+str(overlay/'include'),
                    str(tmp/'test.c'), '-o', str(tmp/'test')], check=True)
    subprocess.run([str(tmp/'test')], check=True)
