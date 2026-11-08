#!/usr/bin/env python3
"""Compare ordinary AP traffic policy with the exact board driver."""
from pathlib import Path
import sys,ctypes,subprocess,tempfile,itertools,json
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
D=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
M=root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
class Peer(ctypes.Structure):
 _fields_=[(x,ctypes.c_uint32) for x in ['tx_seen','rx_seen','packets_seen','tx_kbits','rx_kbits','smooth_tx','smooth_rx','packets']]+[(x,ctypes.c_uint16) for x in ['rate','max_bytes','amsdu_bytes']]+[(x,ctypes.c_uint8) for x in ['max_num','amsdu_num','vendor','bandwidth','nss','mode','smooth_mode']]+[(x,ctypes.c_bool) for x in ['band5g','he','vht']]
edca=[];busy=0;nss=2;regs={0xc300:0x3f642b,0xc304:0xa44f}
def cb(name,args):
 if name=='rtw_hw_set_edca':edca.append(args[2]);regs[0xc300+args[1]*4]=args[2];return 0
 if name=='rtw_get_channel_busy_time':return busy
 if name=='rtw_get_sta_tx_nss':return nss
 if name=='rtw_phl_read32':return regs.get(args[1],0)
 if name=='_rtw_memcmp':return int(bytes(o.uc.mem_read(args[0],args[2]))==bytes(o.uc.mem_read(args[1],args[2])))
 if name=='printk':return 0
 raise AssertionError((name,args))
o=ModuleOracle(M,['sta_dynamic_control','sta_amsdu_num_by_rate','throughput_dynamic_control','rtw_switch_wmm_mode','rtw_parse_vendor_info','rtw_get_ie','_txreq_lmt_check'],cb)
A,V,S,P,H=0x6000000,0x6040000,0x6080000,0x60c0000,0x6100000
for a in [A,V,S,P,H]:o.uc.mem_map(a,0x40000)
def put(a,v,fmt='I'):
 import struct
 o.uc.mem_write(a,struct.pack('<'+fmt,v))
def setup(p):
 for a in [A,V,S,P,H]:o.uc.mem_write(a,bytes(0x40000))
 o.word(A+0xc0,V);o.word(A+0x20,P);o.word(V+0xb0,A);o.word(V+0x15758,S);o.word(V+0x11c8,H);o.word(S,P)
 put(P+0x4f8,int(p.band5g));put(P+0x2c,p.bandwidth);put(P+0x91c,0x24 if p.he else 0x12 if p.vht else 0xc)
 put(S+0x6068,int(p.he),'B');put(S+0x680c,p.vendor,'B');put(S+0x4d5c,p.rate,'H')
 put(S+0x4ff8,p.tx_kbits);put(S+0x4f68,p.rx_kbits);put(S+0x4ffc,p.smooth_tx);put(S+0x4f6c,p.smooth_rx);put(S+0x4fa4,p.packets)
 put(S+0xa7cc,p.amsdu_num,'B');put(S+0xa7cd,p.max_num,'B')
