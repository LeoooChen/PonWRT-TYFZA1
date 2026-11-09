#!/usr/bin/env python3
"""Execute native FDB acquisition/rollback with applied timeout and result faults."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');p.add_argument('--stock',type=Path);args=p.parse_args()
if args.stock:
 from arm64_module_oracle import ModuleOracle
 from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
 import struct
 locked=0
 def boundary(name,values):
  global locked
  if name=='ca_spin_lock':assert not locked;locked=1;return 0
  if name=='ca_spin_unlock':assert locked;locked=0;return 0
  raise AssertionError((name,values))
 o=ModuleOracle(args.stock,['__aal_fdb_access.constprop.0'],boundary)
 base=0x6000000;o.uc.mem_map(base,65536);o.word(o.symbols['g_ne_reg_iobase'][0],base)
 events=[];pending=False;status=5;stuck=False
 def write(u,k,addr,size,value,data):
  global pending
  events.append((addr-base,value))
  if addr==base+0x1ca0:pending=True
 def read(u,k,addr,size,value,data):
  global pending
  if pending and not stuck:
   u.mem_write(addr,struct.pack('<I',o.unpack(addr,'I')[0]&~(1<<31)))
   u.mem_write(base+0x1c2c,struct.pack('<I',(17<<4)|status));pending=False
 o.uc.hook_add(UC_HOOK_MEM_WRITE,write,begin=base,end=base+65535)
 o.uc.hook_add(UC_HOOK_MEM_READ,read,begin=base+0x1ca0,end=base+0x1ca3)
 data=o.scratch+0x2000;result=data+32;words=[0x12345678,0x87654321,0x11112222,0x33334444]
 cases=0
 for command in (4,0x45,0x85):
  for status in (4,5,10,11,15):
   pending=False;events.clear();o.uc.mem_write(base+0x1ca0,bytes(4));o.uc.mem_write(data,struct.pack('<4I',*words))
   # Compiler removed four unused WORD pointers: x3..x6 are DATA3..DATA0.
   ret=o.call('__aal_fdb_access.constprop.0',0,command,0,data+12,data+8,data+4,data,result)
   assert ret==0 and o.unpack(result,'I')[0]==(17<<4)|status
   assert events==[(0x1c2c,0)]+[(0x1cb0-i*4,v) for i,v in enumerate(words)]+[(0x1ca0,(1<<31)|command)]
   cases+=1
  pending=False;events.clear();o.uc.mem_write(base+0x1ca0,struct.pack('<I',1<<31))
  assert o.call('__aal_fdb_access.constprop.0',0,command,0,data+12,data+8,data+4,data,result)==11 and not events
 print('Original FDB:15 command/status pass-through cases and3 busy-before-staging cases PASS')
fixture=root/'scripts/test_fe_mcast_integration.py';ns={'__file__':str(fixture)}
exec(compile(fixture.read_text().split("code += r'''\nint main(void)",1)[0],str(fixture),'exec'),ns)
code=ns['code'];drivers=ns['drivers'];native=(drivers/'rtl9617c-fe.c').read_text()
if args.revision:native=subprocess.check_output(['git','show',args.revision+':'+str((drivers/'rtl9617c-fe.c').relative_to(root))],cwd=root,text=True)
def fn(name):
 a=re.search(r'^static int '+name+r'\([^;]*?\)\s*\{',native,re.M).start();b=native.index('{',a)+1;depth=1
 while depth:depth+=(native[b]=='{')-(native[b]=='}');b+=1
 return native[a:b]+'\n'
code=code.replace('struct rtl_fe {','struct rtl_fe {unsigned fdb[4],fdb_calls,fdb_fail,fdb_result,fdb_result_at;bool fdb_present,fdb_applied;')
code=code.replace('if(reg==0x3928){',r'''
 if(reg==0x1ca0){
  unsigned op=command&255;f->fdb_calls++;
  bool apply=f->fdb_applied||f->fdb_calls!=f->fdb_fail;
  unsigned result=5;
  if(op==4){result=f->fdb_present?5:15;if(f->fdb_present)for(unsigned i=0;i<4;i++)write_reg(f,0x1cb0-i*4,f->fdb[i]);}
  else if(op==0x45){if(apply){for(unsigned i=0;i<4;i++)f->fdb[i]=read_reg(f,0x1cb0-i*4);f->fdb_present=true;}}
  else {assert(op==0x85);result=f->fdb_present?5:15;if(apply)f->fdb_present=false;}
  if(f->fdb_calls==f->fdb_fail){f->reg[reg/4]=command;return -ETIMEDOUT;}
  if(f->fdb_calls==f->fdb_result_at)result=f->fdb_result;
  write_reg(f,0x1c2c,(17<<4)|result);return 0;
 }
 if(reg==0x3928){''')
code=re.sub(r'static int fe_destination_get\([^\n]*','static int fe_destination_get(struct rtl_fe *,struct rtl_fe_flow *,const u8 *);',code)
code=re.sub(r'static int fe_destination_put\([^\n]*','static int fe_destination_put(struct rtl_fe *,struct rtl_fe_flow *);',code)
code+='\n#define ether_addr_equal(a,b) (!memcmp(a,b,6))\n#define list_for_each_entry(p,h,m) for(struct list_head *c=(h)->next;c!=(h) && ((p)=container_of(c,__typeof__(*(p)),m));c=c->next)\n'+fn('fe_destination_get')+fn('fe_destination_put')
code+=r'''
static struct rtl_fe_flow *add(struct rtl_fe *fe,struct rtl_fe_port *port,unsigned index){
 struct rtl_fe_flow *f=calloc(1,sizeof(*f));assert(f);f->input=f->output=port;f->hash_index=index;
 INIT_LIST_HEAD(&f->retry);list_add_tail(&f->list,&fe->flows);__set_bit(index,fe->hashes);return f;
}
int main(void){unsigned cases=0;const u8 mac[6]={2,3,4,5,6,7};
 for(unsigned existing=0;existing<2;existing++)for(unsigned applied=0;applied<2;applied++)for(unsigned stage=2;stage<=3;stage++){
  struct rtl_fe fe={};struct rtl_fe_port port={.fe=&fe};
  fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);
  unsigned original[4];rtl_fe_fdb_key(original,mac);original[0]&=~(BIT(9)|BIT(19)|BIT(20)|BIT(21));
  fe.fdb_present=existing;memcpy(fe.fdb,original,16);fe.fdb_fail=stage;fe.fdb_applied=applied;
  struct rtl_fe_flow *first=add(&fe,&port,0),*second=add(&fe,&port,1);
  assert(fe_destination_get(&fe,first,mac)==-ETIMEDOUT&&first->destination_owned);
  assert(fe_delete(&fe,first)==-EBUSY&&test_bit(0,fe.hashes));
  unsigned calls=fe.fdb_calls;assert(fe_destination_get(&fe,second,mac)==-EBUSY&&!second->destination_owned&&fe.fdb_calls==calls);
  fe.reg[0x1ca0/4]=0;fe.fdb_fail=0;
  assert(!fe_delete(&fe,first)&&fe.fdb_present==!!existing);
  if(existing)assert(!memcmp(fe.fdb,original,16));
  assert(!fe_destination_get(&fe,second,mac)&&second->destination_index==17);
  struct rtl_fe_flow *third=add(&fe,&port,2);calls=fe.fdb_calls;
  assert(!fe_destination_get(&fe,third,mac)&&fe.fdb_calls==calls);
  assert(!fe_delete(&fe,second)&&fe.fdb_calls==calls);
  fe.fdb_result_at=fe.fdb_calls+1;fe.fdb_result=10;
  assert(fe_delete(&fe,third)==-EIO&&third->destination_owned&&test_bit(2,fe.hashes));
  struct rtl_fe_flow *fourth=add(&fe,&port,3);assert(fe_destination_get(&fe,fourth,mac)==-EBUSY);
  fe.fdb_result_at=0;assert(!fe_delete(&fe,third));
  assert(fe.fdb_present==!!existing);if(existing)assert(!memcmp(fe.fdb,original,16));
  assert(!fe_delete(&fe,fourth));assert(list_empty(&fe.flows)&&list_empty(&fe.retries));free(fe.memory);cases++;
 }
 for(unsigned result=0;result<16;result++)if(result!=5){
  struct rtl_fe fe={};struct rtl_fe_port port={.fe=&fe};fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);
  INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);struct rtl_fe_flow *f=add(&fe,&port,0);
  fe.fdb_result_at=1;fe.fdb_result=result;int ret=fe_destination_get(&fe,f,mac);
  if(result==15)assert(!ret&&f->destination_owned);else assert(ret==-EIO&&!f->destination_owned&&fe.fdb_calls==1);
  fe.fdb_result_at=0;assert(!fe_delete(&fe,f));free(fe.memory);cases++;
 }
 /* Full/limit results may have updated the entry: retain cleanup ownership. */
 for(unsigned result=4;result<=11;result++)if(result==4||result==10||result==11){
  struct rtl_fe fe={};struct rtl_fe_port port={.fe=&fe};fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);
  INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);struct rtl_fe_flow *f=add(&fe,&port,0);
  fe.fdb_result_at=2;fe.fdb_result=result;assert(fe_destination_get(&fe,f,mac)==-ENOSPC&&f->destination_owned);
  fe.fdb_result_at=0;assert(!fe_delete(&fe,f)&&!fe.fdb_present);free(fe.memory);cases++;
 }
 printf("Destination FDB: %u existing/new/applied/timeout/result cases; retained snapshot, no unready sharing, busy retry and eventual exact restoration PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='fdb-ownership-') as tmp:
 d=Path(tmp);(d/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',
                 '-I'+str(drivers),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
