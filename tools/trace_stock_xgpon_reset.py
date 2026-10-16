#!/usr/bin/env python3
"""Compare XGTC cold-start reset writes and waits with the board's original init."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
stages = {'aal_psds_init', 'aal_xgpon_xgpn_glb_ctrl_set', 'aal_xgpn_activation_fsm_init',
          'xgpon_ploam_key_exchange_fsm_init', '__xgpon_dsfp_init.constprop.0',
          '__xgpon_ploam_init', '__xgpon_bwmp_init', 'aal_xgpon_actp_init',
          '__xgpon_usbg_init.constprop.0', '__xgpon_datapath_init', 'aal_xgpon_intr_init'}
records = []
for mode in (4, 5):
    events = []

    def callback(name, args):
        if name == 'ca_mdelay':
            events.append([1, 0, args[0]])
        elif name == 'memset':
            oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
            return args[0]
        elif name == 'memcpy':
            oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
            return args[0]
        elif name not in stages | {'ca_printf', 'printk', 'ca_spin_lock_init'}:
            raise RuntimeError((name, args))
        return 0

    oracle = ModuleOracle(module, ['aal_xgpon_init'], callback)
    for symbol, base in [('ca_venus_pon_reg_iobase', 0x6000000),
                         ('ca_venus_pon_glb_reg_iobase', 0x6100000)]:
        oracle.uc.mem_map(base, 0x10000)
        oracle.word(oracle.symbols[symbol][0], base)
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE,
                      lambda uc, access, at, size, value, data: events.append([0, at & 65535, value]),
                      begin=0x6100000, end=0x610ffff)
    assert oracle.call('aal_xgpon_init', 0, mode) == 0
    records.append(dict(mode=mode, stock_events=events))

source = (drivers / 'rtl9617c-pon-xgpon.c').read_text()
start = re.search(r'^int rtl_pon_xgpon_prepare_locked\(', source, re.M).start()
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1

fixture = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-xgpon-hw.h"
typedef uint8_t u8;
struct rtl9617c_pon {
 int mode, lock, control_lock, mac_error;
 bool mac_prepared, serdes_configured, xgpon_identity_configured;
 bool puc_configured, pdc_configured, traffic_enabled, xgpon_pending_valid;
 u8 xgpon_serial[8], xgpon_registration[36];
 void *dev, *control, *gpon_control;
 unsigned char *mac;
};
#define lockdep_assert_held(p) ((void)(p))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define dev_info(...) ((void)0)
static unsigned int globals[2], registers[0x10000/4], event_count;
static void event(unsigned int kind,unsigned int address,unsigned int value) {
 printf("%s[%u,%u,%u]",event_count++?",":"",kind,address,value);
}
static void writel(unsigned int value,void *address) {
 *(unsigned int *)address=value;
 if(address==&globals[0])event(0,0x94,value);
 if(address==&globals[1])event(0,0x7c,value);
}
static void msleep(unsigned int delay) { event(1,0,delay); }
static int rtl_pon_stop_locked(struct rtl9617c_pon *pon) { (void)pon;return 0; }
static unsigned int rtl_xgpon_read(void *context,unsigned int address) {
 return *(unsigned int *)(((struct rtl9617c_pon *)context)->mac+address);
}
static void rtl_xgpon_write(void *context,unsigned int address,unsigned int value) {
 writel(value,((struct rtl9617c_pon *)context)->mac+address);
}
static int rtl_xgpon_indirect(void *context,unsigned int address,unsigned int value) {
 (void)context;(void)address;(void)value;return 0;
}
'''
cases = r'''
int main(void) {
 for(unsigned int mode=RTL_PON_XGPON;mode<=RTL_PON_XGSPON;mode++) {
  struct rtl9617c_pon pon={.mode=mode,.serdes_configured=true,.xgpon_identity_configured=true,
   .control=&globals[0],.gpon_control=&globals[1],.mac=(void *)registers};
  memset(registers,0,sizeof(registers));event_count=0;
  printf("[");assert(!rtl_pon_xgpon_prepare_locked(&pon));puts("]");
  assert(pon.mac_prepared);
 }
}
'''
with tempfile.TemporaryDirectory(prefix='xgpon-reset-') as directory:
    work = Path(directory)
    (work / 'native.c').write_text(fixture + source[start:end] + cases)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-I', str(drivers),
                    str(work / 'native.c'), '-o', str(work / 'native')], check=True)
    output = subprocess.check_output([str(work / 'native')], text=True).splitlines()
    for record, line in zip(records, output, strict=True):
        record['native_events'] = json.loads(line)

result = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), records=records,
              event_format='[0, global_offset, value] or [1, 0, requested_delay_ms]',
              scope='Actual original aal_xgpon_init instructions and native prepare function. '
                    'Original child initializers, native stop and indirect table completion are modeled. '
                    'Compare reset ordering and requested waits, not elapsed silicon timing or optical operation.')
(root / 'logs/stock-xgpon-reset-timing.json').write_text(json.dumps(result, indent=2) + '\n')
for record in records:
    print('mode', record['mode'], 'stock waits', [e[2] for e in record['stock_events'] if e[0]],
          'native waits', [e[2] for e in record['native_events'] if e[0]])
assert all(r['stock_events'] == r['native_events'] for r in records), 'XGTC cold-start reset sequence differs'
print('XG-PON/XGS-PON cold-start: native reset writes and waits match the board original init')
