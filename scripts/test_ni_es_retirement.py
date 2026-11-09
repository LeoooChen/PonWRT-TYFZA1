#!/usr/bin/env python3
"""Exercise real NI release when EPP is empty but its original ES VoQ is occupied."""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--stock',type=Path)
p.add_argument('--revision')
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
original=0
if a.stock:
    from arm64_module_oracle import ModuleOracle
    from unicorn import UC_HOOK_MEM_READ
    reads=[]
    def boundary(name,values):raise AssertionError((name,values))
    o=ModuleOracle(a.stock,['aal_l3_tm_es_voq_status_get'],boundary)
    base=0x6000000;o.uc.mem_map(base,65536);o.word(o.symbols['g_ne_reg_iobase'][0],base)
    o.uc.hook_add(UC_HOOK_MEM_READ,lambda u,k,addr,n,v,d:reads.append(addr-base),begin=base,end=base+65535)
    for cpu in (2,3):
        for mask in range(256):
            word=(0x5aa55aa5 & ~(255<<(cpu*8))) | mask<<(cpu*8)
            o.uc.mem_write(base+0x6830,word.to_bytes(4,'little'))
            for cos in range(8):
                reads.clear();assert o.call('aal_l3_tm_es_voq_status_get',0,cpu,cos,o.scratch)==0
                assert reads==[0x6830] and o.unpack(o.scratch,'I')[0]==int(not(mask&(1<<cos)))
                original+=1

# Reuse the existing release fixture's indirect IO/error ownership model.
fixture=root/'scripts/test_ni_restore.py';text=fixture.read_text();ns={'__file__':str(fixture)}
saved=sys.argv;sys.argv=[str(fixture)]
try:exec(compile(text.split("code+=r'''\nstatic struct rtl_ax_tx setup",1)[0],str(fixture),'exec'),ns)
finally:sys.argv=saved
code=ns['code'].replace('typedef uint32_t u32;','typedef uint32_t u32;typedef uint8_t u8;');function=ns['fn']
old_source=None
if a.revision:
    code=code.replace('static bool ni_epps_empty(struct rtl_ax_tx *t){(void)t;return true;}', 'static bool ni_old_epps_empty(struct rtl_ax_tx *t){(void)t;return true;}\nstatic bool ni_epps_empty(struct rtl_ax_tx *t);')
    old_source=subprocess.check_output(['git','show',a.revision+':'+str((ns['driver']/'ni-test.h').relative_to(root))],cwd=root,text=True)
    # Keep the new state-observation helper available to the same fixture.
    for name in ('ni_test_release','ni_test_remove'):
        now=function(name);current=ns['s'];ns['s']=old_source
        try:old=function(name)
        finally:ns['s']=current
        code=code.replace(now,"#define ni_epps_empty ni_old_epps_empty\n"+old+"#undef ni_epps_empty\n")
code=code.replace('static bool ni_epps_empty(struct rtl_ax_tx *t){(void)t;return true;}',
                  'static bool ni_epps_empty(struct rtl_ax_tx *t);')
code=code.replace('static u32 readl(const void *p){return get_unaligned_le32(p);}',r'''
static unsigned test_cpu,arrival,enable_fault;
static u32 readl(const void *p){return get_unaligned_le32(p);}
''')
code=code.replace(' mmio_writes++;',r'''
 mmio_writes++;
 if(p==mmio+0x6108/4){
  if(enable_fault==1 && !(v&BIT(test_cpu)))return;
  if(enable_fault==2 && (v&BIT(test_cpu)))return;
  if(arrival && !(v&BIT(test_cpu))){
   if(arrival==1)mmio[0x6830/4]|=BIT(test_cpu*8+4);
   else mmio[(0x7000+test_cpu*32)/4+7]=8;
   arrival=0;
  }
 }
''')
code+=function('ni_epps_empty')
tree=ast.parse(text)
setup=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.AugAssign)
           and isinstance(n.value,ast.Constant) and 'static struct rtl_ax_tx setup' in n.value.value)
