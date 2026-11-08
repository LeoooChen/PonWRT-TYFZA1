#!/usr/bin/env python3
"""Compare native peer JOIN with actual stock PPE parsing and HAL/MAC encoding."""
from pathlib import Path
import sys,struct,subprocess,tempfile,ctypes,random
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
m=Path(sys.argv[1]) if len(sys.argv)>1 else root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
def cb(name,a):
 if name=='hal_mac_get_macid_num':return 128
 if name=='get_addr_cam_size':return 32
 if name=='h2cb_alloc':return out+256
 if name=='h2cb_put':return out
 if name=='h2c_agg_enqueue':return 0
 if name in ('h2c_pkt_set_hdr','h2cb_free'):return 0
 if name=='_rtw_memcpy':o.uc.mem_write(a[0],bytes(o.uc.mem_read(a[1],a[2])));return a[0]
 if name=='memcmp':
  x,y=bytes(o.uc.mem_read(a[0],a[2])),bytes(o.uc.mem_read(a[1],a[2]));return (x>y)-(x<y)
 raise AssertionError((name,a))
o=ModuleOracle(m,['_hal_stainfo_to_macrinfo','_rtype_to_mac_nettype','role_info_init','fill_bssid_cam_info','mac_h2c_join_info','fill_addr_cam_info','get_set_bits_of_msk','rtw_he_set_asoc_cap_ppe_thre','init_hal_spec_8852c','init_hal_spec_8192xb'],cb)
hal,common,role,sta,info,out=[0x6000000+i*0x10000 for i in range(6)]
for x in (hal,common,role,sta,info,out):o.uc.mem_map(x,0x10000)
o.word(hal+8,common);o.word(sta+16,role)
o.uc.mem_write(role+8,struct.pack('<I',2));o.uc.mem_write(role+16,struct.pack('<I',2))
o.uc.mem_write(role+28,bytes.fromhex('d05bcbd3ebb2'));o.uc.mem_write(sta+30,bytes.fromhex('3a2ea7759af2'))
o.uc.mem_write(sta+2332,struct.pack('<I',32));o.word(sta+2656,1<<20)

o.uc.mem_write(hal+33,b'\x04');o.uc.mem_write(sta+28,struct.pack('<H',1))

