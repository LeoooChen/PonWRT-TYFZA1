#!/usr/bin/env python3
"""Compare SU feedback retention with the board's actual RA report parser."""
from pathlib import Path
import ctypes
import json
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'pci.c').read_text()
start = source.index('\tif (message.category == 2 && message.class == 1 && message.function == 0) {')
end = source.index('\n\tif (message.category == 2 && message.class == 1 && message.function == 2)', start)
branch = source[start:end]
module = root / 'evidence/wireless-study-20261010-continuation/evidence/stock/modules/rtk_wifi6.ko'

def callback(name, args):
    if name == 'printk':
        return 0
    raise AssertionError((name, args))

oracle = ModuleOracle(module, ['halbb_get_fw_ra_rpt'], callback)
bb, phl, sta, halsta, payload = [0x6000000 + i * 0x10000 for i in range(5)]
for address in (bb, phl, sta, halsta, payload):
    oracle.uc.mem_map(address, 0x10000)
oracle.word(bb, phl)
oracle.word(bb + 0x10 + 7 * 8, sta)
oracle.word(sta + 0xa80, halsta)
oracle.uc.mem_write(bb + 0x490 + 7, b'\1')
oracle.uc.mem_write(phl + 0x58fa, b'\x36')

prefix = r"""
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
static unsigned get_unaligned_le16(const void *p) {const u8 *b=p;return b[0]|b[1]<<8;}
struct rtl_ax_ap {unsigned command_lock;u8 ra_feedback[128][8];u64 ra_reports[128];};
static struct rtl_ax_ap state;
int receive(const u8 *data,unsigned bytes) {
 struct rtl_ax_ap *radio=&state;unsigned long flags;
 struct {unsigned category,class,function,bytes;const u8 *payload;} message={2,1,0,bytes,data};
"""
suffix = r"""
 return -ENOMSG;
}
void feedback(u8 out[8]) {memcpy(out,state.ra_feedback[7],8);}
u64 count(void) {return state.ra_reports[7];}
"""
with tempfile.TemporaryDirectory(prefix='ra-feedback-', dir=root / 'artifacts') as temp:
    c, library = Path(temp) / 'test.c', Path(temp) / 'test.so'
    c.write_text(prefix + branch + suffix)
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror',
                    str(c), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library))
    native.receive.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    native.feedback.argtypes = [ctypes.c_void_p]
    native.count.restype = ctypes.c_uint64
    saved = bytes(8)
    accepted = cases = 0
    # Each valid SU report is followed by a conflicting MU report. The next
    # SU must still replace all fields, so ignoring every report cannot pass.
    for mode in range(4):
        for rate in (0, 1, 7, 11, 15, 16, 23, 27):
            for bw in range(4):
                for mu in (0, 1):
                    report = bytes([7, 0, 17 if mu else 3, 0,
                                    (rate ^ (7 if mu else 0)) | (mu << 7),
                                    mode | (3 << 2) | (bw << 5), 0, 0])
                    oracle.uc.mem_write(payload, report)
                    oracle.call('halbb_get_fw_ra_rpt', bb, 8, payload)
                    assert native.receive(report, len(report)) == 0
                    out = ctypes.create_string_buffer(8)
                    native.feedback(out)
                    if not mu:
                        saved = report
                        accepted += 1
                    original = (oracle.unpack(halsta + 0xc4, 'B')[0],
                                oracle.unpack(halsta + 0xbc, 'I')[0],
                                oracle.unpack(halsta + 0xb8, 'I')[0],
                                oracle.unpack(halsta + 0xc0, 'I')[0],
                                oracle.unpack(halsta + 0xc8, 'B')[0])
                    decoded = (out.raw[4] & 127, out.raw[5] & 3,
                               (out.raw[5] >> 2) & 7, (out.raw[5] >> 5) & 3, out.raw[2])
                    assert decoded == original and out.raw == saved, (mu, report.hex(), out.raw.hex(), original)
                    assert native.count() == accepted
                    cases += 1
    for length in range(8):
        assert native.receive(saved, length) == -22
    assert native.receive(bytes([128, 0, 0, 0, 0, 0, 0, 0]), 8) == -22
    out = ctypes.create_string_buffer(8)
    native.feedback(out)
    assert out.raw == saved and native.count() == accepted
print(json.dumps({'factory_SU_MU_sequence_cases': cases, 'malformed_boundaries': 9,
                  'SU_rate_retry_GI_BW_preserved_across_MU': True}))
