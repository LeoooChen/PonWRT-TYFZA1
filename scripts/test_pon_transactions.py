#!/usr/bin/env python3
"""Exercise actual control transactions and MAC updates with controlled threads."""
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
parser.add_argument('--revision')
parser.add_argument('--case', choices=('transactions', 'address'), action='append')
args = parser.parse_args()


def read(name):
    if args.revision:
        return subprocess.check_output(['git', 'show', f'{args.revision}:{relative / name}'], cwd=root, text=True)
    return (headers / name).read_text()


def function(text, name):
    start = re.search(r'^.*\b' + re.escape(name) + r'\([^;]*?\)\s*\{', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


common = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-mpcp-backoff.h"
typedef uint32_t u32;typedef uint16_t u16;typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h))) & (~0U<<(l)))
struct mutex {pthread_mutex_t native;bool held;};
static pthread_t starter;
static pthread_mutex_t gate=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed=PTHREAD_COND_INITIALIZER;
static bool window,attempted,finished;
static void mutex_lock(struct mutex *lock) {
    if (pthread_equal(pthread_self(),starter)) {
        pthread_mutex_lock(&gate);attempted=true;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
    }
    assert(pthread_mutex_lock(&lock->native)==0);lock->held=true;
}
static void mutex_unlock(struct mutex *lock) {assert(lock->held);lock->held=false;assert(pthread_mutex_unlock(&lock->native)==0);}
#define lockdep_assert_held(lock) assert((lock)->held)
struct delayed_work {bool pending;};
struct rtl9617c_pon {
    struct mutex lock,control_lock;enum rtl_pon_mode mode;
    void *mac,*control,*epon_control,*gpon_control,*serdes_init,*optical_control,*top_irq,*dev;
    bool mpcp_enabled,mpcp_configured,mac_prepared,traffic_enabled,serdes_configured,gearbox_ready,rx_synced,puc_configured,pdc_configured,fe_enabled;
    u32 registered,waiting,serdes_status;int sync_result,serdes_error,mpcp_irq;
    unsigned int llid_count,los_invert,profile_rows;u8 onu_mac[6];
    struct rtl_mpcp_backoff backoff;struct delayed_work mpcp_work,serdes_work;
};
static struct rtl9617c_pon *current;
static int rtl_pon_fe_configure_locked(struct rtl9617c_pon *pon,bool enable) {
    lockdep_assert_held(&pon->control_lock);pon->fe_enabled=enable;return 0;
}
static unsigned int canceled;
static void *state_reader(void *unused) {
    (void)unused;mutex_lock(&current->lock);mutex_unlock(&current->lock);return NULL;
}
static void cancel_delayed_work_sync(struct delayed_work *work) {
    /* Model a running callback that needs the state mutex to finish. */
    pthread_t worker;assert(!current->lock.held);
    assert(pthread_create(&worker,NULL,state_reader,NULL)==0);
    assert(pthread_join(worker,NULL)==0);work->pending=false;canceled++;
}
static void schedule_delayed_work(struct delayed_work *work,unsigned int delay) {(void)delay;work->pending=true;}
#define HZ 1000
#define msleep(ms) ((void)0)
#define udelay(us) ((void)0)
#define dev_info(...) ((void)0)
static u32 readl(const void *p) {u32 value;memcpy(&value,p,4);return value;}
static void writel(u32 value,void *p) {memcpy(p,&value,4);}
static void rtl_pon_update(void *p,u32 mask,u32 value) {writel((readl(p)&~mask)|(value&mask),p);}
#define readl_poll_timeout(addr,status,condition,delay,timeout) ({(status)=readl(addr);(condition)?0:-ETIMEDOUT;})
static int is_valid_ether_addr(const u8 *mac) {return !(mac[0]&1) && memcmp(mac,"\0\0\0\0\0\0",6);}
static u16 get_unaligned_be16(const u8 *p) {return p[0]*256+p[1];}
static u32 get_unaligned_be32(const u8 *p) {return (u32)p[0]<<24|(u32)p[1]<<16|p[2]<<8|p[3];}
static void rtl_pon_oam_carrier(struct rtl9617c_pon *pon) {assert(pon->lock.held);}
static void synchronize_irq(int irq) {(void)irq;assert(!current->lock.held);}
struct firmware {};
static const struct firmware firmware;
static int request_firmware_direct(const struct firmware **fw,const char *name,void *dev) {(void)name;(void)dev;*fw=&firmware;return 0;}
static void release_firmware(const struct firmware *fw) {assert(fw==&firmware);}
static int rtl_pon_profile_validate(const struct firmware *fw) {(void)fw;return 0;}
static void rtl_pon_profile_load(struct rtl9617c_pon *pon,const struct firmware *fw) {(void)fw;pon->profile_rows=1;}
static int rtl_pon_burst_polarity(struct rtl9617c_pon *pon,unsigned int page) {(void)pon;(void)page;return 0;}
static void rtl_pon_gearbox_release(struct rtl9617c_pon *pon) {pon->gearbox_ready=true;}
static int rtl_pon_epon_traffic_locked(struct rtl9617c_pon *pon,bool enable) {
    assert(!enable);pon->traffic_enabled=false;
    /* Cut exactly after stop, while another thread tries public MPCP start. */
    pthread_mutex_lock(&gate);window=true;pthread_cond_broadcast(&changed);
    while(!attempted) pthread_cond_wait(&changed,&gate);
    if (!pon->control_lock.held) while(!finished) pthread_cond_wait(&changed,&gate);
    pthread_mutex_unlock(&gate);return 0;
}
static int rtl_pon_epon_traffic(struct rtl9617c_pon *pon,bool enable) {return rtl_pon_epon_traffic_locked(pon,enable);}
'''

transaction_test = r'''
static int start_result;
static void *concurrent_start(void *arg) {
    struct rtl9617c_pon *pon=arg;
    pthread_mutex_lock(&gate);while(!window) pthread_cond_wait(&changed,&gate);pthread_mutex_unlock(&gate);
    start_result=rtl_pon_mpcp_configure(pon);
    pthread_mutex_lock(&gate);finished=true;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);return NULL;
}
int main(void) {
    u32 mac[0xc000/4]={},top[2]={},control=0,epon=0,gpon=0,serdes=0,optical=0;
    struct rtl9617c_pon pon={.mode=RTL_PON_EPON_D10G,.mac=mac,.top_irq=top,
        .control=&control,.epon_control=&epon,.gpon_control=&gpon,.serdes_init=&serdes,.optical_control=&optical,
        .llid_count=1,.mac_prepared=true,.serdes_configured=true,.onu_mac={2,1,2,3,4,5}};
    current=&pon;assert(pthread_mutex_init(&pon.lock.native,NULL)==0);assert(pthread_mutex_init(&pon.control_lock.native,NULL)==0);
    mac[PSDS_STATUS/4]=PSDS_SYNC_GOAL;
    for(unsigned int mode=RTL_PON_GPON;mode<RTL_PON_MODE_COUNT;mode++) {
        pon.mode=RTL_PON_EPON_D10G;pon.mac_prepared=true;pon.mpcp_enabled=pon.mpcp_configured=true;
        pon.mpcp_work.pending=true;pon.fe_enabled=true;top[1]=1;window=attempted=finished=false;
        assert(pthread_create(&starter,NULL,concurrent_start,&pon)==0);
        assert(rtl_pon_serdes_configure(&pon,mode)==0);
        assert(pthread_join(starter,NULL)==0);
        assert(start_result<0 && pon.mode==(enum rtl_pon_mode)mode && !pon.mpcp_enabled && !pon.mpcp_configured);
        assert(!pon.mpcp_work.pending && !(top[1]&1) && pon.serdes_work.pending && !pon.fe_enabled);
        assert(!pon.control_lock.held && !pon.lock.held);
    }
    assert(canceled==6);
    puts("Actual SerDes/MPCP transaction: concurrent restart blocked, all GPON-family results idle, state workers finish during cancel");
    pthread_mutex_destroy(&pon.lock.native);pthread_mutex_destroy(&pon.control_lock.native);
}
'''

address_model = r'''
#define ETH_ALEN 6
#define NET_ADDR_SET 3
#define NETDEV_CHANGEADDR 8
struct rtl_oam_port {bool mac_configured;};
struct net_device {struct rtl_oam_port private;u8 address[6];bool locked;int addr_assign_type;unsigned int updates,notifications;};
struct device {void *data;};struct device_attribute {};
struct rtl9617c_pon {struct mutex control_lock,lock;bool mpcp_enabled;u8 onu_mac[6];struct net_device *oam_devices[32],*data_devices[32];};
static bool rtnl;
#define ASSERT_RTNL() assert(rtnl)
#define rtnl_dereference(p) (assert(rtnl),(p))
#define rcu_dereference(p) (p)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define dev_get_drvdata(dev) ((struct rtl9617c_pon *)(dev)->data)
static void rtnl_lock(void) {assert(!rtnl);rtnl=true;}
static void rtnl_unlock(void) {assert(rtnl);rtnl=false;}
static void *netdev_priv(struct net_device *dev) {return &dev->private;}
static void netdev_lock(struct net_device *dev) {assert(rtnl&&!dev->locked);dev->locked=true;}
static void netdev_unlock(struct net_device *dev) {assert(dev->locked);dev->locked=false;}
static void ether_addr_copy(u8 *dst,const u8 *src) {memcpy(dst,src,6);}
static void eth_hw_addr_set(struct net_device *dev,const u8 *mac) {assert(rtnl&&dev->locked);memcpy(dev->address,mac,6);dev->updates++;}
static int eth_mac_addr(struct net_device *dev,void *address) {
 if(!address)return -EINVAL;
 eth_hw_addr_set(dev,address);return 0;
}
static void call_netdevice_notifiers(int event,struct net_device *dev) {assert(rtnl&&dev->locked&&event==NETDEV_CHANGEADDR);dev->notifications++;}
static int mac_pton(const char *text,u8 *out) {unsigned int b[6];if(sscanf(text,"%x:%x:%x:%x:%x:%x",b,b+1,b+2,b+3,b+4,b+5)!=6)return 0;for(unsigned int i=0;i<6;i++)out[i]=b[i];return 1;}
static int is_valid_ether_addr(const u8 *mac) {return !(mac[0]&1)&&memcmp(mac,"\0\0\0\0\0\0",6);}
'''
address_test = r'''
int main(void) {
    struct rtl9617c_pon pon={};struct device device={&pon};struct net_device ports[3]={},data[3]={};
	pon.oam_devices[0]=ports;pon.oam_devices[7]=ports+1;pon.oam_devices[31]=ports+2;
    pon.data_devices[0]=data;pon.data_devices[7]=data+1;pon.data_devices[31]=data+2;
    pthread_mutex_init(&pon.lock.native,NULL);pthread_mutex_init(&pon.control_lock.native,NULL);
    const char *mac="02:11:22:33:44:f0";
    assert(onu_mac_store(&device,NULL,mac,17)==17);
    const u8 expected[3]={0xf0,0xf7,0x0f};
    for(unsigned int i=0;i<3;i++) {
        assert(ports[i].address[5]==expected[i] && ports[i].updates==1 && ports[i].notifications==1);
		assert(!ports[i].locked);
        assert(!memcmp(data[i].address,ports[i].address,6) && data[i].updates==1 && data[i].notifications==1);
    }
    /* The normal network MAC path owns the business address, while OAM
     * remains derived from ONU MAC. Test both failed and successful setters. */
    const u8 configured[6]={2,0x55,0x44,0x33,0x22,0x11};
    rtnl_lock();netdev_lock(data+1);
    assert(rtl_data_set_address(data+1,NULL)==-EINVAL && !data[1].private.mac_configured);
    assert(rtl_data_set_address(data+1,(void *)configured)==0 && data[1].private.mac_configured);
    netdev_unlock(data+1);rtnl_unlock();
    assert(onu_mac_store(&device,NULL,"02:11:22:33:44:02",17)==17);
    assert(!memcmp(data[1].address,configured,6) && ports[1].address[5]==9);
    assert(data[0].address[5]==2 && data[2].address[5]==33);
        pon.mpcp_enabled=true;assert(onu_mac_store(&device,NULL,"02:11:22:33:44:01",17)==-EBUSY);
    assert(!rtnl&&!pon.lock.held&&!pon.control_lock.held);
    for(unsigned int i=0;i<3;i++)assert(ports[i].updates==2);
    pthread_mutex_destroy(&pon.lock.native);pthread_mutex_destroy(&pon.control_lock.native);
    puts("Actual ONU MAC store: RTNL/device locks, LLID-derived addresses, change notifications and busy rejection passed");
}
'''

controller = read('rtl9617c-pon.c')
serdes = read('rtl9617c-pon-serdes.c')
mpcp = read('rtl9617c-pon-mpcp.c')
oam = read('rtl9617c-pon-oam.c')
records = []
with tempfile.TemporaryDirectory(prefix='control-tests-', dir=root / 'artifacts') as directory:
    directory = Path(directory)
    for case in args.case or ['transactions', 'address']:
        if case == 'transactions':
            constants = '\n'.join(re.findall(r'^#define .*$', serdes + '\n' + mpcp, re.M))
            definitions = function(mpcp, 'rtl_pon_llid_mask')
            locked = '_locked' if 'void rtl_pon_mpcp_stop_locked' in mpcp else ''
            definitions += function(mpcp, 'rtl_pon_mpcp_stop' + locked) + function(mpcp, 'rtl_pon_mpcp_configure' + locked)
            definitions += function(serdes, 'rtl_pon_serdes_configure' + locked)
            if locked:
                definitions += function(controller, 'rtl_pon_mpcp_configure') + function(controller, 'rtl_pon_serdes_configure')
            source = common + constants + '\n' + definitions + transaction_test
        else:
            if not args.revision:
                assert '.ndo_set_mac_address =' not in oam.split('static const struct net_device_ops rtl_oam_ops = {')[1].split('};',1)[0]
            # Reuse the host mutex primitive; the RX/MMIO model is unnecessary.
            prefix = common[:common.index('struct delayed_work')]
            helper = function(oam, 'rtl_data_set_address') + function(oam, 'rtl_port_address') if 'static void rtl_port_address' in oam else ''
            source = prefix + address_model + helper + function(oam, 'rtl_pon_oam_address') + function(controller, 'onu_mac_store') + address_test
        cfile = directory / f'{case}.c'
        binary = directory / case
        cfile.write_text(source)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter',
                        '-O1', '-g', '-pthread', '-fsanitize=address,undefined', '-I', str(headers), str(cfile), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        if result.returncode:
            print(result.stderr,end='')
            raise SystemExit(result.returncode)
        print(result.stdout,end='')
        records.append({'case':case,'passed':True,'output':result.stdout.strip()})
if not args.revision:
    (root / 'logs/pon-control-transaction-tests.json').write_text(json.dumps({'tests':records,
        'production_source_sha256':{name:hashlib.sha256(read(name).encode()).hexdigest() for name in (
            'rtl9617c-pon.c','rtl9617c-pon-serdes.c','rtl9617c-pon-mpcp.c','rtl9617c-pon-oam.c')},
        'sanitizers':['ASan','UBSan'],'hardware_access':False},indent=2)+'\n')
