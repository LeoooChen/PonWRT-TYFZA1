#!/usr/bin/env python3
"""Execute target special-packet initialization and decode its ARP CPU bypass."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from arm64_module_oracle import ModuleOracle

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--ni',type=Path,required=True)
p.add_argument('--reference',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
text=a.reference.read_text()
code='#include <stdint.h>\n#include <string.h>\ntypedef uint32_t ca_uint32_t;typedef uint8_t ca_mac_addr_t[6];\n'
for name in ('cls_ip_addr','cls_mdata','cls_key','cls_action'):
 code+=re.search(r'typedef struct '+name+r'_s \{.*?\}\s*__attribute__\(\(packed\)\)\s*'+name+r'_t;',text,re.S)[0]+'\n'
code+=r'''
unsigned action_offset(void){return 8+sizeof(cls_key_t);}
void decode(const unsigned char *rule,unsigned *v){
 const cls_key_t *k=(void *)(rule+8);const cls_action_t *a=(void *)(rule+8+sizeof(*k));
 v[0]=k->msk_ethertype_enc;v[1]=k->ethertype_enc;
 v[2]=a->dpid_vld;v[3]=a->dpid_pri;v[4]=a->permit;v[5]=a->mc;v[6]=a->mcgid;
 v[7]=a->t2_ctrl_vld;v[8]=a->t2_ctrl;v[9]=a->keep_orig_pkt_vld;v[10]=a->keep_orig_pkt;
 v[11]=a->cos_sel;v[12]=a->cos;
}
'''
with tempfile.TemporaryDirectory() as folder:
 f=Path(folder);(f/'decode.c').write_text(code)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(f/'decode.c'),'-o',str(f/'decode.so')],check=True)
 lib=C.CDLL(str(f/'decode.so'));offset=lib.action_offset()
 events=[];cam={};fault=None
 def boundary(name,args):
  u=ni.uc
  if name=='memset':u.mem_write(args[0],bytes([args[1]&255])*args[2]);return args[0]
  if name=='printk':return 0
  if name=='aal_entry_add':
   assert args[0]==22
   data=bytes(u.mem_read(args[1],20));protocol=int.from_bytes(data[:2],'little');assert data[2]&1
   if protocol==0x0806 and fault=='cam':return 5
   index=len(cam);cam[index+1]=protocol;u.mem_write(args[2],struct.pack('<I',index))
   events.append(dict(kind='cam',protocol=protocol,index=index));return 0
  if name=='aal_l3_cls_add':
   assert args[0]==0
   rule=bytes(u.mem_read(args[2],224));v=(C.c_uint*13)();lib.decode(rule,v)
   fields=dict(zip(('mask_ethertype','ethertype_id','dpid_valid','dpid_priority','permit','multicast','destination','hash_control_valid','hash_control','keep_valid','keep_packet','cos_select','cos'),v))
   fields['side']=args[1];fields['kind']='rule';fields['protocol']=cam.get(v[1]) if not v[0] else None
   fields['rule']=rule.hex();events.append(fields)
   if fields['protocol']==0x0806 and fault=='rule':return 5
   u.mem_write(args[3],struct.pack('<I',len(events)));return 0
  raise AssertionError(('unmodeled initialization call',name,args))
 ni=ModuleOracle(a.ni,['aal_l3_spcl_pkt_init','aal_l3_cls_key_clean'],boundary)
 assert ni.call('aal_l3_spcl_pkt_init')==0
 arp=[e for e in events if e.get('protocol')==0x0806 and e['kind']=='rule'];assert len(arp)==1
 row=arp[0]
 assert {k:row[k] for k in ('side','dpid_valid','dpid_priority','permit','multicast','destination','hash_control_valid','hash_control','keep_valid','keep_packet','cos_select','cos')}==dict(side=0,dpid_valid=1,dpid_priority=1,permit=1,multicast=0,destination=16,hash_control_valid=1,hash_control=15,keep_valid=1,keep_packet=1,cos_select=1,cos=7),row
 # Convert the actual produced action, rather than a constructed expectation.
 def no_external(name,args):raise AssertionError((name,args))
 converter=ModuleOracle(a.ni,['__l3_cls_fib_entry_update'],no_external)
 converter.uc.mem_write(converter.scratch,bytes.fromhex(row['rule'])[offset:])
 converter.uc.mem_write(converter.scratch+4096,bytes(56))
 assert converter.call('__l3_cls_fib_entry_update',converter.scratch,0,converter.scratch+4096)==0
 row['fib_words']=list(converter.unpack(converter.scratch+4096,'7I'))
 failures=[]
 for fault in ('cam','rule'):
  events=[];cam={};assert ni.call('aal_l3_spcl_pkt_init')==5
  failures.append(dict(failed_at=fault,returned=5,last_event={k:v for k,v in events[-1].items() if k!='rule'}))
 report=dict(arp={k:v for k,v in row.items() if k!='rule'},failures=failures,
  sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (a.ni,a.reference)},
  scope='Actual target ARM64 special-packet producer, key cleaner and FIB converter; CAM/classifier allocator calls modeled. WAN ARP CPU16/Hash bypass is proven, LAN policy and physical ingress are not.')
 a.output.write_text(json.dumps(report,indent=2)+'\n')
 print('Original ARP: WAN CPU16, preserved packet, Hash bypass15, COS7; CAM/classifier failures propagate')
