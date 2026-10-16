#!/usr/bin/env python3
"""Execute native peer enable then linked PE with a cache from an old BA window."""
from pathlib import Path
import ctypes,hashlib,json,re,struct,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'analysis/python-deps'))
D=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(D/'rx.c').read_text()
if '--before' in sys.argv:source=subprocess.check_output(['git','show','43a8a4c:'+str((D/'rx.c').relative_to(ROOT))],cwd=ROOT,text=True)
a=source.index('void rtl_ax_rx_uplink_enable(');b=source.index('\nvoid rtl_ax_rx_uplink_disable(',a)
common=r'''#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define dma_wmb() ((void)0)
struct ieee80211_key_conf {unsigned keyidx;};
struct rtl_ax_rx {int uplink_lock;void *uplink_states;unsigned uplink_wifi_id;struct ieee80211_key_conf *keys[128];u8 uplink_trigger_tids[128];u16 uplink_pending_tids[128];};
static inline u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
'''
wrapper=r'''unsigned native_enable(void *states) {
 struct ieee80211_key_conf key={.keyidx=0};struct rtl_ax_rx rx={.uplink_states=states,.uplink_wifi_id=1};
 rx.keys[2]=&key;rx.uplink_pending_tids[2]=0xffff;rtl_ax_rx_uplink_enable(&rx,2);return rx.uplink_pending_tids[2];
}
'''
# Reuse only the machine definition. It executes the linked binary under the
# same modeled PCIe/NI boundaries as the existing RX ownership verification.
argv=sys.argv[:];ns={'__file__':str(ROOT/'scripts/test_pe_rxq_backpressure.py')};sys.argv=[argv[0],argv[1]]
exec(compile((ROOT/'scripts/test_pe_rxq_backpressure.py').read_text().split('m=Machine();retained=[]')[0],ns['__file__'],'exec'),ns)
sys.argv=argv
B,MB,ST,VP,NI=ns['B'],ns['MB'],ns['ST'],ns['VP'],ns['NI'];Machine=ns['Machine']
with tempfile.TemporaryDirectory(prefix='rx-ba-reenable-',dir=ROOT/'artifacts') as tmp:
 tmp=Path(tmp);(tmp/'case.c').write_text(common+source[a:b]+wrapper)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-shared','-fPIC',str(tmp/'case.c'),'-o',str(tmp/'case.so')],check=True)
 lib=ctypes.CDLL(str(tmp/'case.so'));lib.native_enable.argtypes=[ctypes.c_void_p];lib.native_enable.restype=ctypes.c_uint
 m=Machine();peer=bytes.fromhex('067afb78b287');ap=bytes.fromhex('02e04c076805');dst=bytes.fromhex('0050c2010200')
 m.u.mem_write(MB+64,ap);m.u.mem_write(B+0xe410,peer);m.put(B+0x11a08,1)
 for i,v in ((88,VP|0xa0000000),(89,NI),(93,1),(106,ST)):m.put(MB+i*4,v)
 # Old cache is ahead of the new BA head by three; it must be delivered to
 # ARM's current reorder window, not forwarded using the retired window.
 for tid in range(16):
  m.u.mem_write(ST+1024+tid*32,struct.pack('<8I',1<<31|0x123,1,0,7,700,9,2,0))
 m.put(ST+1536,0x12345678)
 states=ctypes.create_string_buffer(bytes(m.u.mem_read(ST,0x20000)))
 pending=lib.native_enable(states);m.u.mem_write(ST,states.raw[:0x20000])
 assert m.get(ST+0x10008)==1<<31|1<<8
 assert m.get(ST+1536)==0x12345678
 for tid in range(16):
  assert m.get(ST+1024+tid*32+4)==1 and m.get(ST+1024+tid*32+12)==7
  assert m.get(ST+1024+tid*32+24)==2
 hdr=struct.pack('<HH',0x4188,0)+ap+peer+dst+struct.pack('<H',0x123<<4)+bytes(2)
 iv=bytes([2,0,0,0x20,0,0,0,0]);ip=bytearray(20);ip[0]=0x45;ip[9]=17
 m.arrive(hdr+iv+bytes.fromhex('aaaa030000000800')+ip+bytes(8)+bytes(12),True);m.call();m.inventory()
 forwarded=m.get(MB+90*4);arm=m.get(MB+94*4)
 old='--before' in argv
 assert forwarded==int(old) and arm==int(not old)
 assert pending==(0xffff if old else 0)
 # A repeated call for a peer still owned by PE must preserve its live state.
 m.put(ST+1024,1<<31|0x456);states=ctypes.create_string_buffer(bytes(m.u.mem_read(ST,0x20000)))
 lib.native_enable(states);assert struct.unpack_from('<I',states.raw,1024)[0]==1<<31|0x456
 print(json.dumps({'ARM_source_sha256':hashlib.sha256(source.encode()).hexdigest(),'PE_sha256':hashlib.sha256(ns['binary']).hexdigest(),'old_cached_next_SN':0x123,'new_BA_head_SN':0x120,'stale_cache_forwarded_into_NI':forwarded,'current_window_ARM_deliveries':arm,'RX_counters_and_PN_preserved':True,'active_peer_repeated_enable_preserves_live_SN':True,'other_peer_preserved':True,'scope':'Actual native enable C and linked PE. New BA head is the declared software-window scenario; mac80211 reorder itself is not emulated. PCIe/NI and OS locks are modeled; no hardware packet-loss claim.'},indent=2))
