#!/usr/bin/env python3
"""Run Linux policy compiler -> native NI publisher -> linked PE queue/noack selection."""
from pathlib import Path
import sys,subprocess,tempfile,struct,json
r=Path(__file__).resolve().parents[1];pe=Path(sys.argv[1]);kernel=next((r/'sources/openwrt/build_dir').glob('target-*/linux-rtl9617c_generic/linux-6.18.55'))
f=r/'scripts/test_mcast_wifi_policy.py';text=f.read_text();ns={'__file__':str(f)};saved=sys.argv;sys.argv=[str(f),'--kernel',str(kernel)]
exec(compile(text[:text.index("code += r'''\nint main(void)")],str(f),'exec'),ns);sys.argv=saved
code,function=ns['code'],ns['function'];d=r/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
code+=r'''
#include "tx-ownership.h"
struct rtl_ax_tx {bool pe_active;struct {u8 *memory;}pe;};
static int retire_error;static unsigned retired;
static int rtl_ax_tx_ni_retire_peer(struct rtl_ax_tx *tx,unsigned id){(void)tx;assert(id==1);retired++;return retire_error;}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
#define dma_wmb() ((void)0)
'''+function((d/'ni-test.h').read_text(),'rtl_ax_tx_unicast_policy')+r'''
int main(int argc,char **argv){
 assert(argc==2);FILE *f=fopen(argv[1],"wb");assert(f);
 struct wiphy w={.frag_threshold=(u32)-1};struct ieee80211_local local={.hw={4,&w}};
 struct ieee80211_sub_if_data s={.vif={.type=NL80211_IFTYPE_STATION,.addr={2,0,0,0,0,1}},.local=&local};
 struct mac80211_qos_map map={.qos_map={.num_des=1,.dscp_exception={{46,1}}}};
 static u8 memory[0x80000];struct rtl_ax_tx tx={.pe_active=true,.pe={memory}};memory[0xe408]=2;
 u32 header=26<<16;memcpy(memory+0xd028,&header,4);
 for(unsigned mode=0;mode<3;mode++){
  s.noack_map=mode?0xa5:0;s.wmm_acm=mode==1?0xf0:0;peer.reserved_tid=mode==2?1:16;s.qos_map=mode==2?&map:NULL;
  assert(!rtl_ax_tx_unicast_policy(&tx,&s.vif,1));
  assert(fwrite(memory+RTL_AX_TX_PEER_POLICY+128,1,66,f)==66);
  unsigned before=retired;assert(!rtl_ax_tx_unicast_policy(&tx,&s.vif,1)&&retired==before);
 }
 u8 old[66];memcpy(old,memory+RTL_AX_TX_PEER_POLICY+128,66);s.noack_map^=1;retire_error=-EAGAIN;
 assert(rtl_ax_tx_unicast_policy(&tx,&s.vif,1)==-EAGAIN&&!memcmp(old,memory+RTL_AX_TX_PEER_POLICY+128,66));
 retire_error=0;s.u.mgd.tx_tspec[0].admitted_time=640;unsigned before=retired;
 assert(rtl_ax_tx_unicast_policy(&tx,&s.vif,1)==-EOPNOTSUPP&&retired==before+1&&!memcmp(old,memory+RTL_AX_TX_PEER_POLICY+128,66));
 fclose(f);return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-parameter','-Wno-misleading-indentation','-fsanitize=address,undefined','-I'+str(d),'-I'+str(r/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test'),str(p/'policies')],check=True);policies=(p/'policies').read_bytes()
f=r/'scripts/test_pe_ps_selection.py';ns={'__file__':str(f)};sys.argv=[str(f),str(pe)];exec(compile(f.read_text().split('\nrows = []')[0],str(f),'exec'),ns);sys.argv=saved
Radio,call=ns['ns']['Radio'],ns['call'];BASE,MB=ns['BASE'],ns['MB'];PAYLOAD,EPP,BAR=ns['ns']['PAYLOAD'],ns['ns']['EPP'],ns['ns']['BAR']
checks=0
for mode in range(3):
 policy=policies[mode*66:(mode+1)*66]
 for ipv6 in (False,True):
  m=Radio();m.packets([1]*64);m.put(BASE+0xe810,0);m.u.mem_write(BASE+0x69480,policy)
  for ds in range(64):
   prefix=b'\x86\xdd'+bytes([0x60|(ds>>2),(ds&3)<<6]) if ipv6 else b'\x08\x00\x45'+bytes([ds<<2])
   m.u.mem_write(PAYLOAD+ds*2048+80+12,prefix)
  call(m,'ni_ingest')
  for tid in range(8):
   at=m.get(BASE+0x15000+(8+tid)*4);seen=[]
   while at:seen.append((at-PAYLOAD)//2048);at=m.get(at)
   assert seen==[ds for ds in range(64) if policy[ds]==tid],(mode,ipv6,tid,seen)
  for _ in range(64):
   assert call(m,'ni_select')==1
   request=BASE+0x7000;flags=m.get(request+16);tid=flags&7;noack=bool(policy[65]&(1<<tid))
   assert bool(flags&(1<<13))==noack
   assert not noack or not (m.get(request+20)&255)
  checks+=64
 # Inline tagged packets use the independently compiled no-override TID.
 m=Radio();m.packets([1]);m.put(BASE+0xe810,0);m.u.mem_write(BASE+0x69480,policy)
 m.u.mem_write(PAYLOAD+92,b'\x81\x00\xe0\x01');call(m,'ni_ingest')
 assert m.get(BASE+0x15000+(8+policy[64])*4)==PAYLOAD
print(f'Linux compiler -> native publisher -> linked PE: {checks} DSCP/IP/noack selections, tagged policy, retry preservation and TSPEC withdrawal PASS')
