#!/usr/bin/env python3
"""Exercise32 forwarding interfaces with32 independent TYFZA1 counters."""
from pathlib import Path
import hashlib
import argparse
import json
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
native = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe.c').read_text()
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');p.add_argument('--stock',type=Path);args=p.parse_args()
if args.stock:
 from arm64_module_oracle import ModuleOracle
 from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
 import struct
 def boundary(name,values):raise AssertionError((name,values))
 o=ModuleOracle(args.stock,['hw_entry_set','hw_entry_get'],boundary)
 base=0x6000000;o.uc.mem_map(base,65536);o.word(o.symbols['g_ne_reg_iobase'][0],base)
 table=o.symbols['fe_l3e_if_tbl_table'][0];storage=o.scratch+0x2000;payload=o.scratch+0x4000;out=payload+16
 o.word(table+136,storage);events=[];stuck=False;values=[0]*32;pending=0
 def write(u,k,addr,size,value,data):
  global pending
  events.append((addr-base,value))
  if addr==base+0x3000:pending=value
 def read(u,k,addr,size,value,data):
  global pending
  if pending and not stuck:
   if pending&(1<<30):values[pending&31]=o.unpack(base+0x3004,'I')[0]
   else:u.mem_write(base+0x3004,struct.pack('<I',values[pending&31]))
   u.mem_write(addr,struct.pack('<I',pending&~(1<<31)));pending=0
 o.uc.hook_add(UC_HOOK_MEM_WRITE,write,begin=base,end=base+65535)
 o.uc.hook_add(UC_HOOK_MEM_READ,read,begin=base+0x3000,end=base+0x3003)
 for index in range(32):
  word=(1<<23)|(1<<18)|((index%15+1)<<19)|3|(1234<<2)
  o.word(storage+index*24,payload);o.uc.mem_write(payload,struct.pack('<I',word))
  for stuck in (False,True):
   pending=0;events.clear();ret=o.call('hw_entry_set',table,index)&0xffffffff
   assert ret==(0xfffffff5 if stuck else 0)
   assert events==[(0x3004,word),(0x3000,0xc0000000|index)]
   pending=0;events.clear();o.uc.mem_write(out,b'\xa5'*4)
   ret=o.call('hw_entry_get',table,index,out)&0xffffffff
   assert ret==(0xfffffff5 if stuck else 0)
   assert events==[(0x3000,0x80000000|index)]
   assert o.unpack(out,'I')[0]==(0xa5a5a5a5 if stuck else word)
 for index in (32,63,65535):
  for name in ('hw_entry_get','hw_entry_set'):
   events.clear();assert o.call(name,table,index,out)&0xffffffff==0xfffffff7 and not events
 print('Original EIF:128 read/write/success/timeout combinations over32 slots and6 out-of-range rejections PASS')
if args.revision:native=subprocess.check_output(['git','show',args.revision+':port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe.c'],cwd=root,text=True)
def function(name):
    start = re.search(r'^static int ' + name + r'\(', native, re.M).start()
    end = native.index('{', start) + 1
    depth = 1
    while depth:
        depth += (native[end] == '{') - (native[end] == '}')
        end += 1
    return native[start:end]

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include "rtl9617c-fe-pipeline.h"
#define FE_INTERFACES RTL_FE_INTERFACES
#define FE_COUNTERS 32
#define BIT(n) (1U<<(n))
/* aal-77c/include/aal_l3_if.h: fe_l3e_if_tbl_entry_t, SDK field order. */
union sdk_eif {
 unsigned int word;
 struct {unsigned int pppoe_set:1,pppoe_vld:1,pppoe_session_id:16,
  mac_sa_vld:1,mac_sa_an_sel:4,pppoe_len_control:1,reserved:8;} fields;
};
_Static_assert(sizeof(union sdk_eif)==4,"SDK EIF row width");
struct rtl_fe_interface {unsigned int users,saved,source_mac,pppoe;bool counted,ready;};
struct rtl_fe_flow {unsigned int source_mac,counter,pppoe;bool counted,interface_owned;};
/* Keep room for old software's64 owners; the hardware below has32 rows. */
struct rtl_fe {unsigned long counters[1];struct rtl_fe_interface interfaces[64];
 unsigned int table[32],data,access,calls,writes;bool fail_read,fail_write,hold,applied;};
