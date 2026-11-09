#!/usr/bin/env python3
"""Compare native role identity fields with actual HAL/MAC chain outputs."""
import argparse,ctypes as C,json,subprocess,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];d=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,required=True);a=p.parse_args();original=json.loads(a.original.read_text())
prefix=r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;
#define RTL_AX_AP_TRANSPORT_H
#define ETH_ALEN 6
#define BIT(n) (1U<<(n))
#define RTL_AX_BIT(n) BIT(n)
#define ARRAY_SIZE(a) (sizeof(a)/sizeof(*(a)))
#define IEEE80211_HE_PHY_CAP6_PPE_THRESHOLD_PRESENT 128
#define IEEE80211_HE_PHY_CAP9_NOMINAL_PKT_PADDING_POS 6
#define IEEE80211_HE_MAC_CAP1_TF_MAC_PAD_DUR_MASK 12
struct ieee80211_sta_he_cap {bool has_he;struct{u8 phy_cap_info[11],mac_cap_info[6];}he_cap_elem;u8 ppe_thres[25];};
static void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
'''
code=prefix+''.join('#include "'+str(d/f)+'"\n' for f in ('role.h','beacon.h','role-identity.h'))+r'''
void run(u8 *out,unsigned port,unsigned mbid,unsigned kind,unsigned macid,unsigned chip){
 struct rtl_ax_role_identity id={port,mbid,macid,kind==2?1:mbid?3:2};
 struct ieee80211_sta_he_cap he={};u8 self[6]={0xd0,0x5b,0xcb,0xd3,0xeb,0xb2},peer[6]={0x3a,0x2e,0xa7,0x75,0x9a,0xf2};
 memset(out,0,152);
 rtl_ax_store32(out,macid|(kind==2?0x3000:kind?0x4a00:0x4100));rtl_ax_identity_maintain(out,&id);
 if(!kind)rtl_ax_role_join(out+4,macid,0,false);
 else rtl_ax_station_join(out+4,macid,0,&he,1,false,kind==2);
 rtl_ax_identity_join(out+4,&id);
 rtl_ax_peer_cam(out+8,macid,self,peer,0,true);rtl_ax_identity_cam(out+8,&id);
 rtl_ax_station_cmac_connected(out+68,macid,chip==0,0);if(kind==2)rtl_ax_station_cmac_client(out+68);
 rtl_ax_identity_cmac(out+68,&id);
 rtl_ax_beacon_header(out+136,12,4,0);rtl_ax_identity_beacon(out+136,&id);
}
void masked(u8 *out,unsigned port,unsigned mbid){struct rtl_ax_role_identity id={port,mbid,1,2};rtl_ax_identity_cmac(out,&id);}
'''
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'test.c';so=Path(temp)/'test.so';c.write_text(code)
 subprocess.run(['cc','-shared','-fPIC','-O2','-Wall','-Wextra','-Werror',str(c),'-o',str(so)],check=True)
 lib=C.CDLL(str(so));lib.run.argtypes=[C.c_void_p]+[C.c_uint]*5;lib.masked.argtypes=[C.c_void_p,C.c_uint,C.c_uint]
 for row in original['roles']:
  out=C.create_string_buffer(152);lib.run(out,row['port'],row['mbid'],row['kind'],row['macid'],0)
  assert int.from_bytes(out.raw[:4],'little')==row['maintain'],(row,out.raw[:4].hex())
  assert int.from_bytes(out.raw[4:8],'little')==row['join'],(row,out.raw[4:8].hex())
  assert int.from_bytes(out.raw[40:44],'little')==row['cam_port'],(row,out.raw[40:44].hex())
 for row in original['cctl']:
  out=C.create_string_buffer(152);lib.run(out,row['port'],row['mbid'],row['kind'],row['macid'],row['chip']=='8192xb')
  assert out.raw[68:136]==bytes.fromhex(row['wire']),(row,out.raw[68:136].hex())
 for row in original['beacons']:
  out=C.create_string_buffer(152);lib.run(out,row['port'],row['mbid'],0,row['macid'],row['chip']=='8192xb')
  assert out.raw[136:]==bytes.fromhex(row['wire'])
 # Partial RA updates must not inadvertently change an unmasked port/MBID.
 for mask in (0,7,0xf0,0xf7):
  for port in range(5):
   for mbid in range(8):
    raw=bytearray([0xa5]*68);raw[52:56]=mask.to_bytes(4,'little');out=C.create_string_buffer(bytes(raw),68)
    lib.masked(out,port,mbid);expected=bytearray(raw)
    expected[20:24]=((0xa5a5a5a5&~mask)|((port|mbid<<4)&mask)).to_bytes(4,'little')
    assert out.raw==expected
print('Native role identity matches 36 JOIN/CAM, 72 CCTL, 24 beacon chains; 160 partial-mask cases passed')

# Actual beacon upload, not just the header encoder: retain state on H2C failure.
pc=(d/'pci.c').read_text();start=pc.index('static int rtl_ax_beacon_update(');end=pc.index('\n}\n',start)+3
upload=prefix+'#include "'+str(d/'beacon.h')+'"\n#include "'+str(d/'role-identity.h')+'"\n'+r'''
#include <assert.h>
#include <stdlib.h>
#include <errno.h>
#define GFP_KERNEL 0
#define RTL_AX_H2C_LONG_DATA 2
#define kmalloc(n,g) malloc(n)
#define kfree(p) free(p)
static unsigned beacon_tx_ant,commands;static bool fail_alloc;static int error;
static void *allocate(unsigned n){return fail_alloc?NULL:malloc(n);}
#undef kmalloc
#define kmalloc(n,g) allocate(n)
struct rtl_ax_ap {unsigned beacon_bytes;u32 beacon_header[4];};
static u8 sent[716];static unsigned sent_bytes;
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,const void *p,unsigned bytes,bool ack){
 (void)r;assert(q==2&&cat==1&&cls==5&&fn==16&&!ack);memcpy(sent,p,bytes);sent_bytes=bytes;commands++;return error;
}
'''+pc[start:end]+r'''
int main(void){
 u8 frame[700];for(unsigned i=0;i<700;i++)frame[i]=i;
 for(unsigned port=0;port<5;port++)for(unsigned mbid=0;mbid<(port?1:8);mbid++)for(unsigned failed=0;failed<2;failed++){
  struct rtl_ax_ap r={.beacon_bytes=37,.beacon_header={1,2,3,4}};
  struct rtl_ax_role_identity id={port,mbid,17+mbid,mbid?3:2};
  error=failed?-ETIMEDOUT:0;commands=0;
  assert(rtl_ax_beacon_update(&r,frame,700,36,4,&id)==error&&commands==1);
  assert(sent_bytes==716&&!memcmp(sent+16,frame,700));
  assert(sent[0]==port&&sent[1]==mbid&&sent[2]==0&&sent[4]==17+mbid);
  if(failed)assert(r.beacon_bytes==37&&r.beacon_header[0]==1);
  else assert(r.beacon_bytes==700&&!memcmp(r.beacon_header,sent,16));
 }
 struct rtl_ax_ap r={};struct rtl_ax_role_identity id={};commands=0;
 assert(rtl_ax_beacon_update(&r,frame,701,36,4,&id)==-E2BIG&&!commands);
 assert(rtl_ax_beacon_update(&r,frame,64,152,4,&id)==-E2BIG&&!commands);
 fail_alloc=true;assert(rtl_ax_beacon_update(&r,frame,64,36,4,&id)==-ENOMEM&&!commands);
}
'''
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'upload.c';binary=Path(temp)/'upload';c.write_text(upload)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
print('Actual beacon upload: 24 role/error cases, full payload, bounds and allocation failure passed')

monitor=(d/'monitor.h').read_text()
start=monitor.index('static int rtl_ax_ap_template(');end=monitor.index('\n}\n',start)
assert '&((struct rtl_ax_vif *)vif->drv_priv)->identity' in monitor[start:end]
assert 'hw->vif_data_size = sizeof(struct rtl_ax_vif);' in monitor
