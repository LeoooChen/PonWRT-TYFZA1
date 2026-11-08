#!/usr/bin/env python3
"""Execute ARM WP side-stamp publication and reject old PE capabilities."""
from pathlib import Path
import subprocess,tempfile
r=Path(__file__).resolve().parents[1];b=r/'port/overlay/target/linux/rtl9617c/files-6.18'
s=(b/'drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()
a=s.index('\tif (tx->pe_active) {\n\t\tret = rtl9617c_bridge_mcast_tx_stamp');z=s.index('\n\t/* Join the closed-generation barrier',a)
join=s[z:s.index('\n\tpage = tx->pages',z)]
unmap=s[s.index('unmap:',z):s.index('\n}',s.index('unmap:',z))]
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
#include "linux/rtl9617c-mcast-pe.h"
typedef uint32_t u32;
#define PE_PROBE_MAILBOX 0x2000
struct tx {bool pe_active;int lock;struct {void *memory;}pe;};
struct skb {u32 stamp;int error;};
static u32 get_unaligned_le32(void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static int rtl9617c_bridge_mcast_tx_stamp(struct skb *s,u32 *stamp){*stamp=s->stamp;return s->error;}
static bool accept=true,fttr_accept=true;static unsigned refs,unmaps;
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
static bool rtl9617c_fttr_tx_get(unsigned generation){(void)generation;return fttr_accept;}
static void rtl_ax_tx_unmap(struct tx *tx,void *slot){(void)tx;(void)slot;unmaps++;}
static void rtl9617c_bridge_mcast_tx_put(bool held){if(held){assert(unmaps&&refs);refs--;}}
static bool rtl9617c_bridge_mcast_tx_get(u32 stamp){if(!stamp)return true;if(!accept)return false;refs++;return true;}
static int capture(struct tx *tx,struct skb *skb,unsigned id,bool *held){
 struct {bool pe_mcast;unsigned fttr_generation;}slot={0};u32 mcast_stamp=0;int ret=0;unsigned flags=0;
'''+s[a:z]+join+r'''
 *held=slot.pe_mcast;return 0;
'''+unmap+r'''
}
int main(void){
 static unsigned char memory[0x80000];struct tx tx={.pe_active=true,.pe={memory}};bool held;unsigned checks=0;
 for(unsigned id=0;id<256;id++)for(unsigned mode=0;mode<6;mode++){
  memset(memory,0xa5,sizeof(memory));
  u32 cap=mode==2?0x4d435033:RTL_MC_PE_CAPABILITY;put_unaligned_le32(cap,memory+PE_PROBE_MAILBOX+RTL_MC_PE_CAP_MAILBOX*4);
  struct skb s={mode?17:0,mode==3?-ESTALE:0};held=false;refs=unmaps=0;accept=mode!=4;fttr_accept=mode!=5;
  int ret=capture(&tx,&s,id,&held);
  assert(ret==((mode>=3)?-ESTALE:mode==2?-EOPNOTSUPP:0));
  assert(held==(mode==1) && refs==(mode==1));
  for(unsigned i=0;i<256;i++)assert(get_unaligned_le32(memory+RTL_MC_PE_CPU_STAMPS+i*4)==
   (i==id&&(mode<2||mode==5)?s.stamp:0xa5a5a5a5));
  assert(get_unaligned_le32(memory+RTL_MC_PE_CPU_STAMPS-4)==0xa5a5a5a5);
  assert(get_unaligned_le32(memory+RTL_MC_PE_CPU_STAMPS+1024)==0xa5a5a5a5);
  /* The complete128-byte request is owned separately, including all ten addresses. */
  for(unsigned i=0;i<256*128;i++)assert(memory[0x4000+i]==0xa5);
  checks++;
 }
 puts("ARM CPU multicast:1536 stamp/unrelated/stale/old-capability/admission-race/rollback vectors; all256 WP slots and request boundaries passed");
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(b/'include'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