static unsigned int find_first_zero_bit(unsigned long *bits,unsigned int size){
 for(unsigned int i=0;i<size;i++)if(!(bits[i/64]&(1UL<<(i%64))))return i;
 return size;
}
static void __set_bit(unsigned int i,unsigned long *b){b[i/64]|=1UL<<(i%64);}
static void __clear_bit(unsigned int i,unsigned long *b){b[i/64]&=~(1UL<<(i%64));}
static unsigned int fe_read(void *ctx,unsigned int reg){struct rtl_fe *fe=ctx;assert(reg==0x3004||reg==0x3000);return reg==0x3004?fe->data:fe->access;}
static void fe_write(void *ctx,unsigned int reg,unsigned int value){struct rtl_fe *fe=ctx;assert(reg==0x3004);fe->data=value;fe->writes++;}
static int fe_access(void *ctx,unsigned int reg,unsigned int value){
 struct rtl_fe *fe=ctx;fe->calls++;
 assert(reg==0x3000);unsigned int index=value&31;
 if(value&BIT(30)) {
  if(fe->fail_write){fe->fail_write=false;if(fe->hold)fe->access=value;if(fe->applied)fe->table[index]=fe->data;return -ETIMEDOUT;}
  fe->table[index]=fe->data;
 } else {
  if(fe->fail_read){fe->fail_read=false;if(fe->hold)fe->access=value;return -ETIMEDOUT;}
  fe->data=fe->table[index];
 }
 return 0;
}
static struct rtl_dq_io fe_io(struct rtl_fe *fe){return (struct rtl_dq_io){fe,fe_read,fe_write,fe_access};}
'''
test = r'''
int main(void){
 {
  struct rtl_fe fe={};struct rtl_fe_flow flows[33]={};
  for(unsigned i=0;i<33;i++){
   flows[i].source_mac=1;flows[i].pppoe=3U|(i<<2);
   assert(fe_interface_get(&fe,&flows[i])==(i<32?0:-ENOSPC));
   assert(flows[i].interface_owned==(i<32));
  }
  for(unsigned i=0;i<32;i++){
   union sdk_eif row={.word=fe.table[i]};assert(row.fields.pppoe_session_id==i);
   assert(!fe_interface_put(&fe,&flows[i]));
  }
  assert(!fe.counters[0]);
 }
 {
  struct rtl_fe fe={};struct rtl_fe_flow flows[96]={};
  for(unsigned i=0;i<96;i++){
   flows[i].source_mac=i%15;flows[i].pppoe=3U|((i%2?1234U:3456U)<<2);
   assert(!fe_interface_get(&fe,&flows[i]));
   for(unsigned j=0;j<=i;j++){
    union sdk_eif row={.word=fe.table[flows[j].counter&31]};
    assert(row.fields.mac_sa_an_sel==flows[j].source_mac+1 &&
           row.fields.pppoe_session_id==(j%2?1234U:3456U));
   }
  }
  for(unsigned i=0;i<96;i++)assert(!fe_interface_put(&fe,&flows[i]));
  assert(!fe.counters[0]);
 }
 {
  struct rtl_fe fe={};struct rtl_dq_io io=fe_io(&fe);unsigned value=0xa5a5a5a5;
  for(unsigned i=32;i<64;i++){
   assert(rtl_fe_interface_read(&io,i,&value)==-EINVAL&&value==0xa5a5a5a5);
   assert(rtl_fe_interface_write(&io,i,1)==-EINVAL&&!fe.calls&&!fe.writes);
  }
 }
 for(unsigned failure=0;failure<2;failure++)for(unsigned applied=0;applied<2;applied++){
  struct rtl_fe fe={};struct rtl_fe_flow owners[32]={},next={.source_mac=14};
  for(unsigned i=0;i<32;i++){
   fe.table[i]=0x12340000+i;owners[i].source_mac=i==31?14:1;
   if(i==31&&failure==0){fe.fail_write=true;fe.applied=applied;}
   assert(fe_interface_get(&fe,&owners[i])==(i==31&&failure==0?-ETIMEDOUT:0));
  }
  if(failure==1){fe.fail_write=true;fe.applied=applied;assert(fe_interface_put(&fe,&owners[31])==-ETIMEDOUT);}
  unsigned calls=fe.calls,writes=fe.writes;
  assert(fe_interface_get(&fe,&next)==-ENOSPC&&!next.interface_owned&&fe.interfaces[31].users==1);
  assert(fe.calls==calls&&fe.writes==writes);
  assert(!fe_interface_put(&fe,&owners[31])&&fe.table[31]==0x1234001f);
  assert(!fe_interface_get(&fe,&next)&&next.counter==31);
  assert(!fe_interface_put(&fe,&next));
  for(unsigned i=0;i<31;i++)assert(!fe_interface_put(&fe,&owners[i]));
  assert(!fe.counters[0]);
 }
 for(unsigned failure=0;failure<2;failure++){
  struct rtl_fe fe={.hold=true,.applied=true};struct rtl_fe_flow first={.source_mac=1},second={.source_mac=2};
  struct rtl_dq_io io=fe_io(&fe);fe.table[0]=0x12345678;
  fe.fail_read=failure==0;fe.fail_write=failure==1;
  assert(fe_interface_get(&fe,&first)==-ETIMEDOUT);
  assert(first.interface_owned==!!failure);
  unsigned calls=fe.calls,writes=fe.writes,output=0xa5a5a5a5;
  for(unsigned retry=0;retry<3;retry++){
   assert(fe_interface_get(&fe,&second)==-EBUSY&&!second.interface_owned);
   assert(rtl_fe_interface_read(&io,1,&output)==-EBUSY&&output==0xa5a5a5a5);
   if(first.interface_owned)assert(fe_interface_put(&fe,&first)==-EBUSY);
   assert(fe.calls==calls&&fe.writes==writes);
  }
  fe.access=0;if(first.interface_owned)assert(!fe_interface_put(&fe,&first));
  assert(fe.table[0]==0x12345678&&!fe.counters[0]);
 }
 struct rtl_fe fe={};struct rtl_fe_flow *flows=calloc(4096,sizeof(*flows));assert(flows);
 for(unsigned int i=0;i<32;i++)fe.table[i]=0x50000000+i;
 for(unsigned int i=0;i<4096;i++) {
  flows[i].source_mac=i%15;
  flows[i].pppoe=3U|((i%2?1234U:3456U)<<2);
  assert(!fe_interface_get(&fe,&flows[i]));
  assert(fe.interfaces[flows[i].counter].source_mac==i%15);
  assert(fe.interfaces[flows[i].counter].pppoe==flows[i].pppoe);
  assert(flows[i].counted==(i<32));
  union sdk_eif row={.word=fe.table[flows[i].counter]};
  assert(row.fields.pppoe_set==1 && row.fields.pppoe_vld==1);
  assert(row.fields.pppoe_session_id==(i%2?1234U:3456U));
  assert(row.fields.mac_sa_vld==1 && row.fields.mac_sa_an_sel==i%15+1);
  assert(row.fields.pppoe_len_control==1 && !row.fields.reserved);
 }
 struct rtl_fe_flow other_session={.source_mac=0,.pppoe=3U|(65534U<<2)};
 assert(fe_interface_get(&fe,&other_session)==-ENOSPC && !other_session.interface_owned);
 for(unsigned int i=0;i<32;i++)assert(!fe_interface_put(&fe,&flows[i]));
 /* Reusing an EIF for exact counting must not restore it while shared flows remain. */
 struct rtl_fe_flow replacement={.source_mac=0,.pppoe=3U|(3456U<<2)};
 assert(!fe_interface_get(&fe,&replacement) && replacement.counted);
 assert(!fe_interface_put(&fe,&replacement));
 for(unsigned int i=32;i<4096;i++)assert(!fe_interface_put(&fe,&flows[i]));
 assert(!fe.counters[0]);
 for(unsigned int i=0;i<32;i++)assert(!fe.interfaces[i].users && fe.table[i]==0x50000000+i);
 struct rtl_fe_flow failed={.source_mac=3};fe.fail_read=true;
 assert(fe_interface_get(&fe,&failed)==-ETIMEDOUT && !failed.interface_owned && !fe.counters[0]);
 fe.fail_write=true;
 assert(fe_interface_get(&fe,&failed)==-ETIMEDOUT && failed.interface_owned);
 fe.fail_write=true;
 assert(fe_interface_put(&fe,&failed)==-ETIMEDOUT && fe.interfaces[failed.counter].users==1);
 assert(!fe_interface_put(&fe,&failed) && !fe.counters[0]);
 /* Native REMOVE/KEEP rows use the same padding policy without changing SID. */
 for(unsigned int pppoe=0;pppoe<=1;pppoe++) {
  struct rtl_fe_flow plain={.source_mac=14,.pppoe=pppoe};
  assert(!fe_interface_get(&fe,&plain));
  union sdk_eif row={.word=fe.table[plain.counter]};
  assert(row.fields.pppoe_set==pppoe && !row.fields.pppoe_vld);
  assert(!row.fields.pppoe_session_id && row.fields.pppoe_len_control==1);
  assert(row.fields.mac_sa_vld==1 && row.fields.mac_sa_an_sel==15);
  assert(!fe_interface_put(&fe,&plain) && fe.table[0]==0x50000000);
 }
 free(flows);
 puts("Native EIF:4096 flows/15 MACs/two PPPoE sessions,32 interfaces/32 exact MIB owners,session isolation and shared lifetime passed");
 puts("Native EIF:6 applied/unapplied setup/restore/busy cases; incomplete entries never shared and pending window never overwritten");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='fe-interface-') as work:
    path = Path(work) / 'interfaces.c'
    binary = Path(work) / 'interfaces'
    path.write_text(common + ('\n#undef FE_INTERFACES\n#define FE_INTERFACES 64\n' if args.revision else '') + function('fe_interface_get') + function('fe_interface_put') + test)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', '-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'), str(path), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/fe-interface-capacity.json').write_text(json.dumps({
    'production_source_sha256': hashlib.sha256(native.encode()).hexdigest(),
    'flows': 4096, 'source_MACs': 15, 'forwarding_interfaces': 32, 'exact_MIB_owners': 32,
    'PPPoE_sessions_isolated_with_identical_source_MAC': True,
    'SDK_EIF_fields_and_exclude_padding_policy': True,
    'shared_interface_refcounts_and_last_owner_restore': True,
    'counter_reassignment_without_cross_flow_accounting': True,
    'failed_register_restore_retains_owner_until_retry': True,
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
