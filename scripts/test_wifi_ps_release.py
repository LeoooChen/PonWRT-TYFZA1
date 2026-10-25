#!/usr/bin/env python3
"""Run the production PS release with NI and ARM frames on different TIDs."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()


def function(name):
    start = re.search(r'(?:void|bool) ' + name + r'\([^;]*?\)\s*\{', source).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_NI_PS_VERSION 0x19e00
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define dma_wmb() ((void)0)
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
struct ieee80211_sta {bool service_period;u16 buffered;unsigned eosp;};
struct rtl_ax_tx {bool pe_active;int lock;unsigned ps_releases;struct {void *memory;}pe;struct {bool ps_issued[128];}ni;};
static void ieee80211_sta_eosp(struct ieee80211_sta *sta){sta->service_period=false;sta->eosp++;}
static u32 ieee80211_sta_ps_generation(struct ieee80211_sta *sta){(void)sta;return 1;}
static bool ieee80211_sta_eosp_generation(struct ieee80211_sta *sta,u32 generation){assert(generation==1);ieee80211_sta_eosp(sta);return true;}
'''
cases = r'''
int main(void){
 u8 memory[0x20000]={};struct rtl_ax_tx tx={.pe_active=true,.pe={memory}};
 struct ieee80211_sta sta={.service_period=true,.buffered=BIT(0)|BIT(6)};
 u32 *ps=(void*)(memory+0xe800+2*16);
 put_unaligned_le32(1,memory+PE_PROBE_MAILBOX+96);
 put_unaligned_le32(1,memory+0x11800+2*4);
 put_unaligned_le32(1,memory+0x13000+(2*8+0)*4); /* NI queued only on TID0. */
 put_unaligned_le32(1,memory+0x18000+(2*8+0)*4);
 ps[0]=1;
 /* mac80211 announces NI TID0 and an ARM in-flight TID6. PS-Poll picks
  * TID6. The absent NI queue cannot generate its completion/EOSP. */
 rtl_ax_tx_ps_release(&tx,&sta,2,BIT(6),false,true,1);
 assert(!sta.service_period && sta.eosp==1 && !ps[1] && !tx.ps_releases);
 assert(sta.buffered==(BIT(0)|BIT(6)));
 /* A U-APSD grant containing no queued NI TID must end as well. */
 sta.service_period=true;
 rtl_ax_tx_ps_release(&tx,&sta,2,BIT(4)|BIT(6),true,true,1);
 assert(!sta.service_period && sta.eosp==2 && !ps[1] && !tx.ps_releases);
 assert(sta.buffered==(BIT(0)|BIT(6)));
 /* A grant including a real queued TID remains open until its RPQ. */
 sta.service_period=true;
 rtl_ax_tx_ps_release(&tx,&sta,2,BIT(0)|BIT(6),true,true,1);
 assert(sta.service_period && sta.eosp==2 && ps[1]==1 && tx.ps_releases==1);
 assert(ps[3]==(1|2|((BIT(0)|BIT(6))<<16)));
 assert(sta.buffered==(BIT(0)|BIT(6)));
 /* Pending ownership alone does not make an in-flight frame sendable. */
 put_unaligned_le32(0,memory+0x13000+(2*8+0)*4);
 put_unaligned_le32(0,memory+0x11800+2*4);
 rtl_ax_tx_ps_release(&tx,&sta,2,BIT(0),false,false,1);
 assert(!sta.service_period && sta.eosp==3 && ps[1]==1 && tx.ps_releases==1);
 assert(sta.buffered==(BIT(0)|BIT(6)));
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-ps-release-') as directory:
    c, exe = Path(directory) / 'release.c', Path(directory) / 'release'
    c.write_text(common + function('rtl_ax_tx_ni_buffered') + function('rtl_ax_tx_ps_release') + cases)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PS release: empty requested NI TIDs end SP; eligible TIDs grant once; in-flight TIM remains set')
