#!/usr/bin/env python3
"""Compare native periodic health decisions with the shipped XGTC callback."""
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
start = re.search(r'^static unsigned int rtl_xgpon_health_check\(', source, re.M).start()
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
fixture = r'''
#include <stdint.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
struct rtl9617c_pon {
 unsigned char *mac;void *optical_status;
 unsigned int xgpon_health_state,xgpon_health_grants,xgpon_health_matches,xgpon_health_no_grants;
 unsigned long long xgpon_health_fec,xgpon_health_sn;
};
static unsigned int regs[0xc000/4], optical;
static struct rtl9617c_pon pon;
static unsigned int readl(const void *p) {return *(const unsigned int*)p;}
'''
wrapper = r'''
void init(void) {
 memset(&pon,0,sizeof(pon));memset(regs,0,sizeof(regs));
 pon.mac=(void*)regs;pon.optical_status=&optical;pon.xgpon_health_state=~0U;
}
unsigned int sample(unsigned int state,unsigned int grants,unsigned int sn,
 unsigned int fec,unsigned int los,unsigned int sync) {
 regs[0x4860/4]=grants;regs[0x4c50/4]=sn;regs[0x4d98/4]=fec;
 regs[0x452c/4]=sync;optical=los;
 return rtl_xgpon_health_check(&pon,state);
}
'''
scenarios = [
    ('O5 stalled grants', [(3, 100, 0, 0, 0, 3)] * 7, 4),
    ('O5 advancing grants', [(3, 100 + i * 64, 0, 0, 0, 3) for i in range(8)], 0),
    ('O6 grace', [(4, 100, 0, 0, 0, 3)] * 8, 0),
    ('O23 no grants', [(1, 0, 0, 0, 0, 3)] * 21, 2),
    ('O23 grants without SN', [(1, i * 100, 0, 0, 0, 3) for i in range(4)], 3),
    ('O1 FEC without synchronization', [(0, 0, 0, 0x2001, 0, 0)] * 4, 1),
    ('O1 optical loss', [(0, 0, 0, 0x2001, 8, 0)] * 5, 0),
]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
records = []
with tempfile.TemporaryDirectory(prefix='xgpon-health-') as temp:
    c, lib = Path(temp) / 'check.c', Path(temp) / 'check.so'
    c.write_text(fixture + source[start:end] + wrapper)
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(lib)], check=True)
    native = ctypes.CDLL(str(lib))
    for name, samples, expected in scenarios:
        resets = []
        def callback(function, args):
            if function == 'aal_xgpon_reset':
                resets.append(args[1])
                return 0
            if function in ('ca_spin_lock', 'ca_spin_unlock', 'ca_printf',
                            '__xgpon_cfg_record_save.constprop.0', '__xgpon_disable.constprop.0',
                            '__xgpon_cfg_record_recover.constprop.0', 'aal_psds_burst_enable'):
                return 0
            raise RuntimeError((function, args))
        oracle = ModuleOracle(module, ['__xgpon_onu_state_check_timer_callback',
                              'aal_pon_optical_status_get', 'aal_xgpon_dsfp_sync_state_get'], callback)
        pon, glb = 0x6000000, 0x6100000
        for symbol, base in [('ca_venus_pon_reg_iobase', pon), ('ca_venus_pon_glb_reg_iobase', glb)]:
            oracle.uc.mem_map(base, 0x10000)
            oracle.word(oracle.symbols[symbol][0], base)
        oracle.uc.mem_write(oracle.symbols['onu_old_state'][0], struct.pack('<I', 0xffffffff))
        native.init()
        result = 0
        for sample_index, sample in enumerate(samples):
            state, grants, sn, fec, los, sync = sample
            for base, offset, value in [(pon, 0x4d30, state), (pon, 0x4860, grants),
                    (pon, 0x4c50, sn), (pon, 0x4d98, fec), (pon, 0x452c, sync), (glb, 0x90, los)]:
                oracle.uc.mem_write(base + offset, struct.pack('<I', value))
            oracle.call('__xgpon_onu_state_check_timer_callback')
            result = native.sample(*sample)
            assert bool(resets) == bool(result), (name, sample_index, resets, result)
            if result:
                assert resets == [3] and result == expected
                break
        assert result == expected, (name, result)
        records.append(dict(case=name, samples=sample_index + 1, native_reason=result,
                            original_reset_masks=resets))

output = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), records=records,
              scope='Actual original health callback and native health decision function; original optical/sync getters execute. Synthetic hardware samples. Reset orchestration, locks and logs modeled. Counter registers return seeded values; clear-on-read/PM consumer concurrency and optical recovery are not simulated.')
(root / 'logs/stock-xgpon-health.json').write_text(json.dumps(output, indent=2) + '\n')
for record in records:
    print(record)
