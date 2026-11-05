#!/usr/bin/env python3
"""Execute original backend ONU state changes and native serial-guarded control."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_fpga_boot import function


class Registers:
    def __init__(self, id, seed=0xfffe5678, pending=1):
        self.values = {0x40401 + 2 * id: seed, 0x40803 + 4 * id: 0x12345678,
                       0x40801 + 4 * id: 0x54455354, 0x40800 + 4 * id: 0x01234567,
                       0x40802 + 4 * id: 0x42, 0x40040: 3}
        self.events, self.pending, self.fail, self.drop = [], pending, 0, False

    def read(self, reg):
        value = self.values.get(reg, 0)
        if reg == 0x40007:
            value = 2 if self.pending else 0
            self.pending = max(0, self.pending - 1)
        self.events.append(['read', reg, value])
        return value

    def write(self, reg, value):
        self.events.append(['write', reg, value])
        if not self.drop:
            self.values[reg] = value


class Stock:
    def __init__(self, asic, relay, regs):
        self.regs = regs
        self.asic = ElfOracle(asic, self.io, ('FPGA_reg_read', 'FPGA_reg_write', 'drv_get_miniolt_chiptype'))
        self.relay = ElfOracle(relay, self.io, ('fpga_reg_read', 'fpga_reg_write',
            'plr_log', 'plr_pondrv_log_module_id_get', 'plr_timer_timeout_handle_add'))

    def io(self, name, a):
        if name == 'FPGA_reg_read':
            return self.regs.read(a[0])
        if name == 'fpga_reg_read':
            self.relay.uc.mem_write(a[1], struct.pack('<I', self.regs.read(a[0])))
        elif name in ('FPGA_reg_write', 'fpga_reg_write'):
            self.regs.write(*a[:2])
        elif name == 'SendDsPloamMsg':
            data = self.asic.scratch + 0x2000
            self.asic.uc.mem_write(data, bytes(self.relay.uc.mem_read(a[0], 12)))
            return self.asic.call(name, data, a[1])
        elif name == 'usleep':
            self.regs.events.append(['delay', a[0]])
        elif name in ('drv_get_miniolt_chiptype', 'open'):
            return 3
        elif name not in ('flock', 'close', 'pthread_mutex_lock', 'pthread_mutex_unlock',
                          'plr_log', 'plr_pondrv_log_module_id_get', 'plr_timer_timeout_handle_add'):
            raise AssertionError(('unmodeled original call', name))
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asic', type=Path, required=True)
    parser.add_argument('--relay-asic', type=Path, required=True)
    parser.add_argument('--relay-fpga', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    headers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    rd = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))
    wr = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint)
    delay = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)

    class IO(C.Structure):
        _fields_ = [('context', C.c_void_p), ('read', rd), ('write', wr), ('delay', delay)]

    serial = bytes.fromhex('5445535401234567')
    cases = []
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        source = '''#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#define BIT(n) (1U << (n))
#include "rtl9617c-fttr-onu.h"
struct rtl_fttr { struct rtl_fttr_reg_io reg_io; enum rtl_fttr_backend backend; int control_lock; };
struct device { struct rtl_fttr *fttr; };
struct device_attribute { int unused; };
static int locks;
#define dev_get_drvdata(d) ((d)->fttr)
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
static int hex2bin(unsigned char *out, const char *in, size_t size) {
 for (size_t i=0;i<size;i++) {
  unsigned int v;
  if (strspn(in+2*i,"0123456789abcdefABCDEF")<2 || sscanf(in+2*i,"%2x",&v)!=1) return -1;
  out[i]=v;
 }
 return 0;
}
int admin(const struct rtl_fttr_reg_io *io, int backend, unsigned id, int blocked, const unsigned char *serial) {
 return rtl_fttr_onu_admin(io, backend, id, blocked, serial);
}
'''
        source += function((headers / 'rtl9617c-fttr.c').read_text(), 'onu_admin_store')
        source += '''
int store(const struct rtl_fttr_reg_io *io, int backend, const char *input) {
 struct rtl_fttr f={.reg_io=*io,.backend=backend}; struct device d={&f};
 int ret=onu_admin_store(&d,0,input,strlen(input));
 return locks ? -EDEADLK : ret;
}
'''
        (folder / 'native.c').write_text(source)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-sign-compare', '-shared', '-fPIC',
                        '-I' + str(headers), str(folder / 'native.c'), '-o', str(folder / 'native.so')], check=True)
        native = C.CDLL(str(folder / 'native.so'))

        def native_io(regs):
            def read(ctx, reg, value):
                value[0] = regs.read(reg)
                return -errno.EIO if len(regs.events) == regs.fail else 0

            def write(ctx, reg, value):
                regs.write(reg, value)
                return -errno.EIO if len(regs.events) == regs.fail else 0

            return IO(None, rd(read), wr(write), delay(lambda ctx, usec: regs.events.append(['delay', usec])))

        for backend in (1, 2, 3):
            relay = args.relay_asic if backend == 3 else args.relay_fpga
            for id in (0, 5, 63):
                for blocked in (0, 1):
                    expected, actual = Registers(id), Registers(id)
                    stock = Stock(args.asic, relay, expected)
                    assert stock.relay.call('drv_gpon_onu_state_set', 0, id, 5 if blocked else 3) == 0
                    assert struct.unpack('<I', stock.relay.uc.mem_read(
                        stock.relay.symbols['g_onu_deactive_keep_flag'] + 4 * id, 4))[0] == blocked
                    io = native_io(actual)
                    assert native.admin(C.byref(io), backend, id, blocked, serial) == 0
                    # reg_io table completion is separately checked by the MDIO tests.
                    original = [e for e in expected.events if e[:2] != ['read', 0x40040]]
                    assert actual.events[5:-1] == original, (backend, actual.events, expected.events)
                    assert actual.values == expected.values
                    trace = list(actual.events)
                    for fail in range(1, len(trace) + 1):
                        if trace[fail - 1][0] == 'delay':
                            continue
                        broken = Registers(id); broken.fail = fail; io = native_io(broken)
                        assert native.admin(C.byref(io), backend, id, blocked, serial) == -errno.EIO
                        assert broken.events == trace[:fail]
                    stale = Registers(id); io = native_io(stale)
                    assert native.admin(C.byref(io), backend, id, blocked, bytes(8)) == -errno.ESTALE
                    assert all(e[0] == 'read' for e in stale.events)
                    cases.append({'backend': backend, 'id': id, 'blocked': blocked, 'original_match': True})
            stuck = Registers(5, pending=10000); io = native_io(stuck)
            assert native.admin(C.byref(io), backend, 5, 1, serial) == -errno.ETIMEDOUT
            assert len([e for e in stuck.events if e[:2] == ['read', 0x40007]]) == 2000
            assert not any(e[0] == 'write' and 0x40007 <= e[1] <= 0x4000a for e in stuck.events)
            original_timeout = Registers(5, pending=10000)
            stock = Stock(args.asic, relay, original_timeout)
            assert stock.relay.call('drv_gpon_onu_state_set', 0, 5, 5) == 0
            mailbox_writes = [e for e in original_timeout.events if e[0] == 'write' and
                              0x40007 <= e[1] <= 0x4000a]
            assert bool(mailbox_writes) == (backend != 3)
            dropped = Registers(5); dropped.drop = True; io = native_io(dropped)
            assert native.admin(C.byref(io), backend, 5, 1, serial) == -errno.EUCLEAN
            actual = Registers(5); io = native_io(actual)
            for invalid in ('64 1 ' + serial.hex(), '5 2 ' + serial.hex(), '5 1 xxxx',
                            '5 1 ' + serial.hex() + ' junk', '5 1 ' + serial.hex() + '0'):
                assert native.store(C.byref(io), backend, invalid.encode()) == -errno.EINVAL
                assert not actual.events
            command = f'5 1 {serial.hex()}\n'.encode()
            assert native.store(C.byref(io), backend, command) == len(command)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'cases': cases, 'io_failures_stop': True,
        'stale_identity_read_only': True, 'mailbox_timeout_stops': True,
        'original_timeout_false_success_reproduced': True, 'state_readback_checked': True,
        'sysfs_parser_and_lock_checked': True, 'hardware_verified': False,
        'inputs': {name: hashlib.sha256(getattr(args, name).read_bytes()).hexdigest()
                   for name in ('asic', 'relay_asic', 'relay_fpga')}}, indent=2) + '\n')
    print(f'ONU admin: {len(cases)} original state operations, serial guards, IO/timeout/readback and sysfs checks passed')


if __name__ == '__main__':
    main()
