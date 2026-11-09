#!/usr/bin/env python3
"""Execute mask/classifier/P6-CF busy transactions and owned release retries."""
import argparse
from pathlib import Path
import subprocess
import tempfile
root=Path(__file__).resolve().parents[1]
drivers=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');a=p.parse_args()
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fe-pipeline.h"
#include "rtl9617c-fttr-cf.h"
#include "rtl9617c-fe-mcast-profile.h"
struct model {unsigned reg[0x4000/4],mask[128][4],fib[1040][7],key[1024][3],action[512][3];unsigned calls,writes,fail;};
static unsigned rd(void *p,unsigned reg){return ((struct model*)p)->reg[reg/4];}
static void wr(void *p,unsigned reg,unsigned value){struct model *m=p;m->writes++;m->reg[reg/4]=value;}
static int access(void *p,unsigned reg,unsigned value){
 struct model *m=p;unsigned *row,count,data,index=value&2047;
 m->calls++;
 if(reg==0x3910){row=m->mask[index];count=4;data=0x3920;}
 else if(reg==0x33b0){row=m->fib[index];count=7;data=0x33cc;}
 else if(reg==0x1e04){row=m->key[index];count=3;data=0x1e10;}
 else {assert(reg==0x1e14);row=m->action[index];count=3;data=0x1e20;}
 for(unsigned i=0;i<count;i++)if(value&(1U<<30))row[i]=rd(m,data-4*i);else m->reg[(data-4*i)/4]=row[i];
 if(m->calls==m->fail){m->reg[reg/4]=value;return -ETIMEDOUT;}
 m->reg[reg/4]=value&~(1U<<31);return 0;
}
static int transfer(const struct rtl_dq_io *io,unsigned kind,bool write,unsigned index,unsigned words[8]){
 if(kind==0)return write?rtl_fe_mask_write(io,index,words):rtl_fe_mask_read(io,index,words);
 if(kind==1)return write?rtl_fe_classifier_write(io,index,words):rtl_fe_classifier_read(io,index,words);
 return write?rtl_fttr_cf_write(io,kind==3,index,words):rtl_fttr_cf_read(io,kind==3,index,words);
}
int main(void){unsigned cases=0;unsigned regs[]={0x3910,0x33b0,0x1e04,0x1e14};
 for(unsigned kind=0;kind<4;kind++)for(unsigned writing=0;writing<2;writing++)for(unsigned stage=1;stage<=(kind==0?2:1);stage++){
  struct model m={};struct rtl_dq_io io={&m,rd,wr,access};
  unsigned words[8],output[8],saved[8];for(unsigned i=0;i<8;i++)words[i]=0x12345600+i;
  memset(output,0xa5,sizeof(output));memcpy(saved,output,sizeof(saved));m.fail=stage;
  assert(transfer(&io,kind,writing,1,writing?words:output)==-ETIMEDOUT);
  if(!writing)assert(!memcmp(output,saved,sizeof(saved)));
  unsigned calls=m.calls,writes=m.writes,command=m.reg[regs[kind]/4];
  for(unsigned retry=0;retry<3;retry++){
   assert(transfer(&io,kind,true,2,words)==-EBUSY);
   assert(transfer(&io,kind,false,2,output)==-EBUSY);
   assert(m.calls==calls&&m.writes==writes&&m.reg[regs[kind]/4]==command);
  }
  /* Normal FE and multicast profile share these same two windows. */
  if(kind<2){unsigned data=kind?0x33cc:0x3920,count=kind?7:4;
   assert(rtl_fe_mc_profile_write(&io,regs[kind],data,2,words,count)==-EBUSY);
   assert(rtl_fe_mc_profile_read(&io,regs[kind],data,2,output,count)==-EBUSY);
   assert(m.calls==calls&&m.writes==writes);
  }
  m.reg[regs[kind]/4]=0;m.fail=0;
  assert(!transfer(&io,kind,true,2,words)&&!transfer(&io,kind,false,2,output));
  assert(!memcmp(words,output,(kind==0?8:kind==1?7:3)*4));cases++;
 }
 for(unsigned fail=1;fail<=6;fail++){
  struct model m={};struct rtl_dq_io io={&m,rd,wr,access};struct rtl_fttr_cf cf={};
  m.key[512][2]=~(1U<<16);m.action[0][0]=0x12345678;
  assert(!rtl_fttr_cf_claim(&io,&cf)&&cf.owned);
  m.fail=m.calls+fail;assert(rtl_fttr_cf_destination(&io,&cf,25)==-ETIMEDOUT&&cf.owned);
  unsigned reg=fail<=2?0x1e14:0x1e04,command=m.reg[reg/4];
  unsigned data=reg==0x1e14?0x1e20:0x1e10,held[3];
  for(unsigned i=0;i<3;i++)held[i]=m.reg[(data-4*i)/4];
  for(unsigned retry=0;retry<2;retry++){
   assert(rtl_fttr_cf_release(&io,&cf)==-EBUSY&&cf.owned);
   assert(m.reg[reg/4]==command);
   for(unsigned i=0;i<3;i++)assert(m.reg[(data-4*i)/4]==held[i]);
  }
  /* Finish the timed-out access, then verify full original restoration. */
  m.reg[0x1e04/4]=m.reg[0x1e14/4]=0;m.fail=0;
  assert(!rtl_fttr_cf_release(&io,&cf)&&!cf.owned&&!cf.active);
  assert(m.action[0][0]==0x12345678&&m.key[512][2]==~(1U<<16));cases++;
 }
 printf("FE classifiers: %u mask-bank/FIB/P6 key/action timeout cases, busy isolation, no partial read output and full CF restore PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='classifier-busy-') as tmp:
 d=Path(tmp)
 if a.revision:
  for name in ('rtl9617c-fe-pipeline.h','rtl9617c-fe-table.h','rtl9617c-fttr-cf.h'):
   (d/name).write_bytes(subprocess.check_output(['git','show',a.revision+':'+str((drivers/name).relative_to(root))],cwd=root))
 (d/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(d),'-I'+str(drivers),str(d/'test.c'),'-o',str(d/'test')],check=True)
 subprocess.run([str(d/'test')],check=True)
