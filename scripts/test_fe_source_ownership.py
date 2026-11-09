#!/usr/bin/env python3
"""Exercise source CAM acquisition and actual flow rollback after applied timeout."""
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
 def boundary(name,values):raise AssertionError((name,values))
 o=ModuleOracle(args.stock,['cam_hw_entry_set'],boundary)
 base=0x6000000;o.uc.mem_map(base,65536);o.word(o.symbols['g_ne_reg_iobase'][0],base)
 table=o.symbols['l3_cam_mac_da_tbl'][0];storage=o.scratch+0x2000;payload=o.scratch+0x4000
 o.word(table+136,storage);events=[];stuck=False
 def write(u,k,addr,size,value,data):events.append((addr-base,value))
 def read(u,k,addr,size,value,data):
  if not stuck:u.mem_write(addr,struct.pack('<I',o.unpack(addr,'I')[0]&~(1<<31)))
 o.uc.hook_add(UC_HOOK_MEM_WRITE,write,begin=base,end=base+65535)
 o.uc.hook_add(UC_HOOK_MEM_READ,read,begin=base+0x3200,end=base+0x3203)
 for index in range(15):
  words=[0x12340000+index*5+i for i in range(5)]
  o.word(storage+index*24,payload);o.uc.mem_write(payload,struct.pack('<5I',*words))
  for stuck in (False,True):
   events.clear();ret=o.call('cam_hw_entry_set',table,index)&0xffffffff
   assert ret==(0xfffffff5 if stuck else 0)
   assert events==[(0x3214-i*4,v) for i,v in enumerate(words)]+[(0x3200,0xc0030000|index)]
 print('Original CAM:15 slots x success/timeout; five data words and bank3 access ordering PASS')
fixture=root/'scripts/test_fe_mcast_integration.py';ns={'__file__':str(fixture)}
exec(compile(fixture.read_text().split("code += r'''\nint main(void)",1)[0],str(fixture),'exec'),ns)
code=ns['code'];drivers=ns['drivers'];native=(drivers/'rtl9617c-fe.c').read_text()
if args.revision:native=subprocess.check_output(['git','show',args.revision+':'+str((drivers/'rtl9617c-fe.c').relative_to(root))],cwd=root,text=True)
def function(name):
 a=re.search(r'^static int '+name+r'\([^;]*?\)\s*\{',native,re.M).start();b=native.index('{',a)+1;depth=1
 while depth:depth+=(native[b]=='{')-(native[b]=='}');b+=1
 return native[a:b]+'\n'
code=code.replace('struct rtl_fe {', 'struct rtl_fe_mac {unsigned int users,saved[5];u8 address[6];bool ready;};\n#define FE_SOURCE_MACS 15\nstruct rtl_fe {struct rtl_fe_mac source_macs[15];unsigned cam[15][5],cam_calls,cam_failure,cam_writes;bool cam_applied;')
code=code.replace('if(reg==0x3928){', '''if(reg==0x3200){
  unsigned index=command&63;assert(index<15);f->cam_calls++;
  if((command&BIT(30))&&(f->cam_applied||f->cam_calls!=f->cam_failure))
   for(unsigned i=0;i<5;i++)f->cam[index][i]=read_reg(f,0x3214-i*4);
  if(f->cam_calls==f->cam_failure){f->reg[reg/4]=command;return -ETIMEDOUT;}
  if(!(command&BIT(30)))for(unsigned i=0;i<5;i++)write_reg(f,0x3214-i*4,f->cam[index][i]);
  return 0;
 }
 if(reg==0x3928){''')
code=code.replace('((struct rtl_fe *)p)->reg[reg/4]=val;', 'if(reg>=0x3204&&reg<=0x3214)((struct rtl_fe *)p)->cam_writes++;((struct rtl_fe *)p)->reg[reg/4]=val;')
code=code.replace('static int fe_source_get(struct rtl_fe *f,const u8 *a,struct rtl_fe_flow *v){(void)f;(void)a;(void)v;assert(0);return -1;}', 'static int acquire_source(struct rtl_fe *,const u8 *,struct rtl_fe_flow *);\n#define fe_source_get acquire_source')
code=re.sub(r'static int fe_source_put\([^\n]*', 'static int fe_source_put(struct rtl_fe *,unsigned);', code)
code+='\n#undef fe_source_get\n#define ether_addr_equal(a,b) (!memcmp(a,b,6))\n'+function('fe_source_get')+function('fe_source_put')
code+='static int acquire_source(struct rtl_fe *fe,const u8 *mac,struct rtl_fe_flow *flow){\n'
if args.revision:
 code+='int ret=fe_source_get(fe,mac,&flow->source_mac);if(!ret)flow->source_owned=true;return ret;}\n'
