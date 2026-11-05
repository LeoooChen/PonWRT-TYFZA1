#!/usr/bin/env python3
"""Check retained Holdover/local TX controls and refuse OAM reboot/TX operations."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
kernel = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
source = root / "port/packages/tyfza1-pon-control/src"


def function(text, name):
    start = re.search(r"^.*\b" + name + r"\([^;]*?\)\s*\{", text, re.M).start()
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + "\n"


model = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define HZ 1000UL
#define MAX_JIFFY_OFFSET 0x7fffffffUL
#define min(a,b) ((a)<(b)?(a):(b))
#define msecs_to_jiffies(v) (v)
#define time_before(a,b) ((long)((a)-(b)) < 0)
#define RTL_PON_EPON_1G 0
#define OFFLINE 0x20e0
#define REGISTER_STATUS 0x2014
#define system_wq NULL
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct work_struct {int unused;};
struct delayed_work {struct work_struct work; bool pending; unsigned long delay;};
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
struct rtl9617c_pon {
 int mode,lock,control_lock,*tx_disable,epon_tx_error,epon_holdover_error;
 unsigned int llid_count; void *mac,*dev;
 bool traffic_enabled,mpcp_enabled; u32 registered;
 u16 epon_tx_action,epon_holdover_ms[32];
 unsigned long epon_tx_until,epon_holdover_start[32];
 uint64_t epon_llid_epoch[32];
 u32 epon_holdover_pending,epon_holdover_active,epon_holdover_enabled;
 struct delayed_work epon_tx_work,epon_holdover_work;
};
static unsigned long jiffies;
static bool offline_timeout;
static int gpio_error;
static void mutex_lock(int *p) {assert(!*p);*p=1;}
static void mutex_unlock(int *p) {assert(*p);*p=0;}
static int rtl_pon_mode_is_epon(int mode) {return mode<3;}
static void gpiod_set_value_cansleep(int *p,int v) {*p=v;}
static int gpiod_get_value_cansleep(int *p) {return gpio_error ? gpio_error : *p;}
static u16 get_unaligned_be16(const u8 *p) {return (p[0]<<8)|p[1];}
static uint64_t get_unaligned_be64(const u8 *p) {uint64_t v=0;for(int i=0;i<8;i++)v=(v<<8)|p[i];return v;}
static u32 readl(void *p) {return *(u32 *)p;}
static void rtl_pon_update(void *p,u32 mask,u32 v) {
 *(u32 *)p=(*(u32 *)p&~mask)|v;
 if (!offline_timeout) *(u32 *)((char *)p+REGISTER_STATUS-OFFLINE)&=~v;
}
#define readl_poll_timeout(p,v,condition,delay,timeout) ((v)=readl(p),(condition)?0:-ETIMEDOUT)
static void mod_delayed_work(void *q,struct delayed_work *w,unsigned long delay) {(void)q;w->pending=true;w->delay=delay;}
static void cancel_delayed_work(struct delayed_work *w) {w->pending=false;}
#define dev_err(...) ((void)0)
#define dev_err_ratelimited(...) ((void)0)
'''

kernel_test = r'''
int main(void) {
 u32 regs[0x2100/4]={}; int gpio=0;
 struct rtl9617c_pon p={.mac=regs,.tx_disable=&gpio,.llid_count=2,
  .traffic_enabled=true,.mpcp_enabled=true,.registered=3,.epon_holdover_enabled=3,
  .epon_holdover_ms={200,600},.epon_llid_epoch={7,8}};
 regs[REGISTER_STATUS/4]=3; regs[0x10/4]=1; jiffies=1000;
 assert(rtl_mpcp_holdover(&p,3)==3 && p.epon_holdover_active==3);
 assert(p.epon_holdover_work.delay==200);
 jiffies=1199; assert(rtl_mpcp_holdover(&p,3)==3);
 regs[0x10/4]=4; assert(rtl_mpcp_holdover(&p,3)==3 && !p.epon_holdover_pending);
 regs[0x10/4]=1; jiffies=2000; assert(rtl_mpcp_holdover(&p,3)==3);
 jiffies=2200; assert(rtl_mpcp_holdover(&p,3)==2 && p.epon_holdover_active==2);
 assert(regs[OFFLINE/4]==1 && p.epon_holdover_work.delay==400);
 jiffies=2600; offline_timeout=true;
 assert(rtl_mpcp_holdover(&p,2)==2 && p.epon_holdover_error==-ETIMEDOUT && !p.epon_holdover_active);
 offline_timeout=false; assert(rtl_mpcp_holdover(&p,2)==0);
 p.epon_holdover_ms[0]=0; regs[REGISTER_STATUS/4]=1;
 assert(rtl_mpcp_holdover(&p,1)==0);
 p.epon_holdover_enabled=0; regs[REGISTER_STATUS/4]=2;
 assert(rtl_mpcp_holdover(&p,2)==0);
 u8 command[11]={0,0,0,0,0,0,0,0,7,0,2};
 assert(!rtl_pon_epon_tx_control(&p,command,11) && gpio==1);
 jiffies+=1000; command[10]=5;
 assert(!rtl_pon_epon_tx_control(&p,command,11));
 rtl_epon_tx_work(&p.epon_tx_work.work); /* Already-dispatched older timer. */
 assert(gpio==1 && p.epon_tx_work.delay==5000);
 jiffies+=5000; rtl_epon_tx_work(&p.epon_tx_work.work); assert(gpio==0);
 command[9]=command[10]=255; assert(!rtl_pon_epon_tx_control(&p,command,11));
 jiffies+=100000; rtl_epon_tx_work(&p.epon_tx_work.work); assert(gpio==1);
 command[9]=command[10]=0; p.traffic_enabled=false;
 assert(!rtl_pon_epon_tx_control(&p,command,11) && gpio==1);
 command[8]=6; assert(rtl_pon_epon_tx_control(&p,command,11)==-ESTALE);
 command[8]=7; gpio_error=-EIO;
 assert(rtl_pon_epon_tx_control(&p,command,11)==-EIO);
 puts("Holdover deadlines/recovery/zero/timeout and TX replacement/stale-session/stop/GPIO error passed");
}
'''

backend_test = r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "pon-backend.c"
int main(int argc,char **argv) {
 (void)argc;
 struct pon_backend b={.llid=1,.epoch=0x102030405060708ULL};
 struct pon_oam_identity id={.mac={2,1,2,3,4,5}};
 struct pon_oam_request r={.branch=0xc7,.leaf=0xa1};
 unsigned char power[12]={0,2,2,1,2,3,4,5,0,0,0,0},v[16],out[16];
 snprintf(b.device,sizeof(b.device),"%s",argv[1]);pon_backend_bind(&id,&b);
 assert(id.set_parameter(&b,&r,power,12)==0x87);
 char path[600];FILE *f;
 r.leaf=8;unsigned char hold[]={0,0,0,2,0,0,0,200};
 assert(id.set_parameter(&b,&r,hold,8)==0x80);
 snprintf(path,sizeof(path),"%s/epon_holdover",b.device);
 f=fopen(path,"rb");assert(fread(v,1,12,f)==12);fclose(f);
 assert(v[0]==1 && v[9]==1 && v[10]==0 && v[11]==200);
 f=fopen(path,"wb");assert(fwrite("\0\0\0\0\0\1\0\310",1,8,f)==8);fclose(f);
 assert(id.get_parameter(&b,&r,out,sizeof(out))==8 && !memcmp(out,hold,8));
 hold[5]=1;assert(id.set_parameter(&b,&r,hold,8)==0x86);
 r.leaf=6;unsigned char sla[18]={1,1,0,0,0,0,0,1,0,0,0,0,0,0,100,3,232,5};
 assert(id.set_parameter(&b,&r,sla,sizeof(sla))==0x80);
 snprintf(path,sizeof(path),"%s/epon_sla",b.device);
 unsigned char record[89]={18};memcpy(record+1,sla,sizeof(sla));
 f=fopen(path,"rb");assert(fread(v,1,9,f)==9);fclose(f);
 assert(v[0]==1 && !memcmp(v+1,"\1\2\3\4\5\6\7\10",8));
 f=fopen(path,"wb");assert(!fseek(f,89,SEEK_SET));assert(fwrite(record,1,89,f)==89);fclose(f);
 unsigned char sla_out[88];assert(id.get_parameter(&b,&r,sla_out,sizeof(sla_out))==18);
 assert(!memcmp(sla_out,sla,sizeof(sla)));
 r.branch=0xc9;r.leaf=1;assert(id.set_parameter(&b,&r,NULL,0)==0x87);
 puts("Native Holdover/SLA wire retained; OAM transmitter control and reboot refused");
}
'''

with tempfile.TemporaryDirectory(prefix="epon-management-", dir=root / "artifacts") as tmp:
    tmp = Path(tmp)
    epon = (kernel / "rtl9617c-pon-epon.c").read_text()
    mpcp = (kernel / "rtl9617c-pon-mpcp.c").read_text()
    production = "".join(function(epon, name) for name in
                         ("rtl_epon_tx_apply", "rtl_epon_tx_work", "rtl_pon_epon_tx_control"))
    production += function(mpcp, "rtl_mpcp_holdover")
    for name, text, extra in (
        ("kernel", model + production + kernel_test, []),
        ("backend", backend_test, []),
    ):
        cfile = tmp / (name + ".c")
        binary = tmp / name
        cfile.write_text(text)
        subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-I" + str(source),
                        str(cfile), *extra, "-o", str(binary)], check=True)
        subprocess.run([str(binary), str(tmp)], check=True)
