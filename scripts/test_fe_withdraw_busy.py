#!/usr/bin/env python3
"""Execute FE deletion after a previous age/cache command remains in flight."""
import argparse
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');args=p.parse_args()
fixture=root/'scripts/test_fe_mcast_integration.py'
scope={'__file__':str(fixture)}
exec(compile(fixture.read_text().split("code += r'''\nint main(void)",1)[0],str(fixture),'exec'),scope)
code=scope['code'];drivers=scope['drivers']
if args.revision:
    relative=str((drivers/'rtl9617c-fe-table.h').relative_to(root))
    table=subprocess.check_output(['git','show',args.revision+':'+relative],cwd=root,text=True)
    pipeline=(drivers/'rtl9617c-fe-pipeline.h').read_text().replace('#include "rtl9617c-fe-table.h"',table)
    code=code.replace('#include "rtl9617c-fe-pipeline.h"',pipeline)
code=code.replace('unsigned int age,age_fail,','unsigned int hold_command,hold_at,age_calls,cache_calls,table_writes;unsigned int age,age_fail,')
code=code.replace('((struct rtl_fe *)p)->reg[reg/4]=val;', '((struct rtl_fe *)p)->table_writes++;((struct rtl_fe *)p)->reg[reg/4]=val;')
code=code.replace('if(reg==0x3928){', '''if(reg==0x3928){
  f->age_calls++;
  if(f->hold_command==1 && f->age_calls==f->hold_at){f->reg[reg/4]=command;return -ETIMEDOUT;}
''')
code=code.replace('if(reg==0x38b0){', '''if(reg==0x38b0){
  f->cache_calls++;
  if(f->hold_command==2){f->reg[reg/4]=command;return -ETIMEDOUT;}
''')
code+=r'''
static struct rtl_fe_flow *add(struct rtl_fe *fe,struct rtl_fe_port *port,unsigned index){
 struct rtl_fe_flow *flow=calloc(1,sizeof(*flow));assert(flow);
 flow->input=flow->output=port;flow->hash_index=index;flow->crc16=index+100;
 flow->published=flow->installed=true;INIT_LIST_HEAD(&flow->retry);
 list_add_tail(&flow->list,&fe->flows);__set_bit(index,fe->hashes);
 ((u32*)fe->memory)[index]=0x12345678;
 return flow;
}
int main(void){unsigned cases=0;
 for(unsigned command=1;command<=2;command++)for(unsigned stage=1;stage<=2;stage++){
  struct rtl_fe fe={};struct rtl_fe_port port={.fe=&fe};
  fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);
  INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);
  struct rtl_fe_flow *first=add(&fe,&port,0),*second=add(&fe,&port,32);
  struct rtl_dq_io io=fe_io(&fe);fe.hold_command=command;fe.hold_at=stage;
  assert(fe_delete(&fe,first)==-ETIMEDOUT&&first->deleting&&first->published);
  unsigned age_accesses=fe.age_calls,cache_accesses=fe.cache_calls,writes=fe.table_writes;
  unsigned old_command=fe.reg[0x38ac/4],old_age=fe.reg[0x3928/4];
  for(unsigned retry=0;retry<3;retry++){
   assert(fe_delete(&fe,second)==-EBUSY&&second->deleting&&second->published);
   assert(fe_delete(&fe,first)==-EBUSY);
   assert(test_bit(0,fe.hashes)&&test_bit(32,fe.hashes)&&!fe.deletes);
   assert(((u32*)fe.memory)[0]==0x12345678&&((u32*)fe.memory)[32]==0x12345678);
   assert(fe.cache_calls==cache_accesses&&fe.reg[0x38ac/4]==old_command);
   if(command==1){assert(fe.age_calls==age_accesses&&fe.table_writes==writes&&fe.reg[0x3928/4]==old_age);
    unsigned value=0xa5a5a5a5;assert(rtl_fe_main_age_read(&io,64,&value)==-EBUSY&&value==0xa5a5a5a5);}
  }
  assert(!list_empty(&first->retry)&&!list_empty(&second->retry));
  /* Explicitly complete the modeled outstanding access before retry. */
  fe.reg[command==1?0x3928/4:0x38b0/4]=0;fe.hold_command=0;
  assert(!fe_delete(&fe,first)&&!fe_delete(&fe,second));
  assert(list_empty(&fe.flows)&&list_empty(&fe.retries)&&fe.deletes==2);
  assert(!test_bit(0,fe.hashes)&&!test_bit(32,fe.hashes));free(fe.memory);cases++;
 }
 printf("FE withdrawal: %u age-read/write/cache busy cases, repeated second-flow cleanup, untouched pending command and exact eventual release PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='fe-withdraw-busy-') as tmp:
    d=Path(tmp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',
                    '-I'+str(drivers),'-I'+str(root/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(d/'test.c'),'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
