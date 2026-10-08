#!/usr/bin/env python3
"""Run production RX/sysfs/EPON functions against host MMIO and lock models."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = Path('port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek')
headers = root / relative
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision', help='Run a historical Git revision to reproduce a regression')
parser.add_argument('--case', choices=('qm', 'diagnostics', 'epon'), action='append')
args = parser.parse_args()


def read(name):
    if args.revision:
        return subprocess.check_output(['git', 'show', f'{args.revision}:{relative / name}'], cwd=root, text=True)
    return (headers / name).read_text()


def function(source, name):
    start = re.search(r'^.*\b' + re.escape(name) + r'\([^;]*?\)\s*\{', source, re.M).start()
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


qm = read('rtl9617c-qm.c')
qh = read('rtl9617c-qm.h')
xram = read('rtl9617c-xram.c')
epon = read('rtl9617c-pon-epon.c')
controller = read('rtl9617c-pon.c')
constants = '\n'.join(re.findall(r'^#define .*$', qm + '\n' + qh, re.M))
rx_structure = re.search(r'struct rtl_qm_rx \{.*?\n\};', qh, re.S)[0]
common = r'''
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include "rtl9617c-qm-refill.h"
#include "rtl9617c-qm-deepq.h"
typedef uint32_t u32;
typedef uint16_t u16;
typedef uint8_t u8;
typedef unsigned long dma_addr_t;
#define __iomem
#define __rcu
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
#define FIELD_GET(mask,v) (((v) & (mask)) >> __builtin_ctz(mask))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define READ_ONCE(x) (x)
#define lower_32_bits(x) ((u32)(x))
#define NAPI_POLL_WEIGHT 64
#define ETH_HLEN 14
#define ETH_P_IP 0x800
#define IPPROTO_UDP 17
#define PAGE_SIZE 4096
#define dma_rmb() ((void)0)
#define mb() ((void)0)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define rcu_dereference(x) (x)
struct net_device { struct {unsigned long rx_errors,rx_dropped,rx_packets,rx_bytes;} stats; };
struct napi_struct {struct net_device *dev;};
struct sk_buff {unsigned char *data; unsigned int protocol;};
struct rtl9617c_ni_endpoint {
    void (*receive)(struct rtl9617c_ni_endpoint *,struct napi_struct *,struct sk_buff *,unsigned int);
};
static unsigned int delivered, returned, owned[512], expected_source;
static unsigned char *registers;
static u32 readl(const void *address) {u32 value; memcpy(&value,address,4); return value;}
static void writel(u32 value,void *address) {
    unsigned int offset=(unsigned char *)address-registers;
    if (offset==0x636c) {
        unsigned int buffer=((value & 0xffffff80U)-0x10002000U)/2048;
        assert(buffer<512 && !owned[buffer]);
        assert((value & 15)==4+buffer/256);
        owned[buffer]=1; returned++;
    }
    if (offset==0x66b0) value &= ~BIT(31);
    memcpy(address,&value,4);
    if (offset>=0x7100 && offset<0x7120) {
        u32 flags=readl(registers+0x659c) & ~BIT((offset-0x7100)/4);
        memcpy(registers+0x659c,&flags,4);
    }
}
#define readl_poll_timeout(addr,status,condition,delay,timeout) \
    ({ (status)=readl(addr); (condition)?0:-ETIMEDOUT; })
#define readl_poll_timeout_atomic readl_poll_timeout
static u32 get_unaligned_le32(const void *p) {u32 value; memcpy(&value,p,4); return value;}
static u32 get_unaligned_be32(const void *p) {return __builtin_bswap32(get_unaligned_le32(p));}
static u16 get_unaligned_be16(const void *p) {u16 value; memcpy(&value,p,2); return __builtin_bswap16(value);}
static struct sk_buff *napi_alloc_skb(struct napi_struct *napi,unsigned int size) {
    (void)napi; struct sk_buff *skb=malloc(sizeof(*skb)); assert(skb);
    skb->data=calloc(1,size); assert(skb->data); return skb;
}
static void *skb_put(struct sk_buff *skb,unsigned int length) {(void)length; return skb->data;}
static void dev_kfree_skb_any(struct sk_buff *skb) {free(skb->data);free(skb);}
static void napi_gro_receive(struct napi_struct *napi,struct sk_buff *skb) {
    (void)napi;delivered++;dev_kfree_skb_any(skb);
}
static unsigned int eth_type_trans(struct sk_buff *skb,struct net_device *dev) {(void)skb;(void)dev;return 0;}
static int netif_running(struct net_device *dev) {(void)dev;return 1;}
static int scnprintf(char *buf,size_t size,const char *fmt,...) {
    va_list ap; va_start(ap,fmt);int length=vsnprintf(buf,size,fmt,ap);va_end(ap);return length;
}
'''

rx_test = r'''
static void pon_receive(struct rtl9617c_ni_endpoint *endpoint,struct napi_struct *napi,struct sk_buff *skb,unsigned int source) {
    (void)endpoint;(void)napi;
    assert(source==expected_source);delivered++;dev_kfree_skb_any(skb);
}
static void store(void *p,u32 value) {memcpy(p,&value,4);}
static void check(unsigned int packets,int budget,bool blocked,int flow) {
    struct net_device dev={}; struct napi_struct napi={&dev}; struct rtl_qm_rx rx={};
    unsigned int count=0;
    struct rtl9617c_ni_endpoint endpoint={pon_receive};
    if(flow>=0){rx.pon=&endpoint;expected_source=7|((unsigned int)flow<<8);}
    registers=calloc(1,0x10000);rx.ni=registers;
    rx.memory=calloc(1,RTL_QM_MEMORY_BYTES);rx.memory_dma=0x10000000;
    memset(owned,0,sizeof(owned));delivered=returned=0;
    store(registers+0x6338,blocked?0:1);store(registers+0x633c,blocked?0:1);
    store(registers+0x6368,blocked?0:BIT(31));
    /* Up to four full 128-descriptor rings, with unique EQ4/EQ5 buffers. */
    for (int cos=7;cos>=0 && count<packets;cos--) {
        unsigned int n=packets-count; if(n>128)n=128;
        store(registers+0x7000+cos*4,(n*8)%RTL_QM_RING_BYTES);
        if (n==128) store(registers+0x659c,readl(registers+0x659c)|BIT(cos));
        for (unsigned int j=0;j<n;j++,count++) {
            unsigned int offset=8192+count*2048;
            unsigned int command=(rx.memory_dma+offset)|BIT(4)|BIT(6)|(4+count/256);
            store(rx.memory+cos*1024+j*8,command);
            store(rx.memory+offset+32,flow>=0?__builtin_bswap32((unsigned int)flow<<18):0);
            store(rx.memory+offset+36,__builtin_bswap32((60U<<15)|(flow>=0?7<<9:0)));
        }
    }
    int consumed=rtl_qm_rx_poll(&rx,&napi,budget);
    assert(consumed==(int)(packets<(unsigned int)budget?packets:(unsigned int)budget));
    assert(delivered==(unsigned int)consumed && dev.stats.rx_errors==0);
#if HAS_REFILL
    assert(rx.refill.pending+returned==(unsigned int)consumed);
    if (blocked) {assert(!returned);assert(rx.refill.pending==(unsigned int)consumed);}
    store(registers+0x6338,1);store(registers+0x633c,1);store(registers+0x6368,BIT(31));
    for(unsigned int retry=0;retry<16;retry++) rtl_qm_rx_poll(&rx,&napi,budget);
    assert(delivered==packets && returned==packets && !rx.refill.pending);
    for(unsigned int i=0;i<packets;i++) assert(owned[i]==1);
#endif
    free(rx.memory);free(registers);
}
int main(void) {
    check(65,65,false,-1);check(65,256,true,-1);
    check(129,256,false,-1);check(256,256,true,-1);
    check(512,512,true,-1);check(512,1024,false,-1);check(129,64,true,-1);
    check(65,65,false,13);check(65,256,true,31);
    puts("Actual QM RX: 65/129/256/512 packets, budget64..1024 and blocked-pool recovery passed");
}
'''

diagnostics_model = r'''
typedef unsigned short umode_t;
struct attribute {umode_t mode;};
struct device_attribute {struct attribute attr;};
struct rtl_xram {bool qm_rx,dma_tx;struct rtl_qm_rx qm;};
struct device {struct rtl_xram *data;};
struct kobject {struct device *dev;};
#define netdev_priv(p) (p)
#define dev_get_drvdata(d) ((d)->data)
#define kobj_to_dev(k) ((k)->dev)
static struct device_attribute dev_attr_qm_pool={{0444}},dev_attr_qm_trace={{0644}},
    dev_attr_qm_wait={{0644}},dev_attr_qm_es={{0644}},dev_attr_qm_buffer={{0644}},dev_attr_qm_state={{0444}},dev_attr_pon_deepq={{0444}};
int main(void) {
    struct rtl_xram priv={};struct device dev={&priv};struct kobject kobj={&dev};
    struct device_attribute *attrs[]={&dev_attr_qm_pool,&dev_attr_qm_trace,&dev_attr_qm_wait,&dev_attr_qm_es,&dev_attr_qm_buffer,&dev_attr_pon_deepq};
    char buf[4096];
    for (unsigned int dma=0;dma<2;dma++) {
        priv.dma_tx=dma;
        /* Match sysfs: only visible attributes can dispatch their show method. */
        if (rtl_native_visible(&kobj,&dev_attr_qm_pool.attr,0)) qm_pool_show(&dev,&dev_attr_qm_pool,buf);
        for(unsigned int i=0;i<ARRAY_SIZE(attrs);i++) assert(!rtl_native_visible(&kobj,&attrs[i]->attr,i));
        assert(rtl_native_visible(&kobj,&dev_attr_qm_state.attr,5)==0444);
    }
    registers=calloc(1,0x10000);priv.qm.ni=registers;priv.qm_rx=true;
    for(unsigned int i=0;i<ARRAY_SIZE(attrs);i++) assert(rtl_native_visible(&kobj,&attrs[i]->attr,i)==attrs[i]->attr.mode);
    assert(qm_pool_show(&dev,&dev_attr_qm_pool,buf)>0);
    free(registers);
    puts("Actual sysfs visibility/show: XRAM and DMA-TX hide QM-only attributes; QM mode reads pool normally");
}
'''

epon_model = r'''
#include <pthread.h>
#include "rtl9617c-pon-mode.h"
struct mutex {pthread_mutex_t native;bool held;};
struct net_device {};
struct rtl9617c_pon {
	struct mutex lock,control_lock;enum rtl_pon_mode mode;
    bool serdes_configured,mac_prepared,pdc_configured,puc_configured,traffic_enabled,fe_enabled;
    int mac_error;struct {struct net_device *netdev;} oam_endpoint;
    void *mac,*epon_control,*control,*serdes_init,*dev,*tx_disable;
};
static struct rtl9617c_pon *current;
static void gpiod_set_value_cansleep(void *gpio, int value) {(void)gpio;(void)value;}
static int switch_to=-1;
static unsigned int io_writes;
static void *switch_protocol(void *arg) {
    struct mutex *lock=arg;
    assert(pthread_mutex_lock(&lock->native)==0);
    current->mode=switch_to;
    assert(pthread_mutex_unlock(&lock->native)==0);
    return NULL;
}
static void mutex_lock(struct mutex *lock) {
    if (switch_to>=0) {
        pthread_t writer;assert(pthread_create(&writer,NULL,switch_protocol,lock)==0);
        assert(pthread_join(writer,NULL)==0);switch_to=-1;
    }
    assert(pthread_mutex_lock(&lock->native)==0);lock->held=true;
}
static void mutex_unlock(struct mutex *lock) {assert(lock->held);lock->held=false;assert(pthread_mutex_unlock(&lock->native)==0);}
#define lockdep_assert_held(lock) assert((lock)->held)
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h))) & (~0U<<(l)))
typedef uint32_t u32;
static void assert_epon(void) {assert(current->lock.held && rtl_pon_mode_is_epon(current->mode));}
static u32 readl(const void *address) {assert_epon();u32 value;memcpy(&value,address,4);return value;}
static void writel(u32 value,void *address) {assert_epon();memcpy(address,&value,4);io_writes++;}
static void rtl_pon_update(void *reg,u32 mask,u32 value) {writel((readl(reg)&~mask)|(value&mask),reg);}
#define readl_poll_timeout(addr,status,condition,delay,timeout) ({(status)=readl(addr);(condition)?0:-ETIMEDOUT;})
#define msleep(ms) ((void)0)
#define dev_info(...) ((void)0)
static void rtl_pon_mpcp_stop(struct rtl9617c_pon *pon) {(void)pon;}
static void rtl_pon_mpcp_stop_locked(struct rtl9617c_pon *pon) {assert(pon->control_lock.held);}
static int rtl_pon_fe_configure_locked(struct rtl9617c_pon *pon,bool enable) {
    assert(pon->control_lock.held && !pon->lock.held);pon->fe_enabled=enable;return 0;
}
static void rtl_pon_oam_carrier(struct rtl9617c_pon *pon) {assert(pon->lock.held);}
static int netif_running(struct net_device *dev) {(void)dev;return 1;}
'''
epon_test = r'''
int main(void) {
    u32 mac[0xc000/4]={},control=0,epon=0,serdes=0;struct net_device netdev={};
    struct rtl9617c_pon pon={.serdes_configured=true,.mac=mac,.control=&control,.epon_control=&epon,.serdes_init=&serdes};
    pon.oam_endpoint.netdev=&netdev;current=&pon;
    assert(pthread_mutex_init(&pon.lock.native,NULL)==0);
    assert(pthread_mutex_init(&pon.control_lock.native,NULL)==0);mac[0x600/4]=1;
    for(unsigned int mode=0;mode<3;mode++) {
        pon.mode=mode;io_writes=0;
        assert(rtl_pon_epon_prepare(&pon)==0 && pon.mac_prepared && io_writes);
        assert(((epon>>16)&3)==mode+1);
        pon.puc_configured=pon.pdc_configured=true;
        assert(rtl_pon_epon_traffic(&pon,true)==0 && pon.traffic_enabled);
        pon.fe_enabled=true;
        assert(rtl_pon_epon_traffic(&pon,false)==0 && !pon.traffic_enabled && !pon.fe_enabled);
    }
    /* Another thread switches while the caller is about to take the mutex. */
    for(unsigned int mode=RTL_PON_GPON;mode<RTL_PON_MODE_COUNT;mode++) {
        pon.mode=RTL_PON_EPON_D10G;io_writes=0;switch_to=mode;
        assert(rtl_pon_epon_prepare(&pon)==-EOPNOTSUPP && !io_writes && !pon.lock.held);
        pon.mode=RTL_PON_EPON_D10G;switch_to=mode;
        assert(rtl_pon_epon_traffic(&pon,true)==-EOPNOTSUPP && !io_writes && !pon.lock.held);
        pon.mode=RTL_PON_EPON_D10G;switch_to=mode;
        assert(rtl_pon_epon_traffic(&pon,false)==0 && !io_writes && !pon.lock.held);
    }
    pon.mode=RTL_PON_EPON_1G;switch_to=RTL_PON_EPON_BI10G;
    assert(rtl_pon_epon_prepare(&pon)==0 && ((epon>>16)&3)==3);
    assert(pthread_mutex_destroy(&pon.lock.native)==0);
    assert(pthread_mutex_destroy(&pon.control_lock.native)==0);
    puts("Actual EPON prepare/traffic: three modes, concurrent GPON-family switch and fresh ONU mode passed");
}
'''

cases = args.case or ['qm', 'diagnostics', 'epon']
records = []
with tempfile.TemporaryDirectory(prefix='native-rx-tests-', dir=root / 'artifacts') as directory:
    directory = Path(directory)
    for case in cases:
        if case == 'epon':
            declarations = '\n'.join(re.findall(r'^#define .*$', epon, re.M))
            names = ('rtl_epon_csr_reset', 'rtl_epon_fec_configure', 'rtl_pon_epon_prepare', 'rtl_pon_epon_traffic')
            if 'int rtl_pon_epon_prepare_locked' in epon:
                functions = ''.join(function(epon, name + '_locked' if name.startswith('rtl_pon_') else name) for name in names)
                functions += function(controller, 'rtl_pon_epon_prepare') + function(controller, 'rtl_pon_epon_traffic')
            else:
                functions = ''.join(function(epon, name) for name in names)
            source = '#include <stdbool.h>\n#include <stdint.h>\n#include <assert.h>\n#include <errno.h>\n#include <stdio.h>\n#include <string.h>\n' + epon_model + declarations + '\n' + functions + epon_test
        else:
            functions = function(qm, 'rtl_qm_try_push' if 'static int rtl_qm_try_push' in qm else 'rtl_qm_push')
            if case == 'qm':
                source = common + constants + '\n' + rx_structure + '\n' + functions + function(qm, 'rtl_qm_rx_poll')
                source += '\n#define HAS_REFILL ' + str(int('struct rtl_qm_refill refill;' in qh)) + '\n' + rx_test
            else:
                visibility = function(xram, 'rtl_native_visible') if 'static umode_t rtl_native_visible' in xram else 'static umode_t rtl_native_visible(struct kobject *kobj,struct attribute *attr,int index) {(void)kobj;(void)index;return attr->mode;}\n'
                model, main = diagnostics_model.split('int main(void)', 1)
                source = common + constants + '\n' + rx_structure + '\n' + function(qm, 'rtl_qm_indirect') + function(qm, 'rtl_qm_rx_pool') + model + visibility + function(xram, 'qm_pool_show') + 'int main(void)' + main
        path = directory / f'{case}.c'
        binary = directory / case
        path.write_text(source)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                        '-Wno-unused-parameter', '-O1', '-g', '-fsanitize=address,undefined', '-pthread',
                        *(['-Wno-error=sign-compare'] if args.revision else []),
                        '-I', str(headers), str(path), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if result.returncode:
            print(result.stderr, end='')
            raise SystemExit(result.returncode)
        print(result.stdout, end='')
        records.append({'case':case,'passed':True,'output':result.stdout.strip()})
if not args.revision:
    (root / 'logs/native-rx-mode-tests.json').write_text(json.dumps({'tests': records,
        'production_source_sha256': {name: hashlib.sha256(read(name).encode()).hexdigest() for name in (
            'rtl9617c-qm.c','rtl9617c-qm.h','rtl9617c-xram.c','rtl9617c-pon-epon.c')},
        'sanitizers':['ASan','UBSan'],'MMIO':'modeled; no hardware access'}, indent=2) + '\n')
