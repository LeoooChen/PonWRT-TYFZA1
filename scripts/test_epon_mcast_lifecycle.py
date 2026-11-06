#!/usr/bin/env python3
"""Execute EPON snapshot publication and LLID mutation with retry faults."""
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
drivers=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
pdc=(drivers/'rtl9617c-pon-pdc.c').read_text()
mpcp=(drivers/'rtl9617c-pon-mpcp.c').read_text()


def function(source,name):
    start=re.search(r'^[\w *]+\b'+name+r'\([^;]*?\)\s*\{',source,re.M).start()
    end=source.index('{',start)+1;depth=1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]+'\n'


code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#include "rtl9617c-epon-path.h"
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-pon-data.h"
#define BIT(n) (1U<<(n))
#define smp_store_release(p,v) (*(p)=(v))
#define lockdep_assert_held(p) assert(*(p))
#define LLID_DATA 0x164
#define LLID_ACCESS 0x168
#define LLID_RESULT 0x16c
#define PDC_DATA0 0x9028
#define PDC_DATA1 0x9024
struct rtl9617c_pon {
 int lock;void *ni_dev,*mac;bool epon_paths_ready,pdc_configured,puc_configured,traffic_enabled,mpcp_enabled,fe_enabled;
 int epon_paths_error,pdc_error;u64 epon_path_epoch,epon_epoch;
 unsigned llid_count,registered,epon_holdover_active;enum rtl_pon_mode mode;
 u8 epon_sla_length[8];
 u8 pdc_source[64];struct rtl_epon_paths epon_paths;
};
static struct rtl9617c_pon *current;
static struct rtl_epon_paths published;
static unsigned calls,withdraws,publishes,writes,pdc_writes;
static int failure,access_failure;
static bool admitted;
static unsigned readl(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void writel(unsigned v,void *p){assert(!admitted&&!current->epon_paths_ready);writes++;memcpy(p,&v,4);}
static int rtl9617c_ni_epon_paths(void *ni,const struct rtl_epon_paths *p){
 (void)ni;calls++;assert(!current->epon_paths_ready);admitted=false;
 if(!p)withdraws++;else publishes++;
 if(failure){int r=failure;failure=0;return r;}
 if(p){published=*p;admitted=true;}else memset(&published,0,sizeof(published));return 0;
}
static int rtl_pdc_access(struct rtl9617c_pon *p,unsigned index,bool write){(void)p;assert(index<256&&write&&!admitted);pdc_writes++;return access_failure;}
/* Poll completion/result are the only modeled LLID hardware boundary. */
#define readl_poll_timeout(reg,status,condition,delay,timeout) ((void)(reg),(void)(delay),(void)(timeout),(status)=0,(condition)?0:-ETIMEDOUT)
'''
for name in ('rtl_pon_epon_paths_invalidate','rtl_pon_epon_paths_refresh','rtl_pon_pdc_refresh'):
    code+=function(pdc,name)
code+=function(mpcp,'rtl_pon_llid_update')
code+=r'''
static void set(unsigned reg,unsigned val){memcpy(current->mac+reg,&val,4);}
static void setup(struct rtl9617c_pon *p,unsigned *memory,unsigned count){
 memset(p,0,sizeof(*p));memset(memory,0,0xa000);current=p;p->mac=memory;p->lock=1;
 p->mode=RTL_PON_EPON_1G;p->llid_count=count;p->registered=count==32?~0U:BIT(count)-1;
 p->pdc_configured=p->puc_configured=p->traffic_enabled=p->mpcp_enabled=p->fe_enabled=true;
 p->epon_epoch=33;set(0x10,BIT(2));set(0x2014,p->registered);
 set(0x804c,count>16?1:0);for(unsigned i=0;i<8;i++){set(0x81bc-i*4,~0U);p->epon_sla_length[i]=1;}
 for(unsigned i=0;i<64;i++)p->pdc_source[i]=rtl_pon_data_source(i,0);
 admitted=false;calls=withdraws=publishes=writes=pdc_writes=0;failure=access_failure=0;
}
int main(void){
 unsigned memory[0xa000/4];struct rtl9617c_pon pon;
 for(unsigned count=1;count<=32;count++) {
  setup(&pon,memory,count);assert(!rtl_pon_epon_paths_refresh(&pon));
  assert(pon.epon_paths_ready&&admitted&&published.count==count&&published.queues==(count>16?4:8));
  unsigned old=calls;u64 generation=published.generation;
  assert(!rtl_pon_epon_paths_refresh(&pon)&&calls==old);
  /* Contents change at the same MAC instance: before PDC/next snapshot. */
  set(0x64,0x800000|123);assert(!rtl_pon_pdc_refresh(&pon));
  assert(!pon.epon_paths_ready&&!admitted&&!pdc_writes);
  assert(!rtl_pon_epon_paths_refresh(&pon)&&published.generation>generation&&published.table[0]==0x80007b);
  /* Withdrawal failure keeps old owner retryable and prevents LLID IO. */
  failure=-ETIMEDOUT;old=writes;
  assert(rtl_pon_llid_update(&pon,true,1,0,234,false)==-ETIMEDOUT);
  assert(writes==old&&!pon.epon_paths_ready&&!admitted&&pon.epon_paths_error==-ETIMEDOUT);
  set(LLID_RESULT,6);assert(!rtl_pon_llid_update(&pon,true,1,0,234,false)&&writes==old+2);
  assert(!pon.epon_paths_error);
  /* Failed PDC change never admits a stale POL map. */
  assert(!rtl_pon_epon_paths_refresh(&pon));set(0x68,0x800000|321);access_failure=-EIO;
  assert(rtl_pon_pdc_refresh(&pon)==-EIO&&!pon.pdc_configured&&!admitted);
  assert(!rtl_pon_epon_paths_refresh(&pon)&&!pon.epon_paths_ready);
 }
 setup(&pon,memory,32);assert(!rtl_pon_epon_paths_refresh(&pon));
 set(0x2014,1);pon.registered=1;pon.epon_epoch++;
 assert(!rtl_pon_epon_paths_refresh(&pon)&&published.active==1&&published.registration_epoch==34);
 pon.epon_sla_length[0]=0;assert(!rtl_pon_epon_paths_refresh(&pon)&&!published.active);
 pon.epon_sla_length[0]=1;assert(!rtl_pon_epon_paths_refresh(&pon)&&published.active==1);
 set(0x10,1);pon.epon_holdover_active=1;
 assert(!rtl_pon_epon_paths_refresh(&pon)&&published.active==1);
 pon.epon_holdover_active=0;assert(!rtl_pon_epon_paths_refresh(&pon)&&!published.active);
 pon.mode=RTL_PON_GPON;assert(!rtl_pon_epon_paths_refresh(&pon)&&!admitted&&!pon.epon_paths_ready);
 /* A failed autonomous remap withdrawal must be retried before PDC IO,
  * even though the first failure already cleared paths_ready. */
 setup(&pon,memory,32);assert(!rtl_pon_epon_paths_refresh(&pon));set(0x68,0x800000|321);
 failure=-ETIMEDOUT;assert(rtl_pon_pdc_refresh(&pon)==-ETIMEDOUT&&!pon.epon_paths_ready&&!pdc_writes);
 failure=-EIO;assert(rtl_pon_pdc_refresh(&pon)==-EIO&&!pdc_writes&&!writes);
 assert(!rtl_pon_pdc_refresh(&pon)&&pdc_writes==2&&!pon.epon_paths_error);
 assert(!rtl_pon_epon_paths_refresh(&pon)&&pon.epon_paths_ready);
 /* Foreign queue mode and queue-enable changes cannot keep old admission. */
 set(0x804c,0);assert(rtl_pon_epon_paths_refresh(&pon)==-EUCLEAN&&!admitted);
 set(0x804c,1);set(0x81bc,5);assert(!rtl_pon_epon_paths_refresh(&pon)&&published.queue_mask[0]==5);
 setup(&pon,memory,1);failure=-ENOMEM;
 assert(rtl_pon_epon_paths_refresh(&pon)==-ENOMEM&&!pon.epon_paths_ready);
 assert(!rtl_pon_epon_paths_refresh(&pon)&&pon.epon_paths_ready&&admitted);
 puts("EPON multicast lifecycle: 32 queue modes, unchanged snapshot, LLID content/remap, failed withdrawal before IO, failed PDC, holdover/registration/mode and publication retry passed");
}
'''
with tempfile.TemporaryDirectory(prefix='epon-mcast-life-') as tmp:
    tmp=Path(tmp);(tmp/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-I'+str(drivers),str(tmp/'test.c'),'-o',str(tmp/'test')],check=True)
    subprocess.run([str(tmp/'test')],check=True)
