#!/usr/bin/env python3
"""Compare actual WCPU start/stop machine code and test Linux elapsed-time wait."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
native = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
registers, trace, chip_id = bytearray(65536), [], 2

def read(reg, width=4):
    value = int.from_bytes(registers[reg:reg + width], 'little')
    if reg == 0x9110:
        value &= ~(1 << 31)
    if reg == 0x9114:
        value = 0x300
    return value

def write(reg, value, width=4):
    value &= (1 << (width * 8)) - 1
    registers[reg:reg + width] = value.to_bytes(width, 'little')
    trace.append((width, reg, value))

def handler(name, args):
    if name.startswith('read'):
        return read(args[1], int(name[4:]) // 8)
    if name.startswith('write'):
        write(args[1], args[2], int(name[5:]) // 8)
    elif name == 'is_chip_id':
        return int(args[1] == chip_id)
    elif name == 'dle_dfi_qempty':
        assert oracle.unpack(args[1], '2I') == (1, 0)
        write(0x9110, (1 << 31) | (7 << 16))
        oracle.uc.mem_write(args[1] + 8, struct.pack('<I', 0x300))
    elif name not in ('reset_lps_seq_num', 'log'):
        raise AssertionError(('Unexpected original call', name))
    return 0

oracle = ModuleOracle(module, ['mac_enable_cpu', 'mac_disable_cpu', 'chk_patch_pcie_hang'], handler)
scratch = oracle.scratch
oracle.word(scratch, scratch + 0x1000)
oracle.word(scratch + 0x1000, scratch + 0x2000)
oracle.word(scratch + 24, scratch + 0x3000)
oracle.word(scratch + 88, scratch + 0x4000)
oracle.uc.mem_write(scratch + 0x4000 + 8, struct.pack('<I', 2))
for index, name in enumerate(('read8', 'write8', 'read16', 'write16', 'read32', 'write32')):
    oracle.word(scratch + 0x2000 + index * 8, oracle.callback(name))
oracle.word(scratch + 0x3000 + 176, oracle.callback('log'))

R8, R16, R32 = [C.CFUNCTYPE(t, C.c_void_p, C.c_uint) for t in (C.c_ubyte, C.c_ushort, C.c_uint)]
W8, W16, W32 = [C.CFUNCTYPE(None, C.c_void_p, C.c_uint, t) for t in (C.c_ubyte, C.c_ushort, C.c_uint)]
DELAY = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)
POLL = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.POINTER(C.c_uint))
class IO(C.Structure):
    _fields_ = [('ctx', C.c_void_p)] + list(zip(('r8', 'r16', 'r32', 'w8', 'w16', 'w32', 'delay', 'poll'),
                                             (R8, R16, R32, W8, W16, W32, DELAY, POLL)))
def poll(ctx, reg, mask, expected, delay, timeout, last):
    last[0] = read(reg)
    if reg == 0x1e0:  # Phase0 completion has no additional register write.
        last[0] |= 2
    assert last[0] & mask == expected
    return 0
callbacks = [R8(lambda c, r: read(r, 1)), R16(lambda c, r: read(r, 2)), R32(lambda c, r: read(r)),
             W8(lambda c, r, v: write(r, v, 1)), W16(lambda c, r, v: write(r, v, 2)),
             W32(lambda c, r, v: write(r, v)), DELAY(lambda c, us: None), POLL(poll)]
io = IO(None, *callbacks)
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-WCPU-') as directory:
    work = Path(directory)
    source, binary = work / 'native.c', work / 'native.so'
    source.write_text('''#include "transport.h"
int start(const struct rtl_ax_io *io){return rtl_ax_wcpu_start(io);}
void stop(const struct rtl_ax_io *io){rtl_ax_wcpu_stop(io);}
''')
    subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-I' + str(native), str(source), '-o', str(binary)], check=True)
    library = C.CDLL(str(binary))
    library.start.argtypes = library.stop.argtypes = [C.POINTER(IO)]
    rng, vectors = random.Random(0x9617c), 0
    for chip_id in (2, 3):
        oracle.uc.mem_write(scratch + 0x4000 + 1, bytes([chip_id]))
        for vector in range(128):
            initial = bytearray(rng.randbytes(65536))
            initial[0x1080:0x1084] = bytes(4)
            registers[:] = initial;trace.clear()
            assert not oracle.call('mac_disable_cpu', scratch)
            assert not oracle.call('mac_enable_cpu', scratch, 0, 1)
            expected, final = list(trace), bytes(registers)
            registers[:] = initial;trace.clear()
            assert not library.start(C.byref(io))
            assert trace == expected and bytes(registers) == final, (chip_id, vector)
            vectors += 1
            registers[:] = initial;trace.clear()
            assert not oracle.call('mac_disable_cpu', scratch)
            expected, final = list(trace), bytes(registers)
            registers[:] = initial;trace.clear();library.stop(C.byref(io))
            assert trace == expected and bytes(registers) == final
            vectors += 1

kernel = Path(subprocess.check_output(['python3', str(root / 'port/tools/project_paths.py'),
                                     '--kernel-directory', str(root / 'sources/openwrt')], text=True).strip())
polling = (kernel / 'include/linux/iopoll.h').read_text()
source = (native / 'pci.c').read_text()
def macro(name):
    lines = polling.splitlines(True)
    start = next(i for i, line in enumerate(lines) if re.match(r'#define ' + name + r'\(', line))
    end = start
    while lines[end].rstrip().endswith('\\'):
        end += 1
    return ''.join(lines[start:end + 1])
start = source.index('static int rtl_ax_wait_firmware(')
end = source.index('\n}', start) + 2
unit = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "transport.h"
typedef uint64_t u64,ktime_t;
struct rtl_ax_ap {void *bar;};
static unsigned int reads,ready_after,status;
static uint64_t time_us,oversleep;
static ktime_t ktime_get(void){return time_us;}
static ktime_t ktime_add_us(ktime_t a,u64 b){return a+b;}
static int ktime_compare(ktime_t a,ktime_t b){return (a>b)-(a<b);}
#define might_sleep_if(v) ((void)(v))
#define barrier() ((void)0)
#define cpu_relax() ((void)0)
static void usleep_range(unsigned long min,unsigned long max){time_us+=oversleep?oversleep:max;}
static unsigned char readb(void *p){reads++;return (ready_after && reads>=ready_after?status:1)<<5;}
'''
unit += ''.join(macro(n) for n in ('poll_timeout_us', 'read_poll_timeout', 'readx_poll_timeout', 'readb_poll_timeout'))
unit += source[start:end]
unit += r'''
int main(void){struct rtl_ax_ap radio={};unsigned int control;
 for(unsigned int terminal=2;terminal<=7;terminal++){
  if(terminal==5 || terminal==6)continue;
  reads=0;time_us=0;oversleep=0;ready_after=3;status=terminal;
  assert(!rtl_ax_wait_firmware(&radio,&control) && reads==3 && control==terminal<<5);
  assert(rtl_ax_fw_result(control)==(terminal==7?0:terminal==2?-EBADMSG:terminal==3?-EKEYREJECTED:-ENOEXEC));
 }
 reads=0;time_us=0;ready_after=0;oversleep=100000;
 assert(rtl_ax_wait_firmware(&radio,&control)==-ETIMEDOUT);
 assert(time_us>=400000 && time_us<=500000 && reads<=8);
 puts("Native WCPU wait:four terminal states and Linux elapsed 400ms bound under 100ms scheduler sleeps passed");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-WCPU-wait-') as directory:
    work = Path(directory)
    source, binary = work / 'wait.c', work / 'wait'
    source.write_text(unit)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', '-I' + str(native),
                    str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/radio-WCPU-control-test.json').write_text(json.dumps({
    'stock_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
    'native_transport_header_sha256': hashlib.sha256((native / 'transport.h').read_bytes()).hexdigest(),
    'native_PCI_source_sha256': hashlib.sha256((native / 'pci.c').read_bytes()).hexdigest(),
    'Linux_iopoll_header_sha256': hashlib.sha256((kernel / 'include/linux/iopoll.h').read_bytes()).hexdigest(),
    'stock_CPU_start_stop_trace_vectors': vectors,
    'original_PLATFORM_EN_reset_workaround_matches': True,
    'terminal_MCU_states': [2, 3, 4, 7], 'ready_timeout_uses_elapsed_time_us': 400000,
    'long_scheduler_sleep_model_us': 100000, 'sanitizers': ['ASan', 'UBSan'],
    'hardware_verified': False,
}, indent=2) + '\n')
print('Native WCPU control:512 actual original start/stop traces match;elapsed-time status completion verified')
