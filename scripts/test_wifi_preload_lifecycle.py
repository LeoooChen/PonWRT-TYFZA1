#!/usr/bin/env python3
"""Exercise native preload reservation, command failure and fallback publication."""
from pathlib import Path
import subprocess, tempfile
root=Path(__file__).resolve().parents[1]
ov=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
role=(ov/'role.h').read_text(); mon=(ov/'monitor.h').read_text()
tx=(ov/'tx.c').read_text()
runtime=(ov/'mac-runtime.h').read_text()
runtime=runtime[runtime.index('static inline void rtl_ax_preload_scc'):runtime.index('static inline void rtl_ax_hfc_scc')]
a=role.index('static inline void rtl_ax_station_preload');b=role.index('static inline ',a+1)
reserve=mon[mon.index('\t/* Reserve before issuing'):mon.index('\tradio->station_added++;')]
start=mon.index('\t/* Keep the reservation');release=mon[start:mon.index('\tfor (i = 0; i < RTL_AX_SECURITY_ENTRIES;',start)]
assert mon.index('ret = rtl_ax_tx_ni_retire_peer',mon.index('static int rtl_ax_station_release')) < start
assert mon.index('ret = rtl_ax_drop_station',mon.index('static int rtl_ax_station_release')) < start
source=r'''#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define RTL_AX_8852C 1
#define RTL_AX_H2C_DATA 1
static void rtl_ax_store32(u8*p,u32 v){memcpy(p,&v,4);}
static u32 get32(const u8*p){u32 v;memcpy(&v,p,4);return v;}
static bool test_bit(unsigned n,const uint64_t*b){return (b[n/64]>>(n%64))&1;}
static void __set_bit(unsigned n,uint64_t*b){b[n/64]|=UINT64_C(1)<<(n%64);}
static void __clear_bit(unsigned n,uint64_t*b){b[n/64]&=~(UINT64_C(1)<<(n%64));}
static unsigned bitmap_weight(const uint64_t*b,unsigned n){assert(n==128);return __builtin_popcountll(b[0])+__builtin_popcountll(b[1]);}
#define NL80211_IFTYPE_AP 1
#define NL80211_IFTYPE_STATION 2
#define NL80211_IFTYPE_MESH_POINT 7
struct ieee80211_vif {unsigned type;};
struct rtl_ax_ap {unsigned chip;uint64_t preload_peers[2];void *tx;};
struct rtl_ax_io {u32 control,size;};
static void rtl_ax_update32(const struct rtl_ax_io*io,unsigned reg,u32 mask,u32 value){
 struct rtl_ax_io *rw=(struct rtl_ax_io*)io;
 assert(reg==0x9f48||reg==0x9f4c);u32 *p=reg==0x9f48?&rw->control:&rw->size;
 *p=(*p&~mask)|(value&mask);
}
static bool published[128];static unsigned commands;static int fail;
static void rtl_ax_tx_peer_preload(void*tx,unsigned id,bool en){(void)tx;published[id]=en;}
static int rtl_ax_command(struct rtl_ax_ap*r,unsigned b,unsigned c,unsigned cl,unsigned fn,const u8*p,unsigned n,bool ack){
 (void)r;assert(b==1&&c==1&&cl==5&&fn==2&&n==68&&ack);
 for(unsigned i=0;i<68;i+=4)assert(get32(p+i)==(i==0?get32(p):i==12?get32(p+12):i==44?BIT(15):0));
 assert((get32(p)&~127U)==128);assert(get32(p+12)==0||get32(p+12)==BIT(15));commands++;return fail;
}
'''+runtime+role[a:b]+r'''
static int acquire_mode(struct rtl_ax_ap*radio,unsigned id,unsigned type){int ret=0;struct ieee80211_vif v={.type=type},*vif=&v;
'''+reserve+r'''
return 0;remove:return ret;}
static int acquire(struct rtl_ax_ap*r,unsigned id){return acquire_mode(r,id,NL80211_IFTYPE_AP);}
static int release(struct rtl_ax_ap*radio,unsigned macid){int ret;u8 dctl[68];
'''+release+r'''
return 0;}
int main(void){
 struct rtl_ax_io c={.control=0x80000a00},other_io=c;
 rtl_ax_preload_scc(&c,RTL_AX_8852C);rtl_ax_preload_scc(&other_io,2);
 assert(c.control==0x82080208&&c.size==0x134);
 assert(other_io.control==0x82080a00&&other_io.size==0x134);
 struct rtl_ax_ap r={.chip=RTL_AX_8852C};
 assert(!acquire_mode(&r,10,NL80211_IFTYPE_STATION));
 assert(!acquire_mode(&r,11,NL80211_IFTYPE_MESH_POINT));
 assert(!commands&&!published[10]&&!published[11]&&!bitmap_weight(r.preload_peers,128));
 unsigned ids[]={7,63,96,127};
 for(unsigned i=0;i<4;i++){assert(!acquire(&r,ids[i]));assert(published[ids[i]]);}
 assert(commands==4);assert(!acquire(&r,2));assert(!published[2]&&commands==4);
 fail=-110;assert(release(&r,63)==-110);assert(test_bit(63,r.preload_peers)&&published[63]);
 assert(!acquire(&r,2)&&!published[2]);
 fail=0;assert(!release(&r,63));assert(!test_bit(63,r.preload_peers)&&!published[63]);
 fail=-110;assert(acquire(&r,2)==-110);assert(test_bit(2,r.preload_peers)&&!published[2]);
 assert(!acquire(&r,3)&&!published[3]);
 fail=0;assert(!release(&r,2));assert(!acquire(&r,3)&&published[3]);
 unsigned before=commands;assert(!release(&r,2)&&commands==before);
 struct rtl_ax_ap other={.chip=2};assert(!acquire(&other,10)&&!published[10]&&commands==before);
 puts("AP-only reservation, 4 arbitrary MACIDs, capacity fallback, enable/disable timeout reservation, release and reuse passed");
}
'''
# The template flag must survive initial publication and later rate updates.
assert 'macid * 32 + 20;' in tx
assert tx.count('(tx->peer_preload[macid] ? RTL_AX_TX_AGG_PRELOAD : 0)') == 2
connected=role[role.index('static inline void rtl_ax_station_cmac_connected'):role.index('static inline void rtl_ax_station_preload')]
assert '0x000060c0' in connected  # word2 mask excludes PRELD bit15
with tempfile.TemporaryDirectory() as d:
 c=Path(d)/'test.c';exe=Path(d)/'test';c.write_text(source)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(c),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
