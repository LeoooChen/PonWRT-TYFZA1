#!/usr/bin/env python3
"""Exercise actual shared NI table transactions with competing FE/radio owners."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
from ni_table_fixture import code as table_code

root=Path(__file__).resolve().parents[1]
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--legacy',action='store_true');a=p.parse_args()
text=(driver/'rtl9617c-fe-mcast-route.h').read_text()
if a.legacy:text=subprocess.check_output(['git','show','7aeaf11:'+str((driver/'rtl9617c-fe-mcast-route.h').relative_to(root))],cwd=root,text=True)
def function(name):
    start=re.search(r'^static inline int '+name+r'\([^;]*?\)\s*\{',text,re.M).start()
    end=text.index('{',start)+1;depth=1
    while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]+'\n'
code=table_code+r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sched.h>
#include <time.h>
#include "rtl9617c-qm-deepq.h"
static pthread_mutex_t model_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t staged=PTHREAD_COND_INITIALIZER;
static unsigned regs[0x7000/4],map[512],attrs[64][2],commands,writes;
static int fail_command,corrupt,corrupt_read;
static bool rendezvous,first_staged,second_attempt,second_staged;
struct client {unsigned id,entry,value[2],access,count;int result;};
static unsigned read_reg(void *ctx,unsigned reg){
 (void)ctx;pthread_mutex_lock(&model_lock);unsigned v=regs[reg/4];pthread_mutex_unlock(&model_lock);sched_yield();return v;
}
static void write_reg(void *ctx,unsigned reg,unsigned value){
 struct client *c=ctx;pthread_mutex_lock(&model_lock);regs[reg/4]=value;writes++;
 if(rendezvous&&reg==0x1658){
  if(c->id==1){
   first_staged=true;pthread_cond_broadcast(&staged);
   while(!second_attempt)pthread_cond_wait(&staged,&model_lock);
   struct timespec until;clock_gettime(CLOCK_REALTIME,&until);until.tv_nsec+=50000000;
   if(until.tv_nsec>=1000000000){until.tv_sec++;until.tv_nsec-=1000000000;}
   while(!second_staged)if(pthread_cond_timedwait(&staged,&model_lock,&until)==ETIMEDOUT)break;
  }else{second_staged=true;pthread_cond_broadcast(&staged);}
 }
 pthread_mutex_unlock(&model_lock);sched_yield();
}
static int indirect(void *ctx,unsigned access,unsigned command){
 (void)ctx;pthread_mutex_lock(&model_lock);commands++;
 if(fail_command==(int)commands){regs[access/4]=command;pthread_mutex_unlock(&model_lock);return -ETIMEDOUT;}
 unsigned index=command&511;
 if(access==0x1654){
  if(command&BIT(30)){map[index]=regs[0x1658/4]^(corrupt?1:0);}
  else regs[0x1658/4]=map[index]^(corrupt_read?1:0);
 }else{
  assert(access==0x66a4&&index<64);
  for(unsigned i=0;i<2;i++)if(command&BIT(30))attrs[index][i]=regs[(0x66ac-i*4)/4];
  else regs[(0x66ac-i*4)/4]=attrs[index][i];
 }
 regs[access/4]=0;pthread_mutex_unlock(&model_lock);sched_yield();return 0;
}
'''+function('rtl_fe_mc_route_read')+function('rtl_fe_mc_route_write')+r'''
static int transfer(struct client *c){
 if(c->access==0x1654){struct rtl_dq_io io={c,read_reg,write_reg,indirect};return rtl_fe_mc_route_write(&io,0x1654,0x1658,c->entry,c->value,1);}
 return rtl9617c_ni_table(c,read_reg,write_reg,indirect,c->access,c->entry,NULL,c->value,c->count);
}
static void *compete(void *arg){
 struct client *c=arg;
 if(rendezvous&&c->id==2){pthread_mutex_lock(&model_lock);while(!first_staged)pthread_cond_wait(&staged,&model_lock);second_attempt=true;pthread_cond_broadcast(&staged);pthread_mutex_unlock(&model_lock);}
 c->result=transfer(c);return NULL;
}
int main(void){
 struct client a={1,19,{9,0},0x1654,1,0},b={2,27,{8,0},0x1654,1,0};pthread_t x,y;
 rendezvous=true;pthread_create(&x,NULL,compete,&a);pthread_create(&y,NULL,compete,&b);pthread_join(x,NULL);pthread_join(y,NULL);
 assert(!a.result&&!b.result&&map[19]==9&&map[27]==8);
 rendezvous=false;
 for(unsigned round=0;round<512;round++){
  a.value[0]=round+17;b.value[0]=round+900;
  a.value[1]=round+55;b.value[1]=round+1000;
  a.access=b.access=round&1?0x66a4:0x1654;a.count=b.count=round&1?2:1;
  pthread_create(&x,NULL,compete,&a);pthread_create(&y,NULL,compete,&b);pthread_join(x,NULL);pthread_join(y,NULL);
  assert(!a.result&&!b.result);
  if(round&1){assert(!memcmp(attrs[19],a.value,8)&&!memcmp(attrs[27],b.value,8));}
  else assert(map[19]==a.value[0]&&map[27]==b.value[0]);
 }
 a.access=0x1654;a.count=1;a.value[0]=7;
 for(unsigned stage=1;stage<=2;stage++){
  commands=0;fail_command=stage;assert(transfer(&a)==-ETIMEDOUT);
  unsigned before=writes;assert(transfer(&a)<0&&writes==before&&commands==stage);
  regs[0x1654/4]=0;fail_command=0;assert(!transfer(&a)&&map[19]==7);
 }
 corrupt=1;assert(transfer(&a)==-EIO);corrupt=0;
 corrupt_read=1;assert(transfer(&a)==-EIO);corrupt_read=0;
 unsigned result=0xa5a5a5a5;commands=0;fail_command=1;
 assert(rtl9617c_ni_table(&a,read_reg,write_reg,indirect,0x1654,19,&result,NULL,1)==-ETIMEDOUT&&result==0xa5a5a5a5);
 regs[0x1654/4]=0;fail_command=0;assert(!transfer(&a));
 puts("Shared NI windows: forced interleaving,1024 competing transactions, busy timeout, readback corruption and retry PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='shared-ni-') as tmp:
    d=Path(tmp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-O1','-g','-pthread','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(driver),str(d/'test.c'),'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
    if not a.legacy:
        (d/'tables.c').write_text(table_code+(root/'scripts/test_fe_mcast_table.c').read_text())
        subprocess.run(['cc','-O1','-g','-pthread','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                        '-I'+str(driver),str(d/'tables.c'),'-o',str(d/'tables')],check=True)
        subprocess.run([str(d/'tables')],check=True)
