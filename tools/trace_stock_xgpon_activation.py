#!/usr/bin/env python3
"""Compare XGTC activation's CDR, gearbox, MAC and laser ordering with stock."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE

root = Path(__file__).resolve().parents[1]
relative = Path('port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek')
drivers = root / relative
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision', help='Compare an older native activation function')
args = parser.parse_args()
activation = subprocess.check_output(['git', 'show', f'{args.revision}:{relative}/rtl9617c-pon-xgpon.c'],
                                    cwd=root, text=True) if args.revision else (drivers / 'rtl9617c-pon-xgpon.c').read_text()
serdes = (drivers / 'rtl9617c-pon-serdes.c').read_text()


def function(source, name):
    start = re.search(r'^(?:static )?(?:int|void|unsigned int) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


fixture = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-xgpon-hw.h"
typedef uint8_t u8;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define lockdep_assert_held(p) assert(*(p))
#define mutex_lock(p) (*(p)=1)
#define mutex_unlock(p) (*(p)=0)
#define netif_running(p) ((void)(p),true)
#define readl_poll_timeout(p,v,c,d,t) ({v=readl(p);(c)?0:-ETIMEDOUT;})
struct rtl9617c_pon {
 int mode,lock,control_lock,xgpon_activation_error,serdes_error,sync_result;
 bool xgpon_recover,mac_prepared,puc_configured,pdc_configured,xgpon_enabled,xgpon_omcc_ready;
 bool gearbox_ready,rx_synced;u32 xgpon_last_state,serdes_status;
 unsigned char *mac;void *control,*serdes_init,*top_irq,*tx_disable,*omci_device;
};
static u32 registers[0x10000/4],globals[2],top[2];
static int laser,carrier,access_failure;
unsigned int events[128][3],event_count;
int results[8];
static void event(unsigned int kind,unsigned int address,unsigned int value) {
 assert(event_count<128);
 events[event_count][0]=kind;events[event_count][1]=address;events[event_count++][2]=value;
}
static unsigned int readl(const void *p) {
 unsigned int value=*(const unsigned int*)p;
 return p==registers+0xa0cc/4 && !access_failure?value&~BIT(31):value;
}
static void writel(unsigned int value,void *p) {
 *(unsigned int*)p=value;
 unsigned int offset=(uintptr_t)p-(uintptr_t)registers;
 if(p==globals)offset=0x10094;
 if(p==globals+1)offset=0x1022c;
 switch(offset) {
 case 0x10094:case 0x1022c:case 0xa0cc:case 0xa0d0:case 0xa084:case 0x4004:
  event(0,offset,value);break;
 }
}
static void rtl_pon_update(void *p,unsigned int mask,unsigned int value) {writel((readl(p)&~mask)|value,p);}
static void msleep(unsigned int delay) {event(1,0,delay);}
static void gpiod_set_value_cansleep(void *p,int value) {*(int*)p=value;event(2,0,value);}
#define netif_carrier_off(p) (*(int*)(p)=0)
static void rtl_pon_xgpon_stop_locked(struct rtl9617c_pon *p) {(void)p;}
'''
defines = '\n'.join(re.findall(r'^#define PSDS_.*$', serdes, re.M)) + '\n'
wrapper = r'''
int run(unsigned int mode,unsigned int prbs,unsigned int status,unsigned int fail) {
 memset(registers,0,sizeof(registers));memset(top,0,sizeof(top));
 globals[0]=0x30e;globals[1]=0x23;laser=1;carrier=1;access_failure=fail;
 registers[0x4004/4]=mode==RTL_PON_XGPON?0x60:0xa0;
 registers[0xa064/4]=0x50|prbs;registers[0xa060/4]=status;registers[0xa0d4/4]=0x1234;
 struct rtl9617c_pon pon={.mode=mode,.control_lock=1,.mac=(void*)registers,
  .control=globals,.serdes_init=globals+1,.top_irq=top,.tx_disable=&laser,.omci_device=&carrier,
  .mac_prepared=true,.puc_configured=true,.pdc_configured=true};
 event_count=0;int ret=rtl_pon_xgpon_activation_locked(&pon,true);
 int values[]={ret,laser,pon.xgpon_enabled,pon.xgpon_activation_error,pon.serdes_error,
  pon.sync_result,registers[0x4004/4]&3,carrier};
 memcpy(results,values,sizeof(values));return ret;
}
'''
records = []
with tempfile.TemporaryDirectory(prefix='xgpon-activation-') as directory:
    work = Path(directory)
    (work / 'native.c').write_text(fixture + defines +
        function(serdes, 'rtl_pon_gearbox_release') + function(serdes, 'rtl_pon_serdes_restart_locked') +
        ''.join(function(activation, name) for name in ('rtl_xgpon_read', 'rtl_xgpon_write',
                 'rtl_xgpon_indirect', 'rtl_pon_xgpon_activation_locked')) + wrapper)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-I', str(drivers),
                    str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    native.run.argtypes = [ctypes.c_uint] * 4
    count = ctypes.c_uint.in_dll(native, 'event_count')
    events = ((ctypes.c_uint * 3) * 128).in_dll(native, 'events')
    results = (ctypes.c_int * 8).in_dll(native, 'results')
    for mode in (4, 5):
        for prbs in (0, 1):
            stock_events = []

            def callback(name, arguments):
                if name == 'ca_mdelay':
                    stock_events.append([1, 0, arguments[0]])
                elif name == 'ca_pon_laser_tx_disable_set':
                    stock_events.append([2, 0, arguments[1]])
                elif name not in ('ca_spin_lock', 'ca_spin_unlock', 'ca_printf'):
                    raise RuntimeError((name, arguments))
                return 0

            names = ['aal_pon_mac_enable_set', 'aal_psds_burst_enable', 'aal_psds_reset',
                     'aal_psds_gbox_ctrl_get', 'aal_xgpon_xgpn_glb_ctrl_set']
            oracle = ModuleOracle(module, names, callback)
            pon, global_ = 0x6000000, 0x6100000
            for symbol, base in [('ca_venus_pon_reg_iobase', pon), ('ca_venus_pon_glb_reg_iobase', global_)]:
                oracle.uc.mem_map(base, 0x10000)
                oracle.word(oracle.symbols[symbol][0], base)
            oracle.uc.mem_write(oracle.symbols['aal_pon_mac_mode'][0], struct.pack('<I', mode))
            for address, value in [(global_ + 0x22c, 0x23), (global_ + 0x94, 0x30e),
                (pon + 0x4004, 0x60 if mode == 4 else 0xa0), (pon + 0xa064, 0x50 | prbs),
                (pon + 0xa060, 0x9c00), (pon + 0xa0d4, 0x1234)]:
                oracle.uc.mem_write(address, struct.pack('<I', value))
            oracle.uc.hook_add(UC_HOOK_MEM_WRITE,
                lambda uc, access, at, size, value, data: stock_events.append(
                    [0, at - global_ + 0x10000 if at >= global_ else at - pon, value]),
                begin=pon, end=global_ + 65535)
            assert oracle.call('aal_pon_mac_enable_set', 0, 7, 1, 1) == 0
            assert native.run(mode, prbs, 0x9c00, 0) == 0
            actual = [list(events[i]) for i in range(count.value)]
            records.append(dict(mode=mode, prbs=prbs, stock=stock_events, native=actual,
                                match=actual == stock_events))
    result = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), revision=args.revision,
                  records=records, event_format='[0, PON offset or global offset+0x10000, value]; [1,0,delay_ms]; [2,0,TX_DISABLE]',
                  scope='Actual original MAC enable/reset/burst/gearbox/global setters and native activation/reset. '
                        'MMIO, indirect completion, delays, GPIO, locks and netdev state are modeled. '
                        'Compare write ordering and requested waits, not elapsed silicon timing or optical registration.')
    (root / 'logs/stock-xgpon-activation.json').write_text(json.dumps(result, indent=2) + '\n')
    for record in records:
        print('mode', record['mode'], 'PRBS', record['prbs'], 'activation sequence matches:', record['match'])
    assert all(r['match'] for r in records), 'Native activation differs from stock CDR/MAC/laser sequence'
    for mode in (4, 5):
        assert native.run(mode, 0, 0x9c00, 1) == -110
        assert list(results)[:7] == [-110, 1, 0, -110, -110, 0, 0]
        assert not results[7]
        assert native.run(mode, 0, 0x9c01, 0) == 0
        assert results[1] == 0 and results[2] == 1 and results[5] == -110
print('Original/native activation ordering matches; failed access leaves TX disabled and returns timeout; LOS wait remains visible')
