#!/usr/bin/env python3
"""Compare original optical rogue decisions with the native IRQ handoff."""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-pon-xgpon.c').read_text()
start = re.search(r'^void rtl_pon_xgpon_optical_irq\(', source, re.M).start()
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define lockdep_assert_held(p) assert(*(p))
#define dev_err(...) ((void)0)
#define system_wq 0
static unsigned long jiffies;
#define jiffies_to_msecs(n) (n)
static unsigned int optical,gpio,scheduled;
struct rtl9617c_pon {
 int lock;bool xgpon_enabled,xgpon_rogue_pending;
 u32 xgpon_rogue_last_ms,xgpon_rogue_count[2];
 void *optical_control,*tx_disable,*dev;int xgpon_health_work;
};
static struct rtl9617c_pon pon;
static void gpiod_set_value_cansleep(void *p,int v){*(unsigned int*)p=v;}
static void rtl_pon_update(void *p,u32 mask,u32 v){*(unsigned int*)p=(*(unsigned int*)p&~mask)|v;}
static void mod_delayed_work(int q,int *w,int delay){(void)w;assert(q==0 && delay==0);scheduled++;}
'''
wrapper = r'''
void run(unsigned int now,unsigned int last,unsigned int events){
 memset(&pon,0,sizeof(pon));optical=0x40000010;gpio=scheduled=0;jiffies=now;
 pon.lock=1;pon.xgpon_enabled=true;pon.xgpon_rogue_last_ms=last;
 pon.optical_control=&optical;pon.tx_disable=&gpio;
 rtl_pon_xgpon_optical_irq(&pon,events);
 assert(scheduled==pon.xgpon_rogue_pending && gpio==pon.xgpon_rogue_pending);
}
unsigned int field(unsigned int i){
 unsigned int values[]={pon.xgpon_rogue_pending,pon.xgpon_rogue_count[0],pon.xgpon_rogue_count[1],optical};
 return values[i];
}
'''
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
base, ticks = 0x6000000, 0x6100000
state = {}


def callback(name, args):
    if name == '__kfifo_out':
        oracle.uc.mem_write(args[1], struct.pack('<II', state['events'], 0))
        oracle.uc.mem_write(args[0] + 4, struct.pack('<I', 1))
        return 8
    if name == 'jiffies_to_msecs':
        return state['now']
    if name == 'ca_pon_laser_tx_disable_set':
        state['laser'].append(args[1])
        return 0
    if name == 'aal_pon_mac_enable_set':
        state['mac_calls'].append(args[:4])
        return 0
    if name in ('ca_printf', 'ca_event_send'):
        return 0
    raise RuntimeError((name, args))


oracle = ModuleOracle(module, ['aal_opt_wq_proc', 'aal_pon_opt_rogue_state_set'],
                      callback, external_symbols={'jiffies': ticks})
oracle.uc.mem_map(base, 0x10000)
oracle.uc.mem_map(ticks, 0x1000)
oracle.word(oracle.symbols['ca_venus_pon_glb_reg_iobase'][0], base)
records = []
scenarios = [('first distant AT1', 2000, 0, 1 << 8),
             ('close AT1', 2500, 2000, 1 << 8),
             ('close AT0', 2500, 2000, 1 << 9),
             ('exact1000ms', 3000, 2000, 1 << 8),
             ('both AT bits', 2500, 2000, 3 << 8),
             ('LOS event precedence', 2500, 2000, (1 << 8) | 1)]
with tempfile.TemporaryDirectory(prefix='xgpon-rogue-') as temp:
    c, lib = Path(temp) / 'check.c', Path(temp) / 'check.so'
    c.write_text(fixture + source[start:end] + wrapper)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', str(c), '-o', str(lib)], check=True)
    native = ctypes.CDLL(str(lib))
    for mode in (4, 5):
        for name, now, last, events in scenarios:
            state.update(now=now, events=events, laser=[], mac_calls=[])
            for symbol, value in [('aal_pon_mac_mode', mode), ('last_time', last)]:
                oracle.uc.mem_write(oracle.symbols[symbol][0], struct.pack('<I', value))
            oracle.uc.mem_write(oracle.symbols['pon_opt_rogue_cnt'][0], bytes(8))
            oracle.uc.mem_write(oracle.symbols['aal_pon_kmsgfifo'][0], struct.pack('<II', 1, 0))
            oracle.uc.mem_write(base + 0x80, struct.pack('<I', 0x40000010))
            oracle.call('aal_opt_wq_proc')
            native.run(now, last, events)
            assert native.field(0) == bool(state['laser']), (name, state)
            assert state['laser'] in ([], [1])
            assert state['mac_calls'] == ([[0, 0, 0, 1]] if state['laser'] else [])
            count = oracle.unpack(oracle.symbols['pon_opt_rogue_cnt'][0], 'II')
            assert count == (native.field(1), native.field(2))
            assert oracle.unpack(base + 0x80, 'I')[0] == native.field(3)
            records.append(dict(mode=mode, scenario=name, force_o7=bool(native.field(0)),
                                rogue_count=list(count), optical_control=native.field(3)))
result = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), records=records,
              scope='Original optical worker and rearm instructions execute directly; FIFO, '
                    'time, event publication and MAC-enable body are modeled. Native IRQ '
                    'decision/rearm is compared here; the actual deferred force-O7 controller '
                    'is exercised by check_xgpon_warm_recovery.py. No optical signal injection.')
(root / 'logs/stock-xgpon-rogue.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