else:code+='return fe_source_get(fe,mac,flow);}\n'
code+=r'''
static struct rtl_fe_flow *add(struct rtl_fe *fe,struct rtl_fe_port *port,unsigned index){
 struct rtl_fe_flow *f=calloc(1,sizeof(*f));assert(f);f->input=f->output=port;f->hash_index=index;
 INIT_LIST_HEAD(&f->retry);list_add_tail(&f->list,&fe->flows);__set_bit(index,fe->hashes);return f;
}
int main(void){unsigned cases=0;const u8 mac[6]={2,3,4,5,6,7};
 for(unsigned applied=0;applied<2;applied++)for(unsigned pass=0;pass<2;pass++){
  unsigned phase=pass?1:2;
  struct rtl_fe fe={};struct rtl_fe_port port={.fe=&fe};struct rtl_dq_io io=fe_io(&fe);
  fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);
  unsigned original[5]={0x12345678,0x00a51234,0x56781234,0xdeadbeef,0x89abcdef};memcpy(fe.cam[0],original,20);
  struct rtl_fe_flow *first=add(&fe,&port,0),*second=add(&fe,&port,1);
  fe.cam_failure=phase;fe.cam_applied=applied;
  assert(acquire_source(&fe,mac,first)==-ETIMEDOUT);
  if(phase==1){assert(!first->source_owned&&!fe.source_macs[0].users);assert(!fe_delete(&fe,first));}
  else {
   assert(first->source_owned&&fe.source_macs[0].users==1&&!memcmp(fe.source_macs[0].saved,original,20));
   assert(fe_delete(&fe,first)==-EBUSY&&first->deleting&&test_bit(0,fe.hashes));
  }
  unsigned calls=fe.cam_calls,writes=fe.cam_writes;
  assert(acquire_source(&fe,mac,second)==-EBUSY&&!second->source_owned);
  struct rtl_fe_cam_protocol protocol={};assert(rtl_fe_cam_protocol_get(&io,&protocol,2,0x8864)==-EBUSY);
  protocol.created=1;assert(rtl_fe_cam_protocol_put(&io,&protocol)==-EBUSY&&protocol.created);
  assert(fe.cam_calls==calls&&fe.cam_writes==writes);
  fe.reg[0x3200/4]=0;fe.cam_failure=0;
  if(phase==2)assert(!fe_delete(&fe,first));
  assert(!memcmp(fe.cam[0],original,20)&&!fe.source_macs[0].users);
  assert(!acquire_source(&fe,mac,second)&&second->source_owned&&fe.source_macs[0].users==1);
  struct rtl_fe_flow *third=add(&fe,&port,2);
  calls=fe.cam_calls;assert(!acquire_source(&fe,mac,third)&&fe.cam_calls==calls&&fe.source_macs[0].users==2);
  assert(!fe_delete(&fe,second)&&fe.source_macs[0].users==1);
  /* Last restore is applied, but completion remains unknown. */
  fe.cam_failure=fe.cam_calls+1;fe.cam_applied=true;
  assert(fe_delete(&fe,third)==-ETIMEDOUT&&third->source_owned&&test_bit(2,fe.hashes));
  fe.reg[0x3200/4]=0;fe.cam_failure=0;
  struct rtl_fe_flow *fourth=add(&fe,&port,3);
  assert(acquire_source(&fe,mac,fourth)==-EBUSY&&!fourth->source_owned);
  assert(!fe_delete(&fe,third)&&!memcmp(fe.cam[0],original,20));
  assert(!acquire_source(&fe,mac,fourth));assert(!fe_delete(&fe,fourth));
  assert(!memcmp(fe.cam[0],original,20)&&!fe.source_macs[0].users&&list_empty(&fe.flows)&&list_empty(&fe.retries));
  free(fe.memory);cases++;
 }
 printf("Source CAM ownership: %u read/write timeout/applied combinations; protocol busy isolation, no partial sharing, last restore retry and original snapshot recovery PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='source-cam-owner-') as tmp:
 d=Path(tmp);(d/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',
                 '-I'+str(drivers),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