code+=setup.split('int main(void)',1)[0]
code+=r'''
int main(void){unsigned cases=0;
 for(test_cpu=2;test_cpu<=3;test_cpu++)for(unsigned mask=0;mask<256;mask++){
  struct rtl_ax_tx t=setup(test_cpu);
  mmio[0x6830/4]=(0x5aa55aa5 & ~(255U<<(test_cpu*8))) | (mask<<(test_cpu*8));
  assert(ni_es_empty(&t)==!mask && ni_epps_empty(&t)==!mask);cases++;
 }
 for(test_cpu=2;test_cpu<=3;test_cpu++)for(unsigned cos=0;cos<8;cos++){
  struct rtl_ax_tx t=setup(test_cpu);fail_at=0;
  u32 occupied=BIT(test_cpu*8+cos);mmio[0x6830/4]=occupied;
  assert(ni_test_release(&t)==-EBUSY);
  assert(t.ni.active && t.ni.regs==mmio && !released && !accesses && !paused);
  assert(mmio[0x6108/4]==15 && mmio[0x6830/4]==occupied);
  mmio[0x6830/4]=0;
  assert(!ni_test_release(&t) && !t.ni.active && released==2);cases++;
 }
 for(test_cpu=2;test_cpu<=3;test_cpu++)for(unsigned at=1;at<=2;at++){
  struct rtl_ax_tx t=setup(test_cpu);fail_at=0;arrival=at;
  assert(ni_epps_empty(&t));
  assert(ni_test_release(&t)==-EBUSY && t.ni.active && !released && !accesses);
  assert(mmio[0x6108/4]==15);
  mmio[0x6830/4]=0;
  for(unsigned i=0;i<8;i++)mmio[t.ni.registers[11]/4+i]=mmio[(0x7000+test_cpu*32)/4+i];
  assert(!ni_test_release(&t)&&!t.ni.active&&released==2);cases++;
 }
 /* A pre-disabled port is never opened to flush a packet implicitly. */
 for(test_cpu=2;test_cpu<=3;test_cpu++){
  struct rtl_ax_tx t=setup(test_cpu);mmio[0x6108/4]&=~BIT(test_cpu);mmio[0x6830/4]=BIT(test_cpu*8);
  assert(ni_test_release(&t)==-EBUSY && !(mmio[0x6108/4]&BIT(test_cpu)) && !released);
  cases++;
 }
 for(test_cpu=2;test_cpu<=3;test_cpu++)for(unsigned fault=1;fault<=2;fault++){
  struct rtl_ax_tx t=setup(test_cpu);enable_fault=fault;mmio[0x6830/4]=BIT(test_cpu*8);
  assert(ni_test_release(&t)==-EIO && t.ni.active && !released && !accesses);
  enable_fault=0;cases++;
 }
 /* Forced PCI teardown must not restore maps with BIDs still in ES. */
 for(test_cpu=2;test_cpu<=3;test_cpu++)for(unsigned cos=0;cos<8;cos++){
  struct rtl_ax_tx t=setup(test_cpu);withdraw_error=0;fail_at=0;mmio[0x6830/4]=BIT(test_cpu*8+cos);
  assert(ni_test_remove(&t)==-EBUSY && t.ni.active && !released && !accesses && !paused);
  assert(!(mmio[0x6108/4]&BIT(test_cpu)));
  assert(ni_test_remove(&t)==-EBUSY && !released && !accesses);
  mmio[0x6830/4]=0;
  assert(!ni_test_remove(&t)&&!t.ni.active&&released==2);cases++;
 }
 printf("NI ES retirement: %u occupancy/late arrival/disabled owner/write fault/forced cleanup cases PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='ni-es-retire-') as temp:
    d=Path(temp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-I'+str(ns['driver']),'-I'+str(ns['driver'].parents[4]/'include'),
                    str(d/'test.c'),'-o',str(d/'test')],check=True)
    result=subprocess.run([str(d/'test')],text=True,capture_output=True)
    if a.revision:
        assert result.returncode and 'ni_test_release(&t)==-EBUSY' in result.stderr,result
        print('REPRODUCED: original native release accepted nonempty ES while EPP was empty')
    else:
        assert result.returncode==0,result.stderr
        print(result.stdout,end='')
a.output.write_text(json.dumps(dict(original_status_cases=original,old_revision=a.revision,
    native_passed=not result.returncode,scope='Actual target status getter and native release; occupancy, MMIO and arrivals modeled. ES/EPP observation is not a complete upstream retirement barrier.'),indent=2)+'\n')