source=(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/role.h').read_text()
def function(name):
 start=source.index('static inline ',source.index(name)-30)
 pos=source.index('{',start)+1;depth=1
 while depth:
  depth+=(source[pos]=='{')-(source[pos]=='}');pos+=1
 return source[start:pos]
prefix="""
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;
static const u8 self[6]={0xd0,0x5b,0xcb,0xd3,0xeb,0xb2},peer[6]={0x3a,0x2e,0xa7,0x75,0x9a,0xf2};
#define BIT(n) (1U<<(n))
#define IEEE80211_HE_PHY_CAP6_PPE_THRESHOLD_PRESENT 0x80
#define IEEE80211_HE_PHY_CAP9_NOMINAL_PKT_PADDING_POS 6
#define IEEE80211_HE_MAC_CAP1_TF_MAC_PAD_DUR_MASK 0x0c
struct ieee80211_sta_he_cap {bool has_he;struct {u8 mac_cap_info[6],phy_cap_info[11];} he_cap_elem;u8 ppe_thres[25];};
static inline void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
"""
wrapper="""
unsigned int self_join(unsigned int id,unsigned int bw,unsigned int mesh){u8 out[4];rtl_ax_role_join(out,id,bw,mesh);unsigned int v;memcpy(&v,out,4);return v;}
unsigned int camword(unsigned int id,unsigned int he){u8 out[60];rtl_ax_peer_cam(out,id,self,peer,1,true);if(he)rtl_ax_store32(out+32,id|BIT(14));unsigned int v;memcpy(&v,out+32,4);return v;}
unsigned int run(unsigned int id,unsigned int bw,unsigned int he,unsigned int nss,unsigned int client,unsigned int mesh,const u8 *mac,const u8 *phy,const u8 *ppe){
 struct ieee80211_sta_he_cap cap={};u8 out[4];cap.has_he=he;
 memcpy(cap.he_cap_elem.mac_cap_info,mac,6);memcpy(cap.he_cap_elem.phy_cap_info,phy,11);memcpy(cap.ppe_thres,ppe,25);
 rtl_ax_station_join(out,id,bw,&cap,nss,mesh,client);unsigned int v;memcpy(&v,out,4);return v;
}
"""
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'join.c';so=Path(temp)/'join.so'
 c.write_text(prefix+function('rtl_ax_he_padding(')+function('rtl_ax_station_join(')+function('rtl_ax_peer_cam(')+function('rtl_ax_role_join(')+wrapper)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Werror','-O2',str(c),'-o',str(so)],check=True)
 lib=ctypes.CDLL(str(so));lib.run.restype=ctypes.c_uint;lib.run.argtypes=[ctypes.c_uint]*6+[ctypes.c_char_p]*3
 lib.camword.restype=ctypes.c_uint;lib.camword.argtypes=[ctypes.c_uint]*2
 lib.self_join.restype=ctypes.c_uint;lib.self_join.argtypes=[ctypes.c_uint]*3
 rng=random.Random(9617);count=0
 for test in range(2400):
  client=test>=1600;mesh=800<=test<1600
  o.uc.mem_write(role+8,struct.pack('<I',1 if client else 6 if mesh else 2))
  he=test%5!=0;nss=1+test%2;bw=test%4;macid=1+test%127
  present=he and test%4!=0;tf=test%3 if he else 0
  mask=[1,4,8,12,15][test%5];nominal=3 if present else test%3
  phy=bytearray(11);phy[6]=128 if present else 0;phy[9]=nominal<<6
  mac=bytearray(6);mac[1]=tf<<2
  bits=(nss-1)|(mask<<3);position=7
  for ss in range(nss):
   for ru in range(4):
    if mask&(1<<ru):bits|=rng.randrange(64)<<position;position+=6
  ppe=bits.to_bytes(25,'little')
  o.uc.mem_write(sta+2504,struct.pack('<Q',tf<<54));o.word(sta+2656,nss<<20)
  o.uc.mem_write(sta+2620,struct.pack('<H',nominal<<9));o.uc.mem_write(sta+2622,bytes(32))
  o.uc.mem_write(out,ppe);o.call('rtw_he_set_asoc_cap_ppe_thre',hal,sta,out)
  o.uc.mem_write(sta+2332,struct.pack('<I',32 if he else 8));o.uc.mem_write(sta+44,struct.pack('<I',bw));o.uc.mem_write(sta+28,struct.pack('<H',macid))
  o.uc.mem_write(info,bytes(512));o.call('_hal_stainfo_to_macrinfo',hal,sta,info,5 if client else 2,1);o.call('mac_h2c_join_info',hal,info)
  expected=o.unpack(out,'I')[0]
  actual=lib.run(macid,bw,he,nss,client,mesh,bytes(mac),bytes(phy),ppe)
  assert actual==expected,(test,hex(actual),hex(expected),mask,ppe.hex())
  o.call('role_info_init',hal,info);o.uc.mem_write(out,bytes(512));o.call('fill_addr_cam_info',hal,info,out)
  assert lib.camword(macid,he)==o.unpack(out+32,'I')[0]
  count+=1
 # The original self producer overrides the channel BW with hardware BW support.
 o.uc.mem_write(role+8,struct.pack('<I',2))
 phl=0x6060000;o.uc.mem_map(phl,0x10000)
 for chip,chip_id,mask in [('8852c',3,15),('8192xb',4,3)]:
  o.uc.mem_write(common,struct.pack('<I',chip_id))
  o.call('init_hal_spec_'+chip,phl,hal)
  assert o.unpack(common+665,'B')[0]==mask,chip
 o.uc.mem_write(sta+30,bytes.fromhex('d05bcbd3ebb2'))
 o.uc.mem_write(sta+2332,bytes(4));o.uc.mem_write(sta+2504,bytes(8))
 o.uc.mem_write(sta+2622,bytes(32));o.word(sta+2656,1<<20)
 for mesh in (False,True):
  o.uc.mem_write(role+8,struct.pack('<I',6 if mesh else 2))
  for bw in range(4):
   o.uc.mem_write(common+665,bytes([(1<<(bw+1))-1]))
   for channel_bw in range(4):
    o.uc.mem_write(sta+44,struct.pack('<I',channel_bw))
    o.uc.mem_write(sta+28,bytes(2));o.uc.mem_write(info,bytes(512))
    o.call('_hal_stainfo_to_macrinfo',hal,sta,info,0,0)
    o.call('mac_h2c_join_info',hal,info)
    assert lib.self_join(0,bw,mesh)==o.unpack(out,'I')[0],(bw,channel_bw)
print('Native JOIN and CAM match original PPE parser, HAL producer and encoders: '+str(count)+' cases')
print('AP/mesh self creation matches original maximum-bandwidth selection: 32 cases')
