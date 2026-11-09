#!/usr/bin/env python3
"""Execute original MTU layout and native get/delete rollback with lost writes."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');p.add_argument('--stock',type=Path);a=p.parse_args()
if a.stock:
 from arm64_module_oracle import ModuleOracle
 from unicorn import UC_HOOK_MEM_WRITE
 import struct
 def boundary(name,args):raise AssertionError((name,args))
 o=ModuleOracle(a.stock,['aal_l3pe_mtu_size_set','aal_l3pe_mtu_size_get'],boundary)
 base=0x6000000;o.uc.mem_map(base,65536);o.word(o.symbols['g_ne_reg_iobase'][0],base)
 writes=[];o.uc.hook_add(UC_HOOK_MEM_WRITE,lambda u,k,addr,n,value,d:writes.append((addr-base,value)),begin=base,end=base+65535)
 cases=0
 for index in range(16):
  reg=0x3520+index//2*4;shift=index%2*16
  for before in (0,0xffffffff,0x98765432):
   for size in (0,1,1492,1500,16383):
    o.uc.mem_write(base+reg,struct.pack('<I',before));writes.clear()
    assert o.call('aal_l3pe_mtu_size_set',index,size)==0
    assert writes==[(reg,(before&~(0x3fff<<shift))|(size<<shift))]
    assert o.call('aal_l3pe_mtu_size_get',index,o.scratch)==0 and o.unpack(o.scratch,'I')[0]==size;cases+=1
 for index,size in ((16,1500),(255,1500),(0,16384)):
  writes.clear();assert o.call('aal_l3pe_mtu_size_set',index,size)&0xffffffff in (0xfffffff7,0xfffffff4) and not writes
 print(f'Original MTU: {cases} paired-halfword setters/getters and3 invalid bounds PASS')

f=root/'scripts/test_fe_mcast_integration.py';ns={'__file__':str(f)}
exec(compile(f.read_text().split("code += r'''\nint main(void)",1)[0],str(f),'exec'),ns)
code=ns['code'];drivers=ns['drivers'];native=(drivers/'rtl9617c-fe.c').read_text()
if a.revision:native=subprocess.check_output(['git','show',a.revision+':'+str((drivers/'rtl9617c-fe.c').relative_to(root))],cwd=root,text=True)
def fn(name):
 start=re.search(r'^static (?:int|void) '+name+r'\([^;]*?\)\s*\{',native,re.M).start();end=native.index('{',start)+1;depth=1
 while depth:depth+=(native[end]=='{')-(native[end]=='}');end+=1
 return native[start:end]+'\n'
code=code.replace('struct rtl_fe {','struct rtl_fe_mtu {unsigned users,size,saved;bool ready;};\nstruct rtl_fe {struct rtl_fe_mtu mtus[16];unsigned fault,mtu_writes;')
code=code.replace('((struct rtl_fe *)p)->reg[reg/4]=val;', '''struct rtl_fe *f=p;
 if(reg>=0x3520&&reg<=0x353c){f->mtu_writes++;if(f->fault==1)return;if(f->fault==2)val^=1U|1U<<16;}
 f->reg[reg/4]=val;''')
code=re.sub(r'static int fe_mtu_get\([^\n]*','static int fe_mtu_get(struct rtl_fe *,struct rtl_fe_flow *,unsigned);',code)
code=re.sub(r'static int fe_mtu_put\([^\n]*','static int fe_mtu_put(struct rtl_fe *,struct rtl_fe_flow *);',code)
code+='\n#define fe_read read_reg\n#define fe_write write_reg\n'+fn('fe_mtu_get')
if a.revision:
 code+=fn('fe_mtu_put').replace('fe_mtu_put(', 'old_mtu_put(')+'static int fe_mtu_put(struct rtl_fe *f,struct rtl_fe_flow *v){old_mtu_put(f,v);return 0;}\n'
else:code+=fn('fe_mtu_put')
code+=r'''
static struct rtl_fe_flow *add(struct rtl_fe *fe,struct rtl_fe_port *port,unsigned index){
 struct rtl_fe_flow *f=calloc(1,sizeof(*f));assert(f);f->input=f->output=port;f->hash_index=index;
 INIT_LIST_HEAD(&f->retry);list_add_tail(&f->list,&fe->flows);__set_bit(index,fe->hashes);return f;
}
int main(void){unsigned cases=0;
 for(unsigned index=0;index<16;index++)for(unsigned fault=1;fault<=2;fault++){
  struct rtl_fe fe={};struct rtl_fe_port port={.fe=&fe};
  INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);
  for(unsigned i=0;i<index;i++)fe.mtus[i]=(struct rtl_fe_mtu){.users=1,.size=9000+i,.ready=true};
  unsigned reg=0x3520+(index/2)*4,shift=(index%2)*16,mask=0x3fffU<<shift;
  fe.reg[reg/4]=0x82348123;
  unsigned original=fe.reg[reg/4],saved=(original>>shift)&0x3fff;
  struct rtl_fe_flow *first=add(&fe,&port,0),*second=add(&fe,&port,1);
  fe.fault=fault;
  assert(fe_mtu_get(&fe,first,1492)==-EIO&&first->mtu_owned&&first->mtu_index==index);
  assert(fe.mtus[index].users==1&&fe.mtus[index].saved==saved);
  unsigned writes=fe.mtu_writes;assert(fe_mtu_get(&fe,second,1492)==-EBUSY&&!second->mtu_owned&&fe.mtu_writes==writes);
  fe.fault=0;assert(!fe_delete(&fe,first)&&!fe.mtus[index].users);
  /* A corruption injection may also change the adjacent half. Restoration
   * owns only its14 bits and must preserve the current neighbor/reserved bits. */
  assert(((fe.reg[reg/4]>>shift)&0x3fff)==saved);
  assert(!fe_mtu_get(&fe,second,1492));
  struct rtl_fe_flow *third=add(&fe,&port,2);
  writes=fe.mtu_writes;assert(!fe_mtu_get(&fe,third,1492)&&fe.mtu_writes==writes&&fe.mtus[index].users==2);
  assert(!fe_delete(&fe,second)&&fe.mtus[index].users==1);
  fe.fault=fault;unsigned neighbor=fe.reg[reg/4]&~mask;
  assert(fe_delete(&fe,third)==-EIO&&third->mtu_owned&&third->deleting&&test_bit(2,fe.hashes)&&fe.mtus[index].users==1);
  struct rtl_fe_flow *fourth=add(&fe,&port,3);assert(fe_mtu_get(&fe,fourth,1492)==-EBUSY);
  fe.fault=0;neighbor=fe.reg[reg/4]&~mask;
  assert(!fe_delete(&fe,third)&&!fe.mtus[index].users&&(fe.reg[reg/4]&~mask)==neighbor);
  assert(((fe.reg[reg/4]>>shift)&0x3fff)==saved);
  assert(!fe_mtu_get(&fe,fourth,1500)&&!fe_delete(&fe,fourth));
  assert(list_empty(&fe.flows)&&list_empty(&fe.retries));free(fe.memory);cases++;
 }
 struct rtl_fe fe={};struct rtl_fe_flow flows[17]={};
 for(unsigned i=0;i<16;i++)assert(!fe_mtu_get(&fe,&flows[i],1000+i));
 unsigned writes=fe.mtu_writes;assert(fe_mtu_get(&fe,&flows[16],2000)==-ENOSPC&&fe.mtu_writes==writes);
 assert(fe_mtu_get(&fe,&flows[16],0)==-EINVAL&&fe_mtu_get(&fe,&flows[16],16384)==-EINVAL);
 for(unsigned i=0;i<16;i++)assert(!fe_mtu_put(&fe,&flows[i]));
 printf("Native MTU: %u setup/restore write faults over16 slots, no incomplete sharing, exact retry ownership and capacity/bounds PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='mtu-ownership-') as tmp:
 d=Path(tmp);(d/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',
                 '-I'+str(drivers),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
