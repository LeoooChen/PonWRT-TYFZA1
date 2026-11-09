#!/usr/bin/env python3
"""Compare native MBID operations and complete port startup with target traces."""
import argparse,ctypes as C,json,subprocess,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];d=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,required=True);a=p.parse_args();original=json.loads(a.original.read_text())
code=r'''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(x) (sizeof(x)/sizeof(*(x)))
#define min(a,b) ((a)<(b)?(a):(b))
#define RTL_AX_AP_POWER_H
struct rtl_ax_io {void *ctx;unsigned(*read32)(void*,unsigned);u8(*read8)(void*,unsigned);void(*write32)(void*,unsigned,unsigned);void(*write8)(void*,unsigned,u8);void(*delay_us)(void*,unsigned);int(*poll32)(void*,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned*);};
static u8 regs[65536];static unsigned trace[2048],count,clock_us;static int fail_drop;static bool frozen;
static unsigned read32(void *p,unsigned a){(void)p;if(a==0xc434)return frozen?0:clock_us;unsigned v;memcpy(&v,regs+a,4);return v;}
static u8 read8(void *p,unsigned a){(void)p;return regs[a];}
static void write32(void *p,unsigned a,unsigned v){(void)p;memcpy(regs+a,&v,4);trace[count++]=32;trace[count++]=a;trace[count++]=v;}
static void write8(void *p,unsigned a,u8 v){(void)p;regs[a]=v;trace[count++]=8;trace[count++]=a;trace[count++]=v;}
static void update16(const struct rtl_ax_io *io,unsigned a,unsigned mask,unsigned value){(void)io;uint16_t v;memcpy(&v,regs+a,2);unsigned old=v;v=(v&~mask)|value;if(old!=v){memcpy(regs+a,&v,2);trace[count++]=16;trace[count++]=a;trace[count++]=v;}}
#define rtl_ax_update16 update16
static void delay(void*p,unsigned us){(void)p;clock_us+=us;}
static struct rtl_ax_io io={.read32=read32,.read8=read8,.write32=write32,.write8=write8,.delay_us=delay};
static int drop(void *p,unsigned mbid){(void)p;trace[count++]=0;trace[count++]=mbid;trace[count++]=8;return fail_drop==(int)mbid?-EIO:0;}
'''+ '#include "'+str(d/'port0.h')+'"\n'+r'''
static struct rtl_ax_port0 port;
void reset(unsigned initial){memset(regs,0,sizeof(regs));memset(&port,0,sizeof(port));memcpy(regs+0xc568,&initial,4);count=clock_us=0;fail_drop=0;frozen=false;}
void seed(unsigned a,unsigned value){memcpy(regs+a,&value,4);}
unsigned result_trace(unsigned *out){memcpy(out,trace,count*4);return count/3;}
int run(unsigned cfg,unsigned mbid,unsigned value){
 if(cfg==1)return rtl_ax_port0_beacon(&io,0,value);
 if(cfg==19){int ret=rtl_ax_port0_beacon(&io,mbid,value);if(!ret&&!value)ret=drop(NULL,mbid);return ret;}
 if(cfg==99)return rtl_ax_port0_start(&io,&port,NULL,drop,100,1);
 return -EINVAL;
}
void faults(void){
 for(unsigned i=1;i<=7;i++){
  reset(0xffff);port.ready=true;fail_drop=i;
  assert(rtl_ax_port0_start(&io,&port,NULL,drop,100,1)==-EIO&&!port.ready);
 }
 reset(0);frozen=true;assert(rtl_ax_port0_start(&io,&port,NULL,drop,100,1)==-EIO&&!port.ready);
 reset(0);assert(rtl_ax_port0_start(&io,&port,NULL,drop,0,1)==-EINVAL&&!count);
 assert(rtl_ax_port0_start(&io,&port,NULL,drop,100,256)==-EINVAL&&!count);
 assert(rtl_ax_port0_vap_prepare(&io,&port,1,100,1)==-ENETDOWN&&!count);
 for(unsigned mbid=1;mbid<8;mbid++){
  reset(0xa50700ff);port.ready=true;port.interval=100;seed(0xc400,0x13c04);seed(0xc414,0xc0064);seed(0xc540,0x12345678);seed(0xc544,0x87654321);
  u8 before[65536];memcpy(before,regs,sizeof(before));
  assert(!rtl_ax_port0_vap_prepare(&io,&port,mbid,100,3));
  assert(read32(NULL,0xc400)==0x13c04&&read32(NULL,0xc414)==0xc0064);
  assert(!(read32(NULL,0xc568)&BIT(mbid))&&regs[0xc540+mbid]==3&&regs[0xc590+mbid]==50);
  for(unsigned i=0;i<65536;i++)if(i!=0xc568&&i!=0xc540+mbid&&i!=0xc590+mbid&&i!=0xca08)assert(regs[i]==before[i]);
  assert(!rtl_ax_port0_beacon(&io,mbid,true));assert(read32(NULL,0xc568)&BIT(mbid));
  assert(!rtl_ax_port0_beacon(&io,mbid,false));assert(!(read32(NULL,0xc568)&BIT(mbid)));
  unsigned prior=count;assert(rtl_ax_port0_vap_prepare(&io,&port,mbid,101,3)==-EINVAL&&count==prior);
 }
 /* Restart root without touching live VAP DTIM/HIQ/TSF/enable state. */
 for(unsigned dtim=1;dtim<=255;dtim++) {
  reset(0xa50700fe);port.ready=true;port.interval=100;
  seed(0xc400,0x13c04);seed(0xc414,0xc0064);seed(0xc540,0x12345678);seed(0xc544,0x87654321);
  for(unsigned i=1;i<8;i++)regs[0xc590+i]=10+i;
  assert(!rtl_ax_port0_root_resume(&io,&port,100,dtim));
  assert(read32(NULL,0xc568)==0xa50700fe&&read32(NULL,0xc414)==0xc0064);
  assert(read32(NULL,0xc540)==0x12345678&&read32(NULL,0xc544)==0x87654321);
  for(unsigned i=1;i<8;i++)assert(regs[0xc590+i]==10+i);
  assert(regs[0xc427]==dtim&&regs[0xc590]==50&&!(read32(NULL,0xc400)&BIT(12)));
  unsigned before=count;assert(rtl_ax_port0_root_resume(&io,&port,200,dtim)==-EINVAL&&count==before);
 }
 reset(0);assert(rtl_ax_port0_root_resume(&io,&port,100,1)==-ENETDOWN&&!count);
 reset(0);assert(rtl_ax_port0_beacon(&io,1,true)==-EINVAL&&!count);
 assert(rtl_ax_port0_beacon(&io,8,true)==-EINVAL&&!count);
}
'''
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'test.c';so=Path(temp)/'test.so';c.write_text(code)
 subprocess.run(['cc','-shared','-fPIC','-O2','-Wall','-Wextra','-Werror',str(c),'-o',str(so)],check=True)
 lib=C.CDLL(str(so));out=(C.c_uint*2048)();cases=0
 for row in original['records']:
  cfg=row['cfg']
  if cfg not in (1,19,'init'):continue
  initial=row['initial'] if cfg=='init' else 0xa5077f01 if cfg==19 and row['value'] else 0xa5077fff
  lib.reset(initial)
  if cfg!='init':lib.seed(0xc400,0x13c04);lib.seed(0xc414,0xc0064)
  assert lib.run(99 if cfg=='init' else cfg,row.get('mbid',0),row.get('value',0))==row['result']
  n=lib.result_trace(out);actual=[tuple(out[i:i+3]) for i in range(0,n*3,3)]
  expected=[]
  for op in row['operations']:
   if op['op'].startswith('write'):
    address=int(op['address'],16);value=int(op['value'],16)
    # Native intentionally keeps beacon TX disabled until its template exists.
    # Compare all other startup writes without silently masking unrelated bits.
    if cfg=='init' and address==0xc400:
     if op['phase']=='TX_SW':continue
     value&=~(1<<12)
    expected.append((int(op['op'][5:]),address,value))
   elif op['op']=='H2C':
    wire=bytes.fromhex(op['payload']);assert wire[0]==8 and op['class']==9 and op['function']==27 and not op['done_ACK'];expected.append((0,wire[4],8))
  assert actual==expected,({k:v for k,v in row.items() if k!='operations'},actual,expected)
  cases+=1
 lib.faults()
 c.write_text(code+'\nint main(void){faults();return 0;}\n');binary=Path(temp)/'sanitized'
 subprocess.run(['cc','-O1','-fsanitize=address,undefined',str(c),'-o',str(binary)],check=True);subprocess.run([str(binary)],check=True)
