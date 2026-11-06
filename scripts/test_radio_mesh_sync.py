#!/usr/bin/env python3
"""Check mesh TSF IO against stock, then exercise wrap, drift and failures."""
from pathlib import Path
import ctypes
import re
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle


def function(file, name):
    s = (driver / file).read_text()
    start = re.search(r'^(?:static |u32 )[^;]*?\b' + name + r'\([^;]*?\)\s*\{', s, re.M).start()
    end = s.index('{', start) + 1
    depth = 1
    while depth:
        depth += (s[end] == '{') - (s[end] == '}')
        end += 1
    return s[start:end] + '\n'

code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
typedef uint64_t u64;typedef uint32_t u32;typedef int64_t s64;
#define BIT(n) (1U<<(n))
#define GFP_KERNEL 0
#define div_s64(v,d) ((v)/(d))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define dev_err(...) ((void)0)
static u32 regs[0x10000/4], trace[32][2];static unsigned count, calls, fail, stops;
static u64 clock_us, tsf[2];
static u32 read32(void *ctx,unsigned reg){(void)ctx;clock_us++;tsf[0]++;tsf[1]++;
 if(reg==0xc438)return tsf[0];if(reg==0xc43c)return tsf[0]>>32;return regs[reg/4];}
static void write32(void *ctx,unsigned reg,unsigned v){(void)ctx;assert(count<32);trace[count][0]=reg;trace[count++][1]=v;regs[reg/4]=v;}
static int poll32(void *ctx,unsigned reg,unsigned mask,unsigned expected,unsigned delay,unsigned timeout,unsigned *last){
 (void)ctx;(void)delay;(void)timeout;assert(mask==BIT(30)&&!expected);calls++;
 if(calls==fail){*last=regs[reg/4];return -ETIMEDOUT;}
 unsigned target=(reg-0xc2a0)/4,source=(regs[reg/4]>>24)&7,v=regs[reg/4]&0x7ffff;
 assert(target!=source&&target<2&&source<2);
 int delta=(v&0x3ffff)*32;if(v&BIT(18))delta=-delta;
 tsf[target]=tsf[source]+delta;regs[reg/4]&=~BIT(30);*last=regs[reg/4];return 0;}
struct rtl_ax_io {void *ctx;u32(*read32)(void*,unsigned);void(*write32)(void*,unsigned,unsigned);int(*poll32)(void*,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned*);};
#define container_of(p,t,m) ((t*)((char*)(p)-offsetof(t,m)))
#define usecs_to_jiffies(x) (x)
struct wiphy {};struct wiphy_work {unsigned queued,delay;};
struct ieee80211_vif {struct {unsigned beacon_int;}bss_conf;};
struct sk_buff {};static struct sk_buff beacon;static unsigned fetched,freed;
struct rtl_ax_ap {bool ap_started,beacon_enabled;struct ieee80211_vif *ap_vif;struct ieee80211_hw *hw;struct {struct wiphy_work work;}mesh_sync_work;struct rtl_ax_io io;s64 mesh_tsf_remainder;int command_mutex;void *pdev;};
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
static struct sk_buff *ieee80211_beacon_get_template(struct ieee80211_hw *h,struct ieee80211_vif *v,void *offs,unsigned link){(void)h;(void)v;assert(!offs&&!link);fetched++;return &beacon;}
static void dev_kfree_skb(struct sk_buff *s){assert(s==&beacon);freed++;}
#define wiphy_delayed_work_queue(w,p,d) ((void)(w),(p)->work.queued++,(p)->work.delay=(d))
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *h){return h->radio;}
static void *ieee80211_vif_to_wdev(struct ieee80211_vif *v){return v;}
static void cfg80211_stop_iface(void*w,void*v,int g){(void)w;(void)v;(void)g;stops++;}
'''
for name in ('rtl_ax_port0_tsf', 'rtl_ax_tsf_sync', 'rtl_ax_port0_offset_tsf'):
    code += function('port0.h', name)
code += function('monitor.h', 'rtl_ax_offset_tsf')
code += function('monitor.h', 'rtl_ax_mesh_sync_work')
code += r'''
static struct rtl_ax_ap radio={.io={NULL,read32,write32,poll32}};
unsigned native(unsigned src,unsigned dst,int off,unsigned initial,unsigned *out){
 count=calls=fail=0;regs[(0xc2a0+dst*4)/4]=initial;
 assert(!rtl_ax_tsf_sync(&radio.io,src,dst,off));
 for(unsigned i=0;i<4;i++)out[i]=trace[i/2][i%2];return count;}
