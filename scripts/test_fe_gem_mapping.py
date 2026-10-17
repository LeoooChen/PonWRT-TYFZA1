#!/usr/bin/env python3
"""Compare a generic mode1 builder with exact native 8277C assignments."""
from pathlib import Path
import ctypes,hashlib,json,re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
drivers=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
asic=root/'evidence/rtl-workspace/downloads/reference/phoebus-sdk-20261005/vendor/realtek-net/rtl86900/FleetConntrackDriver/core/asicDrv/rtk_8277c_asicDriver.c'
text=asic.read_text()
start=text.index('// forward by port',text.index('memset(hash_action'))
end=text.index('//wifi ssid',start)
block=text[start:end]
fields=['dpid_vld','dpid_pri','permit','mcgid','mc','deepq','t2_ctrl_vld1','pop_l3_en1','pop_l3_chk_ecn_en1','pop_l3_vld1','t2_ctrl1','ldpid_offset_msb']
aal=root/'evidence/rtl-workspace/downloads/reference/ca-network-drivers-20261005/ca-network-engine/1.0-r0/ca-network-engine-1.0/aal-77c/include/aal_common.h'
layout={};offset=0
for number in [18,20]:
    common=aal.read_text()
    start=common.index('#define HASH_ACTION_GROUP_%02d'%number)
    end=common.index('\n\n',start)
    for name,width in re.findall(r'ca_uint64_t\s+(\w+)\s*:\s*(\d+)',common[start:end]):
        layout[name]=(offset,int(width));offset+=int(width)
assert offset==224
bits=[layout[name] for name in fields]
common=r"""
#include <stdint.h>
#include "rtl9617c-fe-format.h"
void pack(unsigned int mapped,unsigned int gem,unsigned int destination,unsigned int seed,unsigned int *out) {
 struct rtl_fe_ipv4_action action={.address=0xc0000201U+seed,.port=1000+seed,
  .destination=destination,.mac_da_index=seed&8191,.ingress_interface=seed&63,
  .egress_interface=(seed>>3)&63,.cos=seed&7,.check_mask=(seed>>4)&63,
  .translate=seed&1,.translate_destination=(seed>>1)&1,.deep_queue=(seed>>2)&1,
  .vlan_set=1,.vlan_count=2,.pppoe_pop=(seed>>3)&1,.mtu_set=1,.mtu_index=seed&15,
  .gem_set=mapped,.gem_index=gem,.bridge=(seed>>4)&1,
  .vlan={{.vid=100+seed%1000,.tpid=1,.priority=seed&7,.dei=seed&1},
         {.vid=200+seed%1000,.tpid=2,.priority=(seed>>3)&7,.dei=(seed>>1)&1}}};
 rtl_fe_ipv4_action(out,&action);
}
"""
with tempfile.TemporaryDirectory(dir=root/'artifacts',prefix='fe-gem-mapping-') as work:
    out=Path(work)
    (out/'pack.c').write_text(common)
    subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-I',str(drivers),str(out/'pack.c'),'-o',str(out/'pack.so')],check=True)
    native=ctypes.CDLL(str(out/'pack.so'))
    native.pack.argtypes=[ctypes.c_uint]*4+[ctypes.POINTER(ctypes.c_uint)]
    vendor='''#define TRUE 1
    #define FALSE 0
    struct path {unsigned int mc,ldpid,gemId_mapping_mode,tcont_id,gem_id;};
    struct action {unsigned int '''+','.join(fields+['mrr_vld','mrr_en'])+''';};
    void vendor(unsigned int mapped,unsigned int gem,unsigned int tcont,unsigned int destination,unsigned int *out) {
     struct path path={.mc=0,.ldpid=destination,.gemId_mapping_mode=mapped,.tcont_id=tcont,.gem_id=gem};
     struct path *pPath12_act=&path;
     struct action action={0},*hash_action=&action;
     int l3fe_egress_mirrored_port=-1;
    '''+block+'\n'+''.join('out[%d]=action.%s;\n'%(i,f) for i,f in enumerate(fields))+'}\n'
    (out/'vendor-map.c').write_text(vendor)
    subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-Wno-sign-compare',str(out/'vendor-map.c'),'-o',str(out/'vendor-map.so')],check=True)
    original=ctypes.CDLL(str(out/'vendor-map.so'))
    original.vendor.argtypes=[ctypes.c_uint]*4+[ctypes.POINTER(ctypes.c_uint)]
    def packed(lib,mapped,gem,destination,seed):
     words=(ctypes.c_uint*8)();lib.pack(mapped,gem,destination,seed,words)
     return int.from_bytes(bytes(words),'little')
    def get(word,bit,width):return (word>>bit)&((1<<width)-1)
    normal=0
    for seed in range(512):
     destination=seed%64
     after=packed(native,0,0,destination,seed)
     assert all(get(after,*layout[name])==0 for name in ['pop_l3_vld1','pop_l3_chk_ecn_en1','pop_l3_en1','t2_ctrl_vld1'])
     expect=(ctypes.c_uint*len(fields))();original.vendor(0,0,0,destination,expect)
     for index,(bit,width) in enumerate(bits):
      if index!=5: # Existing native LAN deepq policy is deliberately unchanged.
       assert get(after,bit,width)==expect[index],(destination,fields[index])
     normal+=1
    mask=sum(((1<<layout[name][1])-1)<<layout[name][0] for name in ['deepq','mcgid','mc','pop_l3_vld1','t2_ctrl1','ldpid_offset_msb'])
    mapped=0
    for tcont in range(32):
     for gem in range(128):
      for bridge in range(2):
       seed=((gem+tcont)&15)|(bridge<<4)
       destination=32+tcont
       before=packed(native,0,gem,destination,seed)
       after=packed(native,1,gem,destination,seed)
       expect=(ctypes.c_uint*len(fields))();original.vendor(1,gem,tcont,destination,expect)
       for index,(bit,width) in enumerate(bits):
        assert get(after,bit,width)==expect[index],(tcont,gem,bridge,fields[index])
       assert all(get(after,*layout[name])==0 for name in ['pol_vld1','pol_en1','pol_id1'])
       assert before&~mask==after&~mask # NAT/interface/VLAN/PPPoE fields survive.
       mapped+=1
    record={'scope':'Source-assignment and packed-field comparison only; no PE arbitration or forwarding simulation',
     'ordinary_actions_match_native_mode0_fields':normal,'mapped_vectors_against_exact_native_assignments':mapped,
     'TCONT_range':[0,31],'GEM_range':[0,127],'routed_and_bridge':True,
     'other_action_fields_preserved':True,'native_non_PON_mapping_flags':[0,0,0,0],
     'source_SHA256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [asic,aal,drivers/'rtl9617c-fe-format.h']}}
    (root/'logs/fe-gem-mapping.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))