print(f'Native MBID: {cases} original full register/HIQ traces match; VAP isolation and startup/drop/timer faults passed')

# Production HIQ command publication keeps failed MBIDs pending for cleanup.
pc=(d/'pci.c').read_text();start=pc.index('static int rtl_ax_port0_drop_mbid(');end=pc.index('\n}\n',start)+3
code=r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define RTL_AX_H2C_CMD 0
struct rtl_ax_ap {u8 mbid_drop_pending;};
static int error;static unsigned calls,last;
static void rtl_ax_store32(u8 *p,unsigned v){memcpy(p,&v,4);}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,const u8 *p,unsigned bytes,bool ack){
 assert(q==0&&cat==1&&cls==9&&fn==27&&bytes==24&&!ack);last=p[4];calls++;
 assert(p[0]==8);if(last)assert(r->mbid_drop_pending&BIT(last));
 for(unsigned i=1;i<24;i++)if(i!=4)assert(!p[i]);return error;
}
'''+pc[start:end]+r'''
int main(void){
 struct rtl_ax_ap r={};
 for(unsigned id=1;id<8;id++){
  error=-ETIMEDOUT;assert(rtl_ax_port0_drop_mbid(&r,id)==error&&last==id&&r.mbid_drop_pending&BIT(id));
 }
 for(unsigned id=1;id<8;id++){
  error=0;unsigned old=r.mbid_drop_pending;
  assert(!rtl_ax_port0_drop_mbid(&r,id)&&r.mbid_drop_pending==(old&~BIT(id)));
 }
 unsigned before=calls;assert(rtl_ax_port0_drop_mbid(&r,8)==-EINVAL&&calls==before);
 assert(!rtl_ax_port0_drop_mbid(&r,0)&&!r.mbid_drop_pending);
}
'''
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'drop.c';binary=Path(temp)/'drop';c.write_text(code)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined',str(c),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
print('Actual HIQ drop: seven independent publication-failure retries and root/invalid-ID boundaries passed')
