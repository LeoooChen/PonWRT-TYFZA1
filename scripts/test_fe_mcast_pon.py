#!/usr/bin/env python3
"""Compare original per-member PON recipes and native NI/QM routing ownership."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DRIVERS=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
sys.path.insert(0,str(ROOT/'tools'))
from trace_stock_fttr_mcast_hw import replica_recipes
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE,UC_MEM_READ
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--fc',type=Path,required=True)
p.add_argument('--ni',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
recipes=[]
for gem,queue,tcont in ((8,0,1),(55,3,5),(255,7,31)):
    recipes+=replica_recipes(a.fc,a.ni,(7,),True,False,(gem,queue,tcont))['cases']

# Execute the original DBUF setter, including exact field masking.
base=0x6000000
pending={}
table=[0]*8

def boundary(name,args):
    if name in ('ca_spin_lock','ca_spin_unlock'):return 0
    raise AssertionError(('unmodeled PON route boundary',name,args))
ni=ModuleOracle(a.ni,['aal_arb_port_dbuf_set'],boundary)
ni.uc.mem_map(base,0x10000)
ni.word(ni.symbols['g_ne_reg_iobase'][0],base)

def mmio(uc,access,address,size,value,unused):
    reg=address-base
    assert size==4
    if access!=UC_MEM_READ:
        if reg==0x164c:pending[reg]=value
        return
    if reg!=0x164c or reg not in pending:return
    command=pending.pop(reg)
    index=command&7
    if command&(1<<30):table[index]=ni.unpack(base+0x1650,'I')[0]
    else:uc.mem_write(base+0x1650,struct.pack('<I',table[index]))
    uc.mem_write(address,struct.pack('<I',command&~(1<<31)))
ni.uc.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,mmio,begin=base,end=base+65535)
for destination in range(33,64):
    table[0]=0xa5800000
    ni.uc.mem_write(ni.scratch,struct.pack('<5I',1,1,destination,0,0))
    assert ni.call('aal_arb_port_dbuf_set',0,0,31,ni.scratch)==0
    assert table[0]==0xa5800003|destination<<2

source=(ROOT/'scripts/test_fe_mcast_table.c').read_text()
code=source[:source.index('static void owner_lifecycle')]
code+=r'''
static void prepare(struct model *m) {
 memset(m,0,sizeof(*m));m->reg[0xaa60/4]=0x2f0ff;m->reg[0xa1c0/4]=0x24009b;m->reg[0x1600/4]=0x80;
 m->reg[0x68f8/4]=10;
 for(unsigned port=33;port<64;port++)for(unsigned flags=0;flags<256;flags+=64)m->map[port+flags]=8;
 m->map[70]=m->map[198]=8;
 for(unsigned i=0;i<4;i++)m->reg[(0x1704+i*4)/4]=0x8100;
}
int main(void) {
 static struct model m,saved;struct rtl_fe_mc_route route={0};struct rtl_dq_io io={&m,read_reg,write_reg,indirect};
 unsigned cases=0;
 prepare(&m);saved=m;
 assert(!rtl_fe_mc_route_prepare(&io,&route));
 assert(!rtl_fe_mc_route_port(&io,&route,6));
 assert(!rtl_fe_mc_route_port(&io,&route,37));
 assert(!rtl_fe_mc_route_port(&io,&route,63));
 assert(m.dbuf[0]==0x1b&&m.dbuf[1]==(3|37<<2)&&m.dbuf[2]==(3|63<<2));
 assert(route.ports&(1ULL<<63));
 assert(!rtl_fe_mc_route_port(&io,&route,37)&&route.pon_dbuf==6);
 assert(!rtl_fe_mc_route_restore(&io,&route));
 assert(!memcmp(m.dbuf,saved.dbuf,sizeof(m.dbuf))&&!memcmp(m.elp,saved.elp,sizeof(m.elp)));
 unsigned calls=m.calls;
 for(unsigned fail=1;fail<=calls;fail++)for(unsigned applied=0;applied<2;applied++) {
  m=saved;route=(struct rtl_fe_mc_route){0};m.fail=fail;m.applied=applied;
  int ret=rtl_fe_mc_route_prepare(&io,&route);
  if(!ret)ret=rtl_fe_mc_route_port(&io,&route,6);
  if(!ret)ret=rtl_fe_mc_route_port(&io,&route,37);
  if(!ret)ret=rtl_fe_mc_route_port(&io,&route,63);
  if(!ret)ret=rtl_fe_mc_route_restore(&io,&route);
  assert(!ret||ret==-ETIMEDOUT);m.fail=0;
  m.reg[0x1654/4]=m.reg[0x164c/4]=m.reg[0x1420/4]=0;
  assert(!rtl_fe_mc_route_restore(&io,&route));
  assert(!memcmp(m.dbuf,saved.dbuf,sizeof(m.dbuf))&&!memcmp(m.elp,saved.elp,sizeof(m.elp)));cases++;
 }
 /* Full exact-match DBUF capacity is shared with P6; retry keeps ownership. */
 m=saved;route=(struct rtl_fe_mc_route){0};assert(!rtl_fe_mc_route_prepare(&io,&route));
 assert(!rtl_fe_mc_route_port(&io,&route,6));
 for(unsigned port=33;port<40;port++)assert(!rtl_fe_mc_route_port(&io,&route,port));
 assert(rtl_fe_mc_route_port(&io,&route,40)==-ENOSPC);
 assert(!rtl_fe_mc_route_port(&io,&route,33));
 assert(!rtl_fe_mc_route_restore(&io,&route)&&!route.pon_dbuf&&!route.dbuf_owned);
 /* All failure paths leave output words untouched; no reserved GEM/TCONT. */
 for(unsigned bad=0;bad<6;bad++) {
  struct rtl_fe_mc_replica r={.destination=37,.gem=8,.queue=3};unsigned words[3]={0xa5,0xa5,0xa5};
  if(bad==0)r.gem=7;
  if(bad==1)r.gem=256;
  if(bad==2)r.queue=8;
  if(bad==3)r.destination=32;
  if(bad==4)r.source_set=1;
  if(bad==5)r.host_group=1;
  assert(rtl_fe_mc_replica_encode(&io,&r,words)<0&&words[0]==0xa5&&words[1]==0xa5&&words[2]==0xa5);
 }
 /* Foreign DBUF rules consume capacity; no overwrite, even for PON. */
 m=saved;route=(struct rtl_fe_mc_route){0};
 for(unsigned i=0;i<8;i++)m.dbuf[i]=0x2301;
 assert(!rtl_fe_mc_route_prepare(&io,&route));
 assert(rtl_fe_mc_route_port(&io,&route,37)==-ENOSPC);
 assert(!rtl_fe_mc_route_restore(&io,&route));
 for(unsigned i=0;i<8;i++)assert(m.dbuf[i]==0x2301);
 for(unsigned bad=0;bad<5;bad++) {
  m=saved;route=(struct rtl_fe_mc_route){0};
  if(bad<4)m.map[37+bad*64]=9;else m.reg[0x68f8/4]=9;
  assert(!rtl_fe_mc_route_prepare(&io,&route));
  assert(rtl_fe_mc_route_port(&io,&route,37)==-EUCLEAN&&!(route.ports&(1ULL<<37)));
  assert(!rtl_fe_mc_route_restore(&io,&route));
 }
 /* Both halves of the existing port-isolation bitmap must authorize it. */
 struct rtl_fe_mc_replica r={.destination=63,.gem=55,.queue=3};
 m=saved;m.reg[0x160c/4]=1;
 assert(rtl_fe_mc_route_members(&io,6,&r,1)==-EOPNOTSUPP);
 m.membership[6][0]=1U<<27;m.membership[6][1]=1U<<31;
 assert(rtl_fe_mc_route_members(&io,6,&r,1)==-EOPNOTSUPP);
 m.membership[27][1]=1U<<31;assert(!rtl_fe_mc_route_members(&io,6,&r,1));
 printf("PON multicast routes: %u failure/retry cases, P6 plus T-CONT5/31, four maps, upper-PON mapping, capacity and 64-bit isolation passed\n",cases);
}
'''
# Compare each native complete three-word action with executed original output.
checks=[]
for case in recipes:
    gem,queue,tcont=case['pon']
    w=case['words']
    checks.append(f'''{{struct rtl_fe_mc_replica r={{.destination={32+tcont},.gem={gem},.queue={queue},.tags={case['output_tags']},
.tag={{{{0x8100,4095|5<<13|1<<12}},{{0x8100,100|3<<13}}}}}};
unsigned w[3];assert(!rtl_fe_mc_replica_encode(&io,&r,w));assert(w[0]=={w[0]}U&&w[1]=={w[1]}U&&w[2]=={w[2]}U);}}''')
code=code.replace('prepare(&m);saved=m;', 'prepare(&m);saved=m;'+''.join(checks),1)
with tempfile.TemporaryDirectory(prefix='mcast-pon-') as tmp:
    tmp=Path(tmp);(tmp/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(DRIVERS),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
a.output.write_text(json.dumps(dict(recipes=recipes,dbuf_ports=31,
    scope='Executed actual member7/action-bit12 PON FC/NI production with six-byte route rows; original DBUF setter and native ownership fault injection; no physical transmission. Supersedes the earlier fixture which mislabeled the wireless helper5+160 callback as PON.'),indent=2)+'\n')
print('Nine full FC/NI PON recipes match native words; original31 destination-DBUF setters match native routing')
