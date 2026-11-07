#!/usr/bin/env python3
"""Execute production ARM/PE PN ownership under concurrent software senders."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
overlay = root / 'port/overlay/target/linux/rtl9617c/files-6.18'
header = (overlay / 'include/linux/rtl9617c-key-pn.h').read_text()
header = header.replace('#include <linux/atomic.h>', '')
# C11 atomic words model the coherent aligned stores with the full barriers
# used by both actors; this avoids undefined host C races in the fixture.
header = header.replace('unsigned int generation, arm, pe, turn, published;',
                        '_Atomic unsigned int generation, arm, pe, turn, published;')
header = header.replace('unsigned int low[2], high[2], reserved;',
                        '_Atomic unsigned int low[2], high[2], reserved;')
source = (overlay / 'net/core/rtl9617c-key-pn.c').read_text()
source = re.sub(r'^#include.*\n', '', source, flags=re.M)
peer = (root / 'port/pe/rtl9617c-radio-key-pn.h').read_text()
peer = re.sub(r'^#include.*\n', '', peer, flags=re.M)

def function(text, name):
    start = re.search(r'^[^\n]*\b'+name+r'\([^;]*?\)\s*\{',text,re.M).start()
    end = text.index('{',start)+1;depth=1
    while depth:
        depth += (text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]+'\n'

code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <stdatomic.h>
#include <sched.h>
#include <errno.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
typedef _Atomic uint64_t atomic64_t;
#define __KERNEL__
#define BIT(n) (1U<<(n))
#define DEFINE_SPINLOCK(n) pthread_mutex_t n=PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(p,f) ((f)=0,assert(!pthread_mutex_lock(p)))
#define spin_unlock_irqrestore(p,f) ((void)(f),assert(!pthread_mutex_unlock(p)))
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define mb() atomic_thread_fence(memory_order_seq_cst)
#define dma_rmb() mb()
#define dma_wmb() mb()
#define sync() mb()
#define udelay(n) ((void)(n),sched_yield())
#define atomic64_set(p,v) atomic_store(p,v)
#define atomic64_read(p) atomic_load(p)
#define atomic64_inc_return(p) (atomic_fetch_add(p,1)+1)
#define EXPORT_SYMBOL_GPL(n)
''' + header + source + peer
kernel=next((root/'sources/openwrt/build_dir').glob('target-*/linux-rtl9617c_generic/linux-6.18.55'))
linux=(kernel/'net/mac80211/wpa.c').read_text()
code += r'''
#define CONFIG_ARCH_RTL9617C 1
#define IEEE80211_KEY_FLAG_PAIRWISE BIT(0)
#define IEEE80211_KEY_FLAG_GENERATE_IV BIT(1)
#define IEEE80211_KEY_FLAG_PUT_IV_SPACE BIT(2)
#define IEEE80211_KEY_FLAG_GENERATE_IV_MGMT BIT(3)
#define IEEE80211_KEY_FLAG_SPP_AMSDU BIT(4)
#define IEEE80211_CCMP_HDR_LEN 8
#define CCM_AAD_LEN 32
#define AES_BLOCK_SIZE 16
#define WARN_ON(x) (x)
struct ieee80211_key_conf {atomic64_t tx_pn;struct rtl_key_pn rtl9617c_pn;unsigned flags,keyidx,hw_key_idx;};
struct ieee80211_key {struct ieee80211_key_conf conf;struct {struct {void *tfm;}ccmp;}u;};
struct ieee80211_hdr {uint16_t frame_control;};
struct ieee80211_tx_info {struct {struct ieee80211_key_conf *hw_key;}control;};
struct sk_buff {u8 storage[128],*data;unsigned len;struct ieee80211_tx_info info;};
struct ieee80211_tx_data {struct ieee80211_key *key;};
#define IEEE80211_SKB_CB(s) (&(s)->info)
static unsigned ieee80211_hdrlen(unsigned fc){(void)fc;return 24;}
static bool ieee80211_is_mgmt(unsigned fc){return !(fc&12);}
static int skb_tailroom(struct sk_buff *s){return sizeof(s->storage)-(s->data-s->storage)-s->len;}
static unsigned skb_headroom(struct sk_buff *s){return s->data-s->storage;}
static u8 *skb_push(struct sk_buff *s,unsigned n){s->data-=n;s->len+=n;return s->data;}
static void ccmp_special_blocks(struct sk_buff *s,u8 *pn,u8 *b,u8 *aad,unsigned flags){(void)s;(void)pn;(void)b;(void)aad;(void)flags;}
static int ieee80211_aes_ccm_encrypt(void *tfm,u8 *b,u8 *aad,u8 *pos,unsigned len,u8 *mic){
 (void)tfm;(void)b;(void)aad;(void)pos;(void)len;(void)mic;return 0;}
static void *skb_put(struct sk_buff *s,unsigned n){void *p=s->data+s->len;s->len+=n;return p;}
'''
code=code.replace('#include <stdint.h>', '#include <stdint.h>\n#include <stdbool.h>')
code += function(linux,'ccmp_pn2hdr')+function(linux,'ccmp_encrypt_skb')
radio=overlay/'drivers/net/wireless/realtek/rtl_ax_ap'
code += r'''
struct rtl_ax_tx {pthread_mutex_t lock;bool pe_active;struct {void *memory;}pe;unsigned key_pn_generation;};
static void rtl_ax_tx_mcast_revoke(struct rtl_ax_tx *tx){(void)tx;}
struct rtl_ax_ap {struct rtl_ax_tx *tx;struct {unsigned valid,cam[7];}security_roles[128];pthread_mutex_t pn_lock;unsigned io;};
struct ieee80211_hw {struct rtl_ax_ap *radio;};
struct ieee80211_key_seq {struct {u8 pn[6];}ccmp;};
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static u64 rtl_ax_security_read_pn(unsigned *io,unsigned macid){(void)io;(void)macid;return 0x123456abcdefULL;}
'''
code += function((radio/'tx.c').read_text(),'rtl_ax_tx_key_pn_bind')
code += function((radio/'tx.c').read_text(),'rtl_ax_tx_key_pn_unbind')
code += function((radio/'monitor.h').read_text(),'rtl_ax_get_key_seq')
code += r'''
static void software_ccmp(void){
 static u8 memory[0x20000] __attribute__((aligned(64)));
 struct rtl_ax_tx tx={.lock=PTHREAD_MUTEX_INITIALIZER,.pe_active=true,.pe={memory}};
 struct ieee80211_key key={.conf={.keyidx=2,.hw_key_idx=63,.tx_pn=0xffffffffULL}};
 struct ieee80211_tx_data t={.key=&key};
 struct rtl_ax_ap radio={.tx=&tx,.pn_lock=PTHREAD_MUTEX_INITIALIZER};struct ieee80211_hw hw={.radio=&radio};
 assert(!rtl_ax_tx_key_pn_bind(&tx,&key.conf));
 assert(rtl9617c_key_pn_set(&key.conf.rtl9617c_pn,&key.conf.tx_pn,1)==-EBUSY);
 u32 request[32]={};request[4]=BIT(15);request[5]=63<<16;request[RTL_KEY_PN_REQUEST]=1;
 for(unsigned i=0;i<32;i++) {
  assert(pe_key_pn(memory,request)==1);
  struct sk_buff s={.len=32};s.data=s.storage+32; /* Software-encrypted management frame. */
  assert(!ccmp_encrypt_skb(&t,&s,8));u8 *iv=s.data+24;
  u64 expected=0xffffffffULL+i*2+2;
  assert(iv[0]==(u8)expected&&iv[1]==(u8)(expected>>8)&&iv[2]==0&&iv[3]==0xa0);
  for(unsigned b=2;b<6;b++)assert(iv[b+2]==(u8)(expected>>(b*8)));
  struct ieee80211_key_seq seq;rtl_ax_get_key_seq(&hw,&key.conf,&seq);
  /* Current ieee80211_get_key directly copies these bytes to nl80211,
   * matching its software fallback (LSB first). */
  for(unsigned b=0;b<6;b++)assert(seq.ccmp.pn[b]==(u8)(expected>>(b*8)));
 }
 assert(!rtl_ax_tx_key_pn_unbind(&tx,&key.conf)&&key.conf.tx_pn==0x10000003fULL);
 assert(!rtl9617c_key_pn_set(&key.conf.rtl9617c_pn,&key.conf.tx_pn,99)&&key.conf.tx_pn==99);
 tx.key_pn_generation=~0U;assert(rtl_ax_tx_key_pn_bind(&tx,&key.conf)==-EOVERFLOW);
 struct rtl_key_pn_shared *s=(void *)(memory+RTL_KEY_PN_BASE+63*40);
 tx.key_pn_generation=9;assert(!rtl_ax_tx_key_pn_bind(&tx,&key.conf));s->pe=1;s->turn=1;
 assert(rtl_ax_tx_key_pn_unbind(&tx,&key.conf)==-ETIMEDOUT);
 struct sk_buff frame={.len=32};frame.data=frame.storage+32;
 assert(ccmp_encrypt_skb(&t,&frame,8)==-1);s->pe=0;
}
#define COUNT 20000
static u8 memory[0x20000] __attribute__((aligned(64)));
static struct rtl_key_pn binding;
static atomic64_t fallback;
static _Atomic unsigned seen[3*COUNT+1],success,timeouts;
static void *reader(void *unused){
 (void)unused;u64 last=0;
 while(success<3*COUNT){u64 current=rtl9617c_key_pn_read(&binding,&fallback);assert(current>=last&&current<=3*COUNT);last=current;}
 return NULL;
}
static void *sender(void *argument){
 unsigned who=(uintptr_t)argument;u32 request[32]={};u64 pn;
 request[4]=BIT(15);request[5]=127U<<16;request[RTL_KEY_PN_REQUEST]=1;
 for(unsigned n=0;n<COUNT;) {
  if(who==2){
   unsigned ret=pe_key_pn(memory,request);assert(ret!=2);if(!ret){sched_yield();continue;}
   pn=(u64)request[7]<<32|request[6];
  }else{
   int ret=rtl9617c_key_pn_next(&binding,&fallback,&pn);
   if(ret==-ETIMEDOUT){timeouts++;continue;}assert(!ret);
  }
  assert(pn&&pn<=3*COUNT);assert(!atomic_fetch_add(&seen[pn],1));success++;n++;
 }
 return NULL;
}
int main(void){
 software_ccmp();
 struct rtl_key_pn_shared *s=(void *)(memory+RTL_KEY_PN_BASE+127*sizeof(*s));
 assert(!rtl9617c_key_pn_bind(&binding,s,1,&fallback));
 assert(rtl9617c_key_pn_token(&binding)==1);
 pthread_t threads[3],observer;assert(!pthread_create(&observer,NULL,reader,NULL));
 for(unsigned i=0;i<3;i++)assert(!pthread_create(threads+i,NULL,sender,(void *)(uintptr_t)i));
 for(unsigned i=0;i<3;i++)assert(!pthread_join(threads[i],NULL));
 assert(!pthread_join(observer,NULL));
 assert(success==3*COUNT&&rtl9617c_key_pn_read(&binding,&fallback)==3*COUNT);
 assert(!rtl9617c_key_pn_unbind(&binding,&fallback)&&fallback==3*COUNT&&!s->generation);
 u64 pn;assert(!rtl9617c_key_pn_next(&binding,&fallback,&pn)&&pn==3*COUNT+1);
 for(unsigned cam=0;cam<128;cam++)for(unsigned v=0;v<5;v++) {
  const u64 starts[]={0,65535,0xffffffffULL,0x123456abcdefULL,0xffffffffffffULL};
  s=(void *)(memory+RTL_KEY_PN_BASE+cam*sizeof(*s));memset(s,0,sizeof(*s));binding=(struct rtl_key_pn){0};
  fallback=starts[v];assert(!rtl9617c_key_pn_bind(&binding,s,cam+2,&fallback));
  u32 request[32]={};request[4]=BIT(15);request[5]=cam<<16;request[RTL_KEY_PN_REQUEST]=cam+2;
  assert(pe_key_pn(memory,request)==1);
  u64 next=(starts[v]+1)&0xffffffffffffULL;
  assert(((u64)request[7]<<32|request[6])==next&&rtl9617c_key_pn_read(&binding,&fallback)==next);
  assert(!rtl9617c_key_pn_unbind(&binding,&fallback)&&fallback==next);
  assert(pe_key_pn(memory,request)==2);
  /* Same CAM gets a different identity; old requests cannot consume PN. */
  fallback=57;assert(!rtl9617c_key_pn_bind(&binding,s,cam+130,&fallback));
  assert(pe_key_pn(memory,request)==2&&rtl9617c_key_pn_read(&binding,&fallback)==57);
  assert(!rtl9617c_key_pn_unbind(&binding,&fallback));
 }
 s=(void *)(memory+RTL_KEY_PN_BASE);binding=(struct rtl_key_pn){0};
 fallback=41;assert(!rtl9617c_key_pn_bind(&binding,s,999,&fallback));
 s->pe=1;s->turn=1;
 assert(rtl9617c_key_pn_next(&binding,&fallback,&pn)==-ETIMEDOUT&&!s->arm);
 assert(rtl9617c_key_pn_read(&binding,&fallback)==41);
 assert(rtl9617c_key_pn_unbind(&binding,&fallback)==-ETIMEDOUT&&!binding.shared&&!s->generation);
 assert(rtl9617c_key_pn_next(&binding,&fallback,&pn)==-EKEYREVOKED);
 assert(!rtl9617c_key_pn_token(&binding));s->pe=0;
 puts("Shared GTK PN: 60000 concurrent ARM/PE allocations and stable reads,640 carry/wrap/CAM generations,32 actual software-CCMP/PE alternations, radio bind/read/unbind, debugfs refusal and poisoned software fallback passed");
}
'''
with tempfile.TemporaryDirectory(prefix='shared-key-pn-') as tmp:
    tmp = Path(tmp)
    (tmp/'pn.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-pthread',
                    '-fsanitize=address,undefined',str(tmp/'pn.c'),'-o',str(tmp/'pn')],check=True)
    subprocess.run([str(tmp/'pn')],check=True)
