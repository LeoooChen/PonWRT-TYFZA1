#!/usr/bin/env python3
"""Run actual per-LLID PON TX/RX/carrier and resize functions against host models."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
text = (drivers / 'rtl9617c-pon-oam.c').read_text()


def function(name):
    start = re.search(r'^.*\b' + re.escape(name) + r'\([^;]*?\)\s*\{', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-pon-oam-format.h"
#include "rtl9617c-pon-data.h"
#define __rcu
#define BIT(n) (1U<<(n))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define ETH_HLEN 14
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define container_of(p,t,f) ((t *)((char *)(p)-offsetof(t,f)))
typedef unsigned int u32;typedef uint8_t u8;typedef int netdev_tx_t;
struct sk_buff {unsigned int len,priority,protocol;u8 *data,*allocation;};
struct napi_struct {};
struct rtl9617c_pon;
struct rtl_oam_port {struct rtl9617c_pon *pon;unsigned int llid;bool control;};
struct net_device {struct rtl_oam_port port;bool running,carrier,stopped,registered;struct {unsigned int tx_dropped,rx_dropped,rx_packets,rx_bytes;} stats;};
struct rtl9617c_ni_endpoint {struct net_device *netdev;};
struct rtl9617c_ni_frame {u32 header_high,header_low;const u8 *prefix;unsigned int prefix_length;};
struct rtl9617c_pon {
    struct net_device *oam_devices[32],*data_devices[32];
    struct rtl9617c_ni_endpoint oam_endpoint;
    unsigned int llid_count,last_oam_llid,last_oam_source,registered;
    enum rtl_pon_mode mode;
    bool traffic_enabled,mpcp_enabled,pdc_configured,puc_configured;
    void *mac,*ni_dev;
};
#define netdev_priv(dev) (&(dev)->port)
#define rcu_dereference(p) (p)
#define rcu_assign_pointer(p,v) ((p)=(v))
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
static bool rtnl;
static unsigned int allocation_number,fail_allocation,live,received,transmitted,woken,retired;
static struct net_device *last_received;
static struct rtl9617c_ni_frame last_frame;
static u8 last_bytes[2048];static unsigned int last_length;
static int tx_busy;
static void rtnl_lock(void) {assert(!rtnl);rtnl=true;}
static void rtnl_unlock(void) {assert(rtnl);rtnl=false;}
#define rtnl_dereference(p) (assert(rtnl),(p))
static void synchronize_net(void) {assert(rtnl);}
static struct net_device *rtl_oam_allocate(struct rtl9617c_pon *pon,unsigned int index,bool control) {
    assert(rtnl);if(++allocation_number==fail_allocation)return NULL;
    struct net_device *dev=calloc(1,sizeof(*dev));assert(dev);live++;
    dev->port.pon=pon;dev->port.llid=index;dev->port.control=control;return dev;
}
static int register_netdevice(struct net_device *dev) {assert(rtnl&&!dev->registered);dev->registered=true;return 0;}
static void unregister_netdevice(struct net_device *dev) {assert(rtnl&&dev->registered);dev->registered=false;}
static void free_netdev(struct net_device *dev) {assert(!dev->registered);live--;free(dev);}
static u32 readl(const void *address) {u32 value;memcpy(&value,address,4);return value;}
static int netif_running(struct net_device *dev) {return dev->running;}
static int netif_carrier_ok(struct net_device *dev) {return dev->carrier;}
static int netif_queue_stopped(struct net_device *dev) {return dev->stopped;}
static void netif_wake_queue(struct net_device *dev) {dev->stopped=false;woken++;}
static void netif_carrier_on(struct net_device *dev) {dev->carrier=true;}
static void netif_carrier_off(struct net_device *dev) {dev->carrier=false;}
static void dev_kfree_skb_any(struct sk_buff *skb) {free(skb->allocation);free(skb);retired++;}
static unsigned int eth_type_trans(struct sk_buff *skb,struct net_device *dev) {(void)dev;return skb->data[12]*256+skb->data[13];}
static void skb_pull(struct sk_buff *skb,unsigned int length) {assert(skb->len>=length);skb->len-=length;skb->data+=length;}
static void napi_gro_receive(struct napi_struct *napi,struct sk_buff *skb) {
    (void)napi;received++;last_length=skb->len;memcpy(last_bytes,skb->data,skb->len);dev_kfree_skb_any(skb);
}
static netdev_tx_t rtl9617c_ni_transmit(void *engine,struct net_device *owner,struct sk_buff *skb,const struct rtl9617c_ni_frame *frame) {
    (void)engine;(void)owner;last_frame=*frame;
    if(tx_busy)return NETDEV_TX_BUSY;
    transmitted++;last_length=skb->len;memcpy(last_bytes,skb->data,skb->len);dev_kfree_skb_any(skb);return NETDEV_TX_OK;
}
static struct sk_buff *packet(const u8 *bytes,unsigned int length) {
    struct sk_buff *skb=calloc(1,sizeof(*skb));assert(skb);
    skb->allocation=malloc(length);assert(skb->allocation);memcpy(skb->allocation,bytes,length);skb->data=skb->allocation;skb->len=length;return skb;
}
'''

test = r'''
int main(void) {
    struct rtl9617c_pon pon={.llid_count=32,.mode=RTL_PON_EPON_D10G};
    unsigned int status[8]={};u8 ethernet[128],control[128];struct napi_struct napi;
    status[4]=2;pon.mac=status;
    assert(rtl_pon_oam_resize(&pon,32)==0 && live==64);
    pon.oam_endpoint.netdev=pon.oam_devices[0];
    pon.traffic_enabled=pon.mpcp_enabled=pon.pdc_configured=pon.puc_configured=true;
    pon.registered=~0U;rtl_pon_oam_carrier(&pon);
    for(unsigned int i=0;i<32;i++) {
        struct net_device *data=pon.data_devices[i];
        assert(data&&data->port.llid==i&&!data->port.control&&data->carrier);
        pon.oam_devices[i]->running=data->running=true;
    }
    for(unsigned int i=0;i<128;i++)ethernet[i]=i;
    ethernet[12]=0x81;ethernet[13]=0;ethernet[14]=0;ethernet[15]=100;
    /* Eight/four queue modes preserve every byte and select all 32 LLIDs. */
    for(unsigned int mode=0;mode<2;mode++) {
        pon.llid_count=mode?32:16;
        for(unsigned int i=0;i<pon.llid_count;i++) {
            struct sk_buff *skb=packet(ethernet,128);skb->priority=7;
            assert(rtl_data_transmit(skb,pon.data_devices[i])==NETDEV_TX_OK);
            assert(last_frame.header_high==0x40000000 && !last_frame.prefix_length);
            assert(last_frame.header_low==(0x20002000 | ((32+i)<<3) | (mode?3:7)));
            assert(last_length==128&&!memcmp(last_bytes,ethernet,128));
        }
    }
    unsigned int retired_before=retired;tx_busy=1;
    struct sk_buff *busy=packet(ethernet,128);
    assert(rtl_data_transmit(busy,pon.data_devices[7])==NETDEV_TX_BUSY && retired==retired_before);
    tx_busy=0;assert(rtl_data_transmit(busy,pon.data_devices[7])==NETDEV_TX_OK);
    for(unsigned int i=0;i<32;i++) {
        unsigned int before=pon.data_devices[i]->stats.rx_packets;
        rtl_oam_receive(&pon.oam_endpoint,&napi,packet(ethernet,128),32+i);
        assert(pon.data_devices[i]->stats.rx_packets==before+1);
        assert(last_length==128&&!memcmp(last_bytes,ethernet,128));
    }
    /* Native PDC physical-source7 plus disabled POL_ID routes all 32
     * business LLIDs; control containers still use their own LLID byte. */
    for(unsigned int i=0;i<32;i++) {
        unsigned int previous=pon.data_devices[i]->stats.rx_packets;
        rtl_oam_receive(&pon.oam_endpoint,&napi,packet(ethernet,128),7|(i<<8));
        assert(pon.data_devices[i]->stats.rx_packets==previous+1);
        assert(last_length==128&&!memcmp(last_bytes,ethernet,128));
    }
    /* A nontrivial LLID-table slot must map to its MAC instance, not slot ID. */
    assert(rtl_pon_data_source(17,0x08800001)==40);
    assert(rtl_pon_data_source(3,0x1f800123)==63);
    assert(rtl_pon_data_source(20,0x00c00001)==32);
    assert(rtl_pon_data_source(63,0)==32);
    memcpy(control,"\0\x13\x25\0\0\1\0\x13\x25\0\0\7\xff\xf1\0\0",16);
    memset(control+16,0,60);control[28]=0x88;control[29]=9;control[30]=3;
    unsigned int before=pon.oam_devices[7]->stats.rx_packets;
    rtl_oam_receive(&pon.oam_endpoint,&napi,packet(control,76),7);
    assert(pon.oam_devices[7]->stats.rx_packets==before+1&&last_length==60);
    assert(!memcmp(last_bytes,control+16,60));
    pon.registered=1;rtl_pon_oam_carrier(&pon);
    assert(pon.data_devices[0]->carrier&&!pon.data_devices[7]->carrier);
    before=transmitted;
    assert(rtl_data_transmit(packet(ethernet,128),pon.data_devices[7])==0&&transmitted==before);
    before=received;
    unsigned int dropped=pon.data_devices[7]->stats.rx_dropped;
    rtl_oam_receive(&pon.oam_endpoint,&napi,packet(ethernet,128),39);
    assert(received==before && pon.data_devices[7]->stats.rx_dropped==dropped+1);
    status[4]=1;rtl_pon_oam_carrier(&pon);assert(!pon.data_devices[0]->carrier);
    pon.registered=~0U;status[4]=2;rtl_pon_oam_carrier(&pon);
    for(unsigned int i=0;i<32;i++)pon.oam_devices[i]->stopped=pon.data_devices[i]->stopped=true;
    rtl_oam_wake(&pon.oam_endpoint);assert(woken==64);
    assert(rtl_pon_oam_resize(&pon,1)==0&&live==2);
    assert(rtl_pon_oam_resize(&pon,0)==0&&!live);
    /* Every allocation failure rolls back both interface families completely. */
    for(unsigned int failure=1;failure<=64;failure++) {
        memset(&pon,0,sizeof(pon));allocation_number=0;fail_allocation=failure;
        assert(rtl_pon_oam_resize(&pon,32)==-ENOMEM&&!live&&!rtnl);
        for(unsigned int i=0;i<32;i++)assert(!pon.data_devices[i]&&!pon.oam_devices[i]);
    }
    puts("Actual PON data path: 32 LLID interfaces, 8/4-queue headers, VLAN byte preservation, RX mapping, carrier loss, DMA busy ownership and resize rollback passed");
}
'''

program = model + ''.join(function(name) for name in ('rtl_data_transmit', 'rtl_oam_receive', 'rtl_oam_wake', 'rtl_pon_oam_carrier', 'rtl_pon_oam_resize')) + test
with tempfile.TemporaryDirectory(prefix='pon-data-test-', dir=root / 'artifacts') as temporary:
    temporary = Path(temporary)
    cfile = temporary / 'data-path.c'
    binary = temporary / 'data-path'
    cfile.write_text(program)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable', '-Wno-unused-function',
                    '-O1', '-g', '-fsanitize=address,undefined', '-I', str(drivers), str(cfile), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/pon-data-path-test.json').write_text(json.dumps({'actual_driver_functions_executed':True,
    'LLIDs':32,'queue_modes':[8,4],'VLAN_bytes_preserved':True,'carrier_gating':True,
    'allocation_failures_tested':64,'sanitizers':['ASan','UBSan'],'PON_hardware_verified':False},indent=2)+'\n')