int check(void){
 struct ieee80211_vif mesh={.bss_conf={100}};radio.ap_vif=&mesh;
 rtl_ax_mesh_sync_work(NULL,&radio.mesh_sync_work.work);assert(!fetched&&!radio.mesh_sync_work.work.queued);
 radio.ap_started=true;rtl_ax_mesh_sync_work(NULL,&radio.mesh_sync_work.work);assert(!fetched);
 radio.beacon_enabled=true;rtl_ax_mesh_sync_work(NULL,&radio.mesh_sync_work.work);
 assert(fetched==1&&freed==1&&radio.mesh_sync_work.work.queued==1&&radio.mesh_sync_work.work.delay==102400);
 radio.beacon_enabled=false;rtl_ax_mesh_sync_work(NULL,&radio.mesh_sync_work.work);assert(fetched==1);
 radio.ap_started=false;radio.ap_vif=NULL;

 tsf[0]=((u64)9<<32)|0xfffffffeU;clock_us=0;u64 t=rtl_ax_port0_tsf(&radio.io);
 assert((t>>32)==10 && t==tsf[0]-1);
 for(unsigned failure=0;failure<3;failure++){
  count=calls=0;fail=failure;regs[0xc440/4]=0x24000;regs[0xc2a0/4]=0x80000000;regs[0xc2a4/4]=0x80000000;
  tsf[0]=10000000;tsf[1]=1;u64 before=tsf[0],ticks=clock_us;
  int ret=rtl_ax_port0_offset_tsf(&radio.io,-3);
  assert(ret==(failure?-ETIMEDOUT:0));assert(calls==(failure==1?1:2));
  if(!failure)assert(tsf[0]==before+(clock_us-ticks)-96);
  assert(regs[0xc440/4]==0x24000&&regs[0xc2a0/4]==0x80000000&&regs[0xc2a4/4]==0x80000000);
 }
 struct ieee80211_hw hw={&radio};struct ieee80211_vif vif;
 fail=0;tsf[0]=1000000;u64 before=tsf[0],ticks=clock_us;s64 requested=0;
 for(unsigned i=0;i<100;i++){
  s64 delta=i<50?-41:17;requested+=delta;count=0;
  rtl_ax_offset_tsf(&hw,&vif,delta);
  assert((s64)(tsf[0]-before-(clock_us-ticks))+radio.mesh_tsf_remainder==requested);
  assert(radio.mesh_tsf_remainder>-32&&radio.mesh_tsf_remainder<32);
 }
 assert(!stops);count=calls=0;fail=1;rtl_ax_offset_tsf(&hw,&vif,-64);assert(stops==1);
 count=0;rtl_ax_offset_tsf(&hw,&vif,0x40000LL*32);assert(stops==2&&!count);
 rtl_ax_offset_tsf(&hw,&vif,INT64_MAX);assert(stops==3&&!count);
 rtl_ax_offset_tsf(&hw,&vif,INT64_MIN);assert(stops==4&&!count);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='mesh-tsf-') as temp:
    p = Path(temp);(p/'test.c').write_text(code)
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-missing-field-initializers','-Wno-misleading-indentation','-shared','-fPIC',str(p/'test.c'),'-o',str(p/'test.so')],check=True)
    (p/'asan.c').write_text(code+'\nint main(void){return check();}\n')
    subprocess.run(['cc','-O2','-fsanitize=address,undefined',str(p/'asan.c'),'-o',str(p/'asan')],check=True)
    subprocess.run([str(p/'asan')],check=True)
    lib=ctypes.CDLL(str(p/'test.so'));assert lib.check()==0
    operations=[];registers={}
    def callback(name,args):
        if name=='read32':return registers.get(args[1],0)
        if name=='write32':
            operations.append(tuple(args[1:3]));registers[args[1]]=args[2];return 0
        if name=='log':return 0
        raise RuntimeError((name,args))
    module=(Path(sys.argv[1]) if len(sys.argv)>1 else root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko')
    oracle=ModuleOracle(module,['mac_tsf_sync'],callback)
    adapter,ops,io=0x6000000,0x6010000,0x6020000
    for addr in (adapter,ops,io):oracle.uc.mem_map(addr,0x10000)
    oracle.word(adapter,ops);oracle.word(ops,io)
    oracle.word(io+32,oracle.callback('read32'));oracle.word(io+40,oracle.callback('write32'))
    cases=0
    for src,dst in ((0,1),(1,0)):
        for offset in (-0x3ffff,-1024,-2,-1,0,1,2,1024,0x3ffff):
            for initial in (0,0x80000000,0x08880000):
                operations.clear();registers[0xc2a0+dst*4]=initial
                assert not oracle.call('mac_tsf_sync',adapter,src,dst,offset&0xffffffff,0)
                out=(ctypes.c_uint*4)();assert lib.native(src,dst,offset,initial,out)==2
                assert operations==[(out[0],out[1]),(out[2],out[3])]
                cases+=1
    print(f'Mesh TSF: {cases} stock ARM64 IO comparisons; wrap, ticking clocks, signed remainder, two-stage failure restore PASS')
