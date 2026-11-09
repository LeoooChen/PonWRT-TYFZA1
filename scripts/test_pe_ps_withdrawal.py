#!/usr/bin/env python3
"""Execute PS grants revoked after NI selection, including generation and BID lifetime."""
from pathlib import Path
import struct
import itertools
import subprocess
import tempfile
root=Path(__file__).resolve().parents[1]
f=root/'scripts/test_fttr_ni_pe_drain.py'
e={'__file__':str(f),'__name__':'ps_withdrawal_fixture'}
exec(compile(f.read_text().split('cases = 0',1)[0],str(f),'exec'),e)
machine,base,mb,ni,bar,payload,table,requests,DRAIN=(e[n] for n in ('machine','base','mb','ni','bar','payload','table','requests','DRAIN'))
DONE=base+0x1ba08
cases=0
snapshots=[]
for generation in (0,17,0xffffffff):
 for cause,uapsd in itertools.product(('route','peer','global','fttr','mcast'),(False,True)):
  for blocked in (False,True):
   for newer in (False,True):
    m=machine(cookie=cause=='mcast');m.u.mem_write(payload+64+m.header+15,b'\0')
    if cause=='mcast':m.u.mem_write(table+16+9*96+4,struct.pack('<I',1|3<<8|6<<16))
    m.ingest()
    tid=3 if cause=='mcast' else 0
    m.put(base+0xe810,1);m.put(base+0xe814,generation);m.put(base+0xe81c,(1<<tid)<<16|int(uapsd))
    m.put(base+0x19e04,2);m.put(base+0x19804,10)
    assert m.call('ni_select',mb,base,requests)==1
    assert m.get(base+0xe818)==2 and m.get(base+0x20004)&(1<<16)
    assert bool(m.get(requests+16)&(1<<20))==uapsd # U-APSD carries EOSP; PS-Poll still ends the grant
    m.put(mb+193*4,1)
    if blocked:
     m.put(bar+0x1058,1<<16);m.call('pe_tx_publish',mb,8,requests)
     assert not m.get(mb+194*4) and not m.get(DONE+4)
    current=(generation+1)&0xffffffff if newer else generation
    if newer:
     m.put(base+0xe814,current);m.put(base+0x19e04,4)
    m.put(DONE,current);m.put(DONE+4,42)
    if cause=='route':m.put(base+0x11a04,0)
    elif cause=='peer':m.put(mb+62*4,1)
    elif cause=='global':m.put(mb+62*4,128)
    elif cause=='fttr':m.put(table+4,3)
    else:m.put(table,0)
    event=m.get(mb+67*4)
    m.call('pe_tx_publish',mb,8,requests)
    assert m.get(mb+67*4)==event+(not newer)
    assert m.get(mb+194*4)==1 and not m.get(mb+10*4)
    assert m.get(DONE)==current and m.get(DONE+4)==42+(not newer),(generation,cause,blocked,newer)
    # ni_ps_scan has already ACKed this command; it cannot repair a missing
    # selected-packet completion. Repeated publication must not notify twice.
    m.call('pe_tx_publish',mb,8,requests)
    assert m.get(DONE+4)==42+(not newer)
    quota=base+0x3e010
    assert m.get(quota)==1 and m.get(DRAIN+8)==1
    m.call('ni_recycle',mb,base)
    assert m.get(quota)==1 and m.get(base+0x20000)==payload
    m.put(ni+0x6340,(1<<31)|1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
    assert not m.get(quota) and not m.get(DRAIN+8) and not m.get(mb+30*4)
    assert m.get(DONE+4)==42+(not newer)
    snapshots.append((current,m.get(DONE),m.get(DONE+4),not newer))
    cases+=1
# Real RPQ still completes a submitted grant (success or radio discard),
# without allowing its old generation to overwrite a newer period's record.
for generation,newer,status in itertools.product((0,17,0xffffffff),(False,True),(0,4)):
 m=machine(cookie=False);m.u.mem_write(payload+64+m.header+15,b'\0');m.ingest()
 m.put(base+0xe810,1);m.put(base+0xe814,generation);m.put(base+0xe81c,1<<16|1)
 m.put(base+0x19e04,2);m.put(base+0x19804,10)
 assert m.call('ni_select',mb,base,requests)==1
 m.put(mb+193*4,1);m.call('pe_tx_publish',mb,8,requests)
 assert m.get(mb+10*4)==1 and not m.get(DONE+4)
 current=(generation+1)&0xffffffff if newer else generation
 m.put(base+0xe814,current);m.put(DONE,current);m.put(DONE+4,42)
 tag=m.get(e['PAGES']+56)&4095
 raw=struct.pack('<II',24|0xc000|1<<16,7<<24|4)+bytes(12)+struct.pack('<I',1|status<<13|tag<<16)
 m.u.mem_write(e['REPORTS'],raw);m.put(bar+0x121c,1<<16)
 m.call('pe_rpq',mb,base)
 assert m.get(DONE)==current and m.get(DONE+4)==42+(not newer)
 assert m.get(base+0x20004)&(1<<31) and m.get(base+0x3e010)==1
 m.call('pe_rpq',mb,base);assert m.get(DONE+4)==42+(not newer)
 m.put(ni+0x6340,(1<<31)|1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
 assert not m.get(base+0x3e010) and not m.get(DRAIN+8)
 snapshots.append((current,m.get(DONE),m.get(DONE+4),not newer));cases+=1
print(f'PS withdrawal: {cases} selected grant, credit stall, stale/current generation, completion and BID/quota cases passed')

# Consume these exact PE output records with the production ARM dispatcher
# and mac80211 generation-aware EOSP, including duplicate dispatch.
f=root/'scripts/test_wifi_ps_generation.py'
e={'__file__':str(f),'__name__':'ps_arm_fixture'}
exec(compile(f.read_text().split("code += r'''\nstatic pthread_barrier_t",1)[0],str(f),'exec'),e)
code=e['code']
code+='static const u32 records[][4]={'+','.join('{'+','.join(str(int(v))+'U' for v in row)+'}' for row in snapshots)+'};\n'
code+=r'''
int main(void){
 struct ieee80211_local local={};struct sta_info sta={.sta={.id=1},.local=&local,.ps_lock=PTHREAD_MUTEX_INITIALIZER};
 static u8 memory[0x80000];struct rtl_ax_tx tx={.pe_active=true,.lock=PTHREAD_MUTEX_INITIALIZER,.pe={memory}};
 lookup[1]=&sta;memory[0xe408]=1;
 /* Include both production setup functions in this bridge fixture. */
 start(&sta);wake(&sta);rtl_ax_tx_peer_qos(&tx,1,true,10);memory[0xe408]=1;
 rtl_ax_tx_peer_sleep(&tx,1,true);rtl_ax_tx_ps_release(&tx,&sta.sta,1,1,false,false,0);ni_ps_retire(&tx);
 for(unsigned i=0;i<sizeof(records)/sizeof(records[0]);i++){
  const u32 *r=records[i];sta.ps_generation=r[0];sta.service_period=true;
  tx.ni.ps_issued[1]=true;tx.ni.ps_seen[1][1]=42;tx.ps_eosp=0;
  put_unaligned_le32(r[0],memory+0xe814);
  put_unaligned_le32(r[1],memory+RTL_AX_NI_PS_DONE+8);put_unaligned_le32(r[2],memory+RTL_AX_NI_PS_DONE+12);
  ni_ps_done(&tx,1);
  assert(tx.ps_eosp==r[3] && sta.service_period==!r[3] && tx.ni.ps_issued[1]==!r[3]);
  ni_ps_done(&tx,1);assert(tx.ps_eosp==r[3]);
 }
 puts("Actual PE records -> ARM ni_ps_done -> mac80211 EOSP:132 exact/current, stale and duplicate completion cases passed");
}
'''
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'arm.c').write_text(code)
 subprocess.run(['cc','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined',
  '-I'+str(e['driver']),str(p/'arm.c'),'-o',str(p/'arm')],check=True)
 subprocess.run([str(p/'arm')],check=True)
