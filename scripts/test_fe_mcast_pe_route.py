#!/usr/bin/env python3
"""Compare target FC/NI wireless actions and verify native PE pool admission."""
import argparse
import json
from pathlib import Path
from ni_table_fixture import code as ni_table_code
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
drivers=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
sys.path.insert(0,str(root/'tools'))
from trace_stock_fttr_mcast_hw import replica_recipes
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fc',type=Path,required=True)
parser.add_argument('--ni',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
recipes=[]
for destination in (18,19):
    recipes+=replica_recipes(args.fc,args.ni,(1,),wifi=(9,destination,0))['cases']
code=(root/'scripts/test_fe_mcast_table.c').read_text().split('static void owner_lifecycle',1)[0]
code+=r'''
static void prepare(struct model *m){
 memset(m,0,sizeof(*m));m->reg[0xaa60/4]=0x2f0ff;m->reg[0xa1c0/4]=0x24009b;
 m->reg[0x6104/4]=1U<<31;m->reg[0x6108/4]=(1U<<2)|(1U<<3);
 for(unsigned i=0;i<4;i++)m->reg[(0x1704+i*4)/4]=0x8100;
 for(unsigned cpu=2;cpu<4;cpu++){
  unsigned profile=cpu+4,eq=9-cpu;
  m->reg[(0x6148+cpu*4)/4]=profile;m->reg[(0x6128+profile*4)/4]=eq|15U<<4;
  m->reg[(0x61e8+eq*20)/4]=1;m->reg[(0x61ec+eq*20)/4]=2496U<<16|(1536+(3-cpu)*2496);
  m->reg[(0x61f0+eq*20)/4]=13;m->reg[(0x61c8+cpu*4)/4]=RTL_QM_PACKET_LAYOUT;
  for(unsigned i=0;i<4;i++)m->map[16+cpu+64*i]=9;
  for(unsigned i=0;i<8;i++)m->reg[(0x65a4+cpu*32+i*4)/4]=profile;
 }
}
int main(void){
 static struct model m,baseline;struct rtl_fe_mc_route route={};struct rtl_dq_io io={&m,read_reg,write_reg,indirect};
 prepare(&m);baseline=m;
 assert(!rtl_fe_mc_route_prepare(&io,&route));
 assert(!rtl_fe_mc_route_port(&io,&route,18));assert(!rtl_fe_mc_route_port(&io,&route,19));
 assert(!rtl_fe_mc_route_restore(&io,&route));
 assert(!memcmp(m.elp,baseline.elp,sizeof(m.elp)));
 unsigned tests=0;
 for(unsigned cpu=2;cpu<4;cpu++)for(unsigned fault=0;fault<20;fault++){
  prepare(&m);route=(struct rtl_fe_mc_route){};unsigned eq=9-cpu,profile=cpu+4;
  assert(!rtl_fe_mc_route_prepare(&io,&route));
  if(fault==0)m.reg[0x6104/4]=0;
  if(fault==1)m.reg[0x6108/4]&=~(1U<<cpu);
  if(fault==2)m.reg[0x6108/4]|=1U<<28;
  if(fault==3)m.reg[(0x6148+cpu*4)/4]^=1;
  if(fault==4)m.reg[(0x6128+profile*4)/4]^=1;
  if(fault==5)m.reg[(0x61ec+eq*20)/4]^=1;
  if(fault==6)m.reg[(0x61f0+eq*20)/4]^=1;
  if(fault==7)m.reg[(0x61c8+cpu*4)/4]^=1;
  if(fault==8)m.reg[(0x6558+cpu*4)/4]=1;
  if(fault==9)m.reg[0xa1dc/4]|=1U<<(cpu*2);
  if(fault==10)m.reg[0xa1ec/4]|=1U<<(cpu*2);
  if(fault==11)m.map[16+cpu+3*64]=8;
  if(fault>=12)m.reg[(0x65a4+cpu*32+(fault-12)*4)/4]^=1;
  assert(rtl_fe_mc_route_port(&io,&route,16+cpu)==-EUCLEAN);
  assert(!(route.ports&(1ULL<<(16+cpu))));tests++;
 }
 prepare(&m);
 /* ORIGINAL_ACTION_CHECKS */
 printf("Native PE multicast routing: %u pool/map/header fault cases and6 original actions passed\n",tests);
}
'''
checks=[]
for recipe in recipes:
    port=recipe['wifi'][1];tags=recipe['output_tags'];words=recipe['words'][:]
    words[0]=(words[0]&0x7fffff)|(265<<23)
    words[2]|=1<<11
    checks.append(f'''{{struct rtl_fe_mc_replica r={{.destination={port},.direct_cookie=9,.tags={tags},.tag={{{{0x8100,4095|5<<13|1<<12}},{{0x8100,100|3<<13}}}}}};
unsigned words[3];assert(!rtl_fe_mc_replica_encode(&io,&r,words));assert(words[0]=={words[0]}U&&words[1]=={words[1]}U&&words[2]=={words[2]}U);}}''')
code=code.replace('/* ORIGINAL_ACTION_CHECKS */',''.join(checks))
with tempfile.TemporaryDirectory(prefix='mcast-pe-route-') as t:
    p=Path(t);(p/'test.c').write_text(ni_table_code+code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(drivers),str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
args.output.write_text(json.dumps(dict(recipes=recipes,scope='Actual target FC/NI action execution; native replaces VIF POL with owned cookie and preserves original source. RMU/EQ allocation and payload exclusivity remain hardware-contract assumptions, not measured physical behavior.'),indent=2)+'\n')
