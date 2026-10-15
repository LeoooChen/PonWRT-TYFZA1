#!/usr/bin/env python3
"""Execute production EPON OAM open/TX across all six selected PON modes."""
from pathlib import Path
import re, subprocess, tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-oam.c').read_text()


def function(name):
    start = re.search(r'^static [^\n]+\b' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-pon-oam-format.h"
typedef uint8_t u8;typedef int netdev_tx_t;
#define BIT(n) (1U<<(n))
#define READ_ONCE(n) (n)
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
struct rtl9617c_pon {enum rtl_pon_mode mode;void *ni_dev;};
struct rtl_oam_port {struct rtl9617c_pon *pon;unsigned int llid;bool control;};
struct net_device {struct rtl_oam_port port;bool carrier;unsigned int starts;struct {unsigned int tx_dropped;} stats;};
struct sk_buff {unsigned int len;u8 data[60];};
struct rtl9617c_ni_frame {unsigned int header_high,header_low;const u8 *prefix;unsigned int prefix_length;};
#define netdev_priv(p) (&(p)->port)
static unsigned int opened,configured,closed,transmitted,freed;
static bool busy;
static int rtl9617c_ni_open(void *p){(void)p;opened++;return 0;}
static int rtl9617c_ni_oam_configure(void *p){(void)p;configured++;return 0;}
static void rtl9617c_ni_close(void *p,struct net_device *d){(void)p;(void)d;closed++;}
static void netif_start_queue(struct net_device *p){p->starts++;}
#define netif_carrier_ok(p) ((p)->carrier)
static int skb_copy_bits(struct sk_buff *p,unsigned int offset,void *out,unsigned int size){assert(offset+size<=p->len);memcpy(out,p->data+offset,size);return 0;}
static void dev_kfree_skb_any(struct sk_buff *p){freed++;free(p);}
static int rtl9617c_ni_transmit(void *p,struct net_device *d,struct sk_buff *skb,const struct rtl9617c_ni_frame *f){
 (void)p;assert(f->header_high==BIT(13) && f->header_low==0x2000227f && f->prefix_length==16 && f->prefix[5]==d->port.llid);
 if(busy)return NETDEV_TX_BUSY;
 transmitted++;dev_kfree_skb_any(skb);return 0;
}
static struct sk_buff *packet(void){struct sk_buff *p=calloc(1,sizeof(*p));assert(p);p->len=60;p->data[12]=0x88;p->data[13]=9;p->data[14]=3;return p;}
'''
cases = r'''
int main(void){
 for(unsigned int mode=0;mode<RTL_PON_MODE_COUNT;mode++){
  struct rtl9617c_pon pon={.mode=mode};struct net_device dev={.port={&pon,7,true},.carrier=true};
  unsigned int before_open=opened,before_config=configured,before_tx=transmitted,before_free=freed;
  int result=rtl_oam_open(&dev);
  assert(result==(rtl_pon_mode_is_epon(mode)?0:-EOPNOTSUPP));
  assert(opened-before_open==(unsigned int)rtl_pon_mode_is_epon(mode) && configured-before_config==(unsigned int)rtl_pon_mode_is_epon(mode));
  assert(rtl_oam_transmit(packet(),&dev)==NETDEV_TX_OK);
  assert(transmitted-before_tx==(unsigned int)rtl_pon_mode_is_epon(mode) && freed==before_free+1);
  before_tx=transmitted;dev.carrier=false;
  assert(rtl_oam_transmit(packet(),&dev)==NETDEV_TX_OK && transmitted==before_tx);
  if(rtl_pon_mode_is_epon(mode)){
   dev.carrier=true;busy=true;struct sk_buff *p=packet();before_free=freed;
   assert(rtl_oam_transmit(p,&dev)==NETDEV_TX_BUSY && freed==before_free);busy=false;
   /* Model a mode switch while a previous BUSY packet awaits retry. */
   pon.mode=RTL_PON_GPON;
   assert(rtl_oam_transmit(p,&dev)==NETDEV_TX_OK && freed==before_free+1 && transmitted==before_tx);
  }
  dev.port.control=false;assert(!rtl_oam_open(&dev)); /* Common data interface still opens in ITU. */
 }
 assert(!closed);
 puts("Production OAM open/TX:6 protocol modes, stopped carriers, BUSY retry after family change; ITU data open retained, packet ownership correct");
}
'''
with tempfile.TemporaryDirectory(prefix='oam-family-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(model + ''.join(function(n) for n in ('rtl_oam_pon', 'rtl_oam_open', 'rtl_oam_transmit')) + cases)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(drivers), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
