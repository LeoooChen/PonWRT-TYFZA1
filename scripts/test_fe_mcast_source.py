#!/usr/bin/env python3
"""Check original L2 MAC writer/FC actions and native replica MAC ownership."""
import argparse
import json
from pathlib import Path
from ni_table_fixture import code as ni_table_code
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DRIVERS=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
sys.path.insert(0,str(ROOT/'tools'))
from arm64_module_oracle import ModuleOracle
from trace_stock_fttr_mcast_hw import replica_recipes
from unicorn import UC_HOOK_MEM_WRITE

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--fc',type=Path,required=True)
p.add_argument('--ni',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
original=replica_recipes(a.fc,a.ni,(6,),True,True)

def boundary(name,args):
    raise AssertionError(('unmodeled MAC table boundary',name,args))

stock=ModuleOracle(a.ni,['aal_l2fe_pe_my_mac_direct_set','aal_direct_table_set'],boundary)
base=0x6000000
stock.uc.mem_map(base,0x10000)
stock.word(stock.symbols['g_ne_reg_iobase'][0],base)
writes=[]
stock.uc.hook_add(UC_HOOK_MEM_WRITE,lambda u,access,addr,size,value,data:writes.append((addr-base,value)),begin=base,end=base+65535)
mac=bytes.fromhex('022233445566')
stock.uc.mem_write(stock.scratch,mac[::-1]+bytes(2))
for index in range(8):
    writes.clear()
    assert stock.call('aal_l2fe_pe_my_mac_direct_set',0,index,stock.scratch)==0
    assert writes==[(0x1714+8*index,0x33445566),(0x1718+8*index,0x222)]
writes.clear()
assert stock.call('aal_l2fe_pe_my_mac_direct_set',0,8,stock.scratch)&0xffffffff==0xfffffff7
assert not writes

# Reuse the established table model, adding loss of an individual MMIO write.
source=(ROOT/'scripts/test_fe_mcast_table.c').read_text()
code=source[:source.index('static void owner_lifecycle')]
code=code.replace('unsigned int calls,fail,writes;', 'unsigned int calls,fail,writes,fail_write;')
code=code.replace('m->writes++;m->reg[address/4]=value;', 'if(++m->writes!=m->fail_write)m->reg[address/4]=value;')
code+=r'''
int main(void){
 static struct model m;
 static struct rtl_fe_mc_owner owner;
 struct rtl_dq_io io={&m,read_reg,write_reg,indirect};
 struct rtl_fe_mc_replica replica={.destination=6,.source_set=1,.source={2,0x22,0x33,0x44,0x55,0x66}};
 unsigned group,second,total;
 /* Nonzero foreign slots are preserved; high reserved bits survive. */
 m.reg[0x1714/4]=0xaabbccdd;m.reg[0x1718/4]=0x2222;
 m.reg[0x1720/4]=0xa5000000;
 assert(!rtl_fe_mc_stage(&io,&owner,&replica,1,&group));
 assert(owner.groups[group].mac_held==2 && m.actions[0][2]==7);
 assert(m.reg[0x171c/4]==0x33445566 && m.reg[0x1720/4]==0xa5000222);
 assert(!rtl_fe_mc_reference(&io,&owner,group));
 assert(!rtl_fe_mc_stage(&io,&owner,&replica,1,&second));
 assert(owner.macs[1].users==2);
 assert(!rtl_fe_mc_withdraw(&io,&owner,second));
 assert(!rtl_fe_mc_withdraw(&io,&owner,group));
 assert(owner.macs[1].exposed && !owner.macs[1].users && m.reg[0x171c/4]==0x33445566);
 assert(m.reg[0x1714/4]==0xaabbccdd);
 /* Every direct/indirect staging failure remains owned until rollback. */
 memset(&m,0,sizeof(m));memset(&owner,0,sizeof(owner));owner.initialized=1;
 assert(!rtl_fe_mc_stage(&io,&owner,&replica,1,&group));total=m.writes;
 for(unsigned fail=1;fail<=total;fail++) {
  memset(&m,0,sizeof(m));memset(&owner,0,sizeof(owner));owner.initialized=1;m.fail_write=fail;
  int ret=rtl_fe_mc_stage(&io,&owner,&replica,1,&group);
  assert(!ret || ret==-EIO);
  m.fail_write=0;
  if(group)assert(!rtl_fe_mc_withdraw(&io,&owner,group));
  for(unsigned i=0;i<8;i++)assert(!owner.macs[i].owned&&!owner.macs[i].users);
  assert(!m.reg[0x1714/4]&&!m.reg[0x1718/4]);
 }
 /* A lost MAC restoration must retain its dependency for a later retry. */
 memset(&m,0,sizeof(m));memset(&owner,0,sizeof(owner));owner.initialized=1;
 assert(!rtl_fe_mc_stage(&io,&owner,&replica,1,&group));
 m.fail_write=m.writes+6; /* group2/action3 then MAC low */
 assert(rtl_fe_mc_withdraw(&io,&owner,group)==-EIO);
 assert(owner.macs[0].users==1 && owner.groups[group].mac_held==1 && !owner.actions[0].users);
 m.fail_write=0;assert(!rtl_fe_mc_withdraw(&io,&owner,group));
 /* Eight exposed MAC values are immutable; changing the shared MAC cannot
  * repurpose an address still referenced by an old hardware action. */
 memset(&m,0,sizeof(m));memset(&owner,0,sizeof(owner));
 for(unsigned i=0;i<8;i++) {
  replica.source[5]=i+1;
  assert(!rtl_fe_mc_stage(&io,&owner,&replica,1,&group));
  assert(!rtl_fe_mc_reference(&io,&owner,group));
  assert(!rtl_fe_mc_withdraw(&io,&owner,group));
 }
 replica.source[5]=9;
 assert(rtl_fe_mc_stage(&io,&owner,&replica,1,&group)==-ENOSPC);
 assert(!rtl_fe_mc_withdraw(&io,&owner,group));
 replica.source[5]=1;
 assert(!rtl_fe_mc_stage(&io,&owner,&replica,1,&group));
 m.reg[0x1714/4]^=1;
 assert(rtl_fe_mc_reference(&io,&owner,group)==-EUCLEAN);
 assert(!rtl_fe_mc_withdraw(&io,&owner,group));
 printf("Replica L2 MAC: %u write failures, shared/exposed refs, foreign slots, rollback retry and eight-address exhaustion passed\n",total);
}
'''
# Compare the native encoded P6 action to each fully executed original
# recipe; only bridge TTL-decrement is deliberately cleared.
checks=[]
for case in original['cases']:
    words=case['words'].copy();words[2]&=~(1<<10)
    checks.append(f"""{{struct rtl_fe_mc_replica r={{.destination=6,.tags={case['output_tags']},.source_set=1,.source_index=3,
.source={{2,0x22,0x33,0x44,0x55,0x66}},.tag={{{{0x8100,4095|5<<13|1<<12}},{{0x8100,100|3<<13}}}}}};
unsigned w[3];assert(!rtl_fe_mc_replica_encode(&io,&r,w));
assert(w[0]=={words[0]}U && w[1]=={words[1]}U && w[2]=={words[2]}U);}}""")
code=code.replace(' /* Nonzero foreign', ' for(unsigned i=0;i<4;i++)m.reg[(0x1704+i*4)/4]=0x8100;\n'+''.join(checks)+'\n /* Nonzero foreign')
with tempfile.TemporaryDirectory(prefix='mcast-source-') as tmp:
    tmp=Path(tmp)
    (tmp/'test.c').write_text(ni_table_code+code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(DRIVERS),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
original['native_difference']='Per-P6 source rewrite retains VLAN/COS and bridge TTL; native clears original helper ip_ttl_dec_cmd bit10'
original['mac_table']='Executed eight original direct writers: low1714+i*8/high1718+i*8; reversed MAC bytes; index8 rejected'
a.output.write_text(json.dumps(original,indent=2)+'\n')
print('Original FC/NI source-MAC chain and eight L2 MAC direct writers passed')
