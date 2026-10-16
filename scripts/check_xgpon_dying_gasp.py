#!/usr/bin/env python3
"""Exercise the actual precomputed Dying Gasp lifecycle against the stock PDU."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from elftools.elf.elffile import ELFFile

drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-omci.c').read_text()


def function(name):
    start = re.search(r'^(?:static )?(?:void|irqreturn_t) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
with module.open('rb') as stream:
    elf = ELFFile(stream)
    symbol = elf.get_section_by_name('.symtab').get_symbol_by_name('aal_gpon_dg_pdu')[0]
    offset = symbol['st_value']
    body = elf.get_section(symbol['st_shndx']).data()[offset:offset + 44]

model = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-pon-omci-format.h"
typedef uint8_t u8;typedef uint32_t u32;typedef int netdev_tx_t;typedef int irqreturn_t;
#define BIT(n) (1U<<(n))
#define GFP_KERNEL 0
#define NETDEV_TX_BUSY 1
#define IRQ_HANDLED 1
#define lockdep_assert_held(p) assert(*(p))
#define dev_err(...) ((void)0)
#define spin_lock_irqsave(p,f) do{assert(!*(p));*(p)=1;(f)=0;}while(0)
#define spin_unlock_irqrestore(p,f) do{assert(*(p));*(p)=0;(void)(f);}while(0)
struct net_device {bool running;};
struct sk_buff {u8 data[48];unsigned int len;struct net_device *dev;};
struct rtl9617c_ni_frame {u32 header_high,header_low;const u8 *prefix;unsigned int prefix_length;bool raw_payload;};
struct rtl9617c_pon {
 int lock,dying_gasp_lock,dying_gasp_irq,dying_gasp_error;
 bool dying_gasp_registered,dying_gasp_sent,xgpon_omcc_ready,xgpon_key_ready;
 unsigned char *mac;void *dying_gasp,*ni_dev,*dev;
 struct net_device *omci_device;struct sk_buff *dying_gasp_packet;
 u8 dying_gasp_prefix[16];unsigned long long dying_gasp_tx;
};
static u8 expected[44],sent[48];
static unsigned int allocations,frees,mics,transmits,synchronizes;
static int mic_error,tx_busy;static bool in_irq;
static u32 readl(const void *p){return *(const u32*)p;}
static void writel(u32 v,void *p){*(u32*)p=v;}
static bool netif_running(struct net_device *d){return d->running;}
static void synchronize_irq(int irq){assert(irq==24 && !in_irq);synchronizes++;}
static struct sk_buff *alloc_skb(unsigned int n,int flags){assert(n==48 && flags==GFP_KERNEL && !in_irq);allocations++;return calloc(1,sizeof(struct sk_buff));}
static void *skb_put(struct sk_buff *p,unsigned int n){assert(p->len+n<=48);void *v=p->data+p->len;p->len+=n;return v;}
static void skb_put_data(struct sk_buff *p,const void *s,unsigned int n){memcpy(skb_put(p,n),s,n);}
static void dev_kfree_skb_any(struct sk_buff *p){if(p){frees++;free(p);}}
static int rtl_omci_mic(struct rtl9617c_pon *p,u8 direction,const u8 *data,unsigned int bytes,u8 *result){
 assert(p->lock && !in_irq && p->xgpon_key_ready && direction==2 && bytes==44);
 assert(!memcmp(data,expected,44));mics++;memcpy(result,"MIC!",4);return mic_error;
}
static netdev_tx_t rtl9617c_ni_transmit(void *ni,struct net_device *dev,struct sk_buff *p,const struct rtl9617c_ni_frame *f){
 (void)ni;assert(in_irq && dev->running && p->len==48 && p->dev==dev);
 assert(f->header_high==BIT(13) && f->header_low==(7|(15<<3)|(17<<9)|BIT(29)|(64<<15)));
 u8 prefix[16];rtl_omci_prefix(prefix,48,0);
 assert(f->raw_payload && f->prefix_length==16 && !memcmp(prefix,f->prefix,16));
 assert(!memcmp(p->data,expected,44) && !memcmp(p->data+44,"MIC!",4));
 memcpy(sent,p->data,48);transmits++;
 if(tx_busy)return NETDEV_TX_BUSY;
 dev_kfree_skb_any(p);return 0;
}
'''
cases = r'''
static void trigger(struct rtl9617c_pon *p){in_irq=true;assert(rtl_pon_dying_gasp_irq(24,p)==IRQ_HANDLED);in_irq=false;}
int main(void){
 u32 regs[0xc000/4]={},gasp[4]={};struct net_device dev={.running=true};
 struct rtl9617c_pon pon={.lock=1,.dying_gasp_registered=true,.dying_gasp_irq=24,
  .dying_gasp=gasp,.mac=(void*)regs,.omci_device=&dev,.xgpon_omcc_ready=true,.xgpon_key_ready=true};
 regs[0x4d30/4]=4;rtl_pon_dying_gasp_prepare(&pon);assert(!allocations);
 regs[0x4d30/4]=3;dev.running=false;rtl_pon_dying_gasp_prepare(&pon);assert(!allocations);dev.running=true;
 rtl_pon_dying_gasp_prepare(&pon);assert(allocations==1 && mics==1 && gasp[0]==0x105 && gasp[1]==1);
 rtl_pon_dying_gasp_prepare(&pon);assert(allocations==1 && mics==1);
 trigger(&pon);assert(transmits==1 && frees==1 && pon.dying_gasp_tx==1 && !gasp[1] && pon.dying_gasp_sent);
 trigger(&pon);rtl_pon_dying_gasp_prepare(&pon);assert(transmits==1 && allocations==1);
 rtl_pon_dying_gasp_stop(&pon);assert(synchronizes==1 && !pon.dying_gasp_sent);
 rtl_pon_dying_gasp_prepare(&pon);rtl_pon_dying_gasp_stop(&pon);
 trigger(&pon);assert(transmits==1 && frees==2 && !pon.dying_gasp_packet);
 rtl_pon_dying_gasp_stop(&pon);tx_busy=1;rtl_pon_dying_gasp_prepare(&pon);trigger(&pon);
 assert(transmits==2 && frees==3 && pon.dying_gasp_tx==1 && pon.dying_gasp_error==-EBUSY && !gasp[1]);
 rtl_pon_dying_gasp_stop(&pon);mic_error=-EIO;rtl_pon_dying_gasp_prepare(&pon);
 assert(pon.dying_gasp_error==-EIO && !pon.dying_gasp_packet && !gasp[1] && allocations==frees);
 puts("Dying Gasp: stock44-byte PDU, direction2 MIC before IRQ, one-shot TX, stale-session cancellation, BUSY ownership and MIC failure passed");
}
'''
data = model + ''.join(function(n) for n in ('rtl_pon_dying_gasp_stop', 'rtl_pon_dying_gasp_prepare',
                                          'rtl_pon_dying_gasp_irq')) + cases
data = data.replace('static u8 expected[44],sent[48];',
                    'static u8 expected[44]={' + ','.join(str(n) for n in body) + '},sent[48];')
with tempfile.TemporaryDirectory(prefix='xgpon-dying-gasp-', dir=root / 'artifacts') as temp:
    c, binary = Path(temp) / 'check.c', Path(temp) / 'check'
    c.write_text(data)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(drivers), str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
