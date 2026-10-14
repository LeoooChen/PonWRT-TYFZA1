#!/usr/bin/env python3
"""Check ARM and PE TX ownership with the production driver's real predicates."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()


def function(name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
#define RTL_AX_TX_ENTRIES 256
#define IEEE80211_NUM_TIDS 16
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define for_each_set_bit(i,b,n) for(i=0;i<n;i++)if(b[i])
struct rtl_ax_tx {bool pe_active;int lock;unsigned int ni_rpq_producer;struct {void *memory;} pe;
 bool pending[256];struct {bool encrypted;unsigned int key_index,macid,tid;} slots[256];};
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put(void *p,u32 v){memcpy(p,&v,4);}
static void put_unaligned_le32(u32 v,void *p){put(p,v);}
#define dma_wmb() ((void)0)
'''
test = r'''
int main(void){
 unsigned char memory[0x18000]={};struct rtl_ax_tx tx={.pe_active=true,.pe={memory}};
 assert(!rtl_ax_tx_ni_buffered(&tx,7));
 put(memory+0x2060,1);put(memory+0x206c,7);put(memory+0xd000+7*32+20,42<<16);
 put(memory+0xc400,0x06000000);put(memory+0xc404,(7<<8)|(42<<17));
 assert(rtl_ax_tx_station_pending(&tx,7,16) && rtl_ax_tx_station_pending(&tx,7,0));
 assert(!rtl_ax_tx_station_pending(&tx,7,3) && !rtl_ax_tx_station_pending(&tx,8,16));
 assert(rtl_ax_tx_key_pending(&tx,42) && !rtl_ax_tx_key_pending(&tx,43));
 put(memory+0xc404,BIT(31)|(7<<8)|(42<<17));
 assert(!rtl_ax_tx_station_pending(&tx,7,16) && !rtl_ax_tx_key_pending(&tx,42));
 /* Every supported TID owns queued/submitted PE frames until its RPQ. */
 for(unsigned int tid=0;tid<8;tid++) {
  put(memory+0xc404,(7<<8)|(42<<17));put(memory+0x1100c,tid);
  assert(rtl_ax_tx_station_pending(&tx,7,tid));
  assert(rtl_ax_tx_station_pending(&tx,7,16) && rtl_ax_tx_key_pending(&tx,42));
  for(unsigned int other=0;other<8;other++)
   assert(rtl_ax_tx_station_pending(&tx,7,other)==(other==tid));
  put(memory+0xc404,BIT(31)|(7<<8)|(42<<17));
  assert(!rtl_ax_tx_station_pending(&tx,7,tid));
 }
 tx.pending[0]=true;tx.slots[0].macid=8;tx.slots[0].tid=3;
 tx.slots[0].encrypted=true;tx.slots[0].key_index=43;
 assert(rtl_ax_tx_station_pending(&tx,8,3) && rtl_ax_tx_key_pending(&tx,43));
 put(memory+0xc400,0x06000000);put(memory+0xc404,(1<<24)|(7<<8)|(42<<17));
 put(memory+0xc408,0x06000800);put(memory+0xc40c,BIT(15)|(7<<8)|(42<<17));
 tx.ni_rpq_producer=127;
 rtl_ax_tx_pe_complete(&tx,256,0);
 assert(tx.ni_rpq_producer==1 && get_unaligned_le32(memory+0x20b4)==1);
 assert(get_unaligned_le32(memory+0xc800+127*4)==0 && get_unaligned_le32(memory+0xc800)==1);
 assert(get_unaligned_le32(memory+0xc404)&BIT(31));
 assert(get_unaligned_le32(memory+0xc40c)&BIT(31));
 assert(!rtl_ax_tx_station_pending(&tx,7,16) && !rtl_ax_tx_key_pending(&tx,42));
 puts("PE lifetime: all eight TIDs retain BA/MACID/key ownership until RPQ; unrelated TIDs and completed frames do not block");
}
'''
with tempfile.TemporaryDirectory(prefix='pe-lifetime-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(common + ''.join(function(n) for n in
        ('rtl_ax_tx_ni_buffered', 'rtl_ax_tx_pe_pending', 'rtl_ax_tx_key_pending',
         'rtl_ax_tx_station_pending', 'rtl_ax_tx_pe_complete')) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