with tempfile.TemporaryDirectory(dir=root/'artifacts') as t:
 c=Path(t)/'check.c';so=Path(t)/'check.so'
 c.write_text('#include <stdint.h>\n#include <stdbool.h>\ntypedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;\n#include <string.h>\n#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))\n#include "traffic.h"\n#include "vendor.h"\nbool wmm(struct rtl_ax_traffic_wmm *w,const u32 a[4]){return rtl_ax_traffic_wmm_update(w,a); }\nunsigned int vendor(const u8 *a,const u8 *b,unsigned int n){return rtl_ax_vendor(a,b,n);}\nvoid amsdu(struct rtl_ax_traffic_peer *p,u32 pps){rtl_ax_traffic_amsdu(p,pps);}\nu32 quota(struct rtl_ax_traffic_wmm *w,u32 n){return rtl_ax_traffic_wmm_quota(w,n); }\nu32 edca(struct rtl_ax_traffic_peer *p,bool ba,u16 window,u8 flows,u8 busy,bool *old){return rtl_ax_traffic_edca(p,ba,window,flows,busy,old);}')
 subprocess.run(['cc','-shared','-fPIC','-O2','-Wall','-Werror','-I'+str(D),str(c),'-o',str(so)],check=True)
 lib=ctypes.CDLL(str(so));lib.amsdu.argtypes=[ctypes.POINTER(Peer),ctypes.c_uint32];lib.edca.argtypes=[ctypes.POINTER(Peer),ctypes.c_bool,ctypes.c_uint16,ctypes.c_uint8,ctypes.c_uint8,ctypes.POINTER(ctypes.c_bool)];lib.edca.restype=ctypes.c_uint32
 count=0
 for band,width,tx,rx,rate,maximum,previous,pps,packets in itertools.product([False,True],[0,3],[0,100,300,600,1301],[0,30],[0x180,0x19b],[2,4,7],[0,4],[0,100],[0,1000]):
  p=Peer(tx_kbits=tx<<10,rx_kbits=rx<<10,smooth_tx=tx<<10,smooth_rx=rx<<10,rate=rate,max_num=maximum,amsdu_num=previous,packets=packets,band5g=band,bandwidth=width,nss=2,he=True)
  setup(p);put(A+0x9c78,pps);o.call('sta_dynamic_control',A,S)
  p.mode=o.unpack(S+0xacf0,'B')[0];p.smooth_mode=o.unpack(S+0xacf1,'B')[0]
  lib.amsdu(ctypes.byref(p),pps)
  want=(o.unpack(S+0xa7cc,'B')[0],o.unpack(S+0xa7d0,'I')[0])
  assert (p.amsdu_num,p.amsdu_bytes)==want,(band,width,tx,rx,rate,maximum,previous,pps,packets,p.mode,(p.amsdu_num,p.amsdu_bytes),want)
  count+=1
 print('A-MSDU ordinary AP policy:',count,'cases match actual ELF')
 count=0
 for band,he,vht,width,tx,rx,vendor,ba,window,flows,busy,wasbusy in itertools.product([False,True],[False,True],[False,True],[0,2,3],[0,500,1300,1301],[0,400],[0,3,18,20,22,23],[(False),(True)],[64,128],[1,2,3],[0,8,10],[False,True]):
  p=Peer(tx_kbits=tx<<10,rx_kbits=rx<<10,band5g=band,he=he,vht=vht,bandwidth=width,vendor=vendor)
  setup(p);put(S+0x5891,int(ba),'B');put(S+0x5897,window,'B');put(S+0x8604,flows,'B');put(V+0x15760,int(wasbusy),'B')
  edca.clear();o.call('throughput_dynamic_control',A)
  old=ctypes.c_bool(wasbusy);got=lib.edca(ctypes.byref(p),ba,window,flows,busy,ctypes.byref(old))
  assert edca==[got] and old.value==bool(o.unpack(V+0x15760,'B')[0]),(band,he,vht,width,tx,rx,vendor,ba,window,flows,busy,wasbusy,edca,hex(got))
  count+=1
 print('EDCA ordinary AP policy:',count,'cases match actual ELF')

 # Vendor precedence is tested through the actual parser and original OUI tables.
 lib.vendor.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint];lib.vendor.restype=ctypes.c_uint
 count=0
 for name in ['APPLE_OUI','QUALCOMM_OUI','BROADCOM_OUI','OCTOSCOPE_OUI','QINGDAO_PDA_OUI','FIBOCOM_PDA_OUI','VERIWAVE_OUI']:
  a,n=o.symbols[name];table=bytes(o.uc.mem_read(a,n))
  for at in range(0,n,3):
   mac=table[at:at+3]+b'\x01\x02\x03'
   for ies in [b'',b'\xdd\x03'+table[at:at+3],b'\xdd\x03\x00\xa0\xc6',b'\xdd\x03\x00\x05\xb5']:
    setup(Peer());o.uc.mem_write(P+30,mac);o.uc.mem_write(H,ies+bytes(16))
    o.call('rtw_parse_vendor_info',S,H,len(ies));want=o.unpack(S+0x680c,'B')[0]
    got=lib.vendor(mac,ies,len(ies));assert got==want,(name,at,ies.hex(),got,want)
    count+=1
 print('Vendor parser:',count,'table and precedence cases match actual ELF')

 class Wmm(ctypes.Structure):
  _fields_=[('ac_seen',ctypes.c_uint32*4),('timeout',ctypes.c_uint32),('streams',ctypes.c_uint8),('enabled',ctypes.c_bool)]
 lib.wmm.argtypes=[ctypes.POINTER(Wmm),ctypes.POINTER(ctypes.c_uint32)];lib.wmm.restype=ctypes.c_bool
 lib.quota.argtypes=[ctypes.POINTER(Wmm),ctypes.c_uint32];lib.quota.restype=ctypes.c_uint32
 sequences=[[0]*8+[2]*8+[0]*40,[2,0,2,2,0,0,4,3,1,0]*5,[0,1,2,3,4]*10]
 count=0
 for sequence in sequences:
  setup(Peer());w=Wmm();totals=[0]*4;put(A+0x24c00,2048)
  for streams in sequence:
   for i in range(4):
    put(V+0x15790+i*8,totals[i],'Q')
    totals[i]+=501 if i<streams else 500
    put(V+0x15770+i*8,totals[i],'Q')
   o.call('rtw_switch_wmm_mode',A)
   lib.wmm(ctypes.byref(w),(ctypes.c_uint32*4)(*totals))
   assert (w.timeout,w.streams,w.enabled)==(o.unpack(V+0x15764,'I')[0],o.unpack(V+0x15763,'B')[0],bool(o.unpack(V+0x15762,'B')[0]))
   quota=lib.quota(ctypes.byref(w),2048)
   assert quota==(o.unpack(V+0x15768,'I')[0] if w.enabled else 0)
   count+=1
 print('WMM ordinary AP transitions:',count,'calls match actual ELF')

 for streams,tid,pending in itertools.product([1,2,3,4],range(8),[0,511,512,513,682,683,1023,1024,1025,2048]):
  setup(Peer());put(V+0x15868,1,'B');put(V+0x15762,1,'B');put(V+0x15768,2048//streams)
  o.word(H+0x28,tid<<32)
  ac=[0,1,1,0,2,2,3,3][tid];put(S+0xacf8+ac*4,pending)
  result=o.call('_txreq_lmt_check',A,H,S,H+0x100)
  assert result==int(pending<2048//streams) and o.unpack(H+0x100,'B')[0]==ac
 print('WMM quota check:320 pending/TID boundary cases match actual ELF')
