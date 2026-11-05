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
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_PC
from unicorn import UC_HOOK_CODE


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
        self.timer, self.software_state = None, 0
        self.asic = ElfOracle(asic, self.io, ('FPGA_reg_read', 'FPGA_reg_write', 'drv_get_miniolt_chiptype'))
        self.relay = ElfOracle(relay, self.io, ('fpga_reg_read', 'fpga_reg_write',
            'plr_log', 'plr_pondrv_log_module_id_get', 'plr_timer_timeout_handle_add', 'drv_gpon_fpga_state_get'))

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
        elif name == 'drv_gpon_fpga_state_get':
            return self.software_state
        elif name == 'plr_timer_timeout_handle_add':
            self.timer = (a[0], a[2], bytes(self.relay.uc.mem_read(a[3], a[4])))
        elif name in ('drv_get_miniolt_chiptype', 'open'):
            return 3
        elif name not in ('flock', 'close', 'pthread_mutex_lock', 'pthread_mutex_unlock',
                          'plr_log', 'plr_pondrv_log_module_id_get', 'plr_timer_timeout_handle_add', 'printf'):
            raise AssertionError(('unmodeled original call', name))
        return 0

    def offline_unblock(self, backend, id, downstream, keep):
        """Run the actual offline decision block, with explicit current state."""
        oracle = self.relay
        oracle.uc.mem_write(oracle.symbols['g_onu_deactive_keep_flag'] + 4 * id, struct.pack('<I', keep))
        stack = oracle.scratch + 0x1ff000
        oracle.uc.reg_write(UC_ARM64_REG_SP, stack)
        if backend != 1:
            # ONU_Offline has already fetched the downstream state at this boundary.
            oracle.uc.mem_write(stack + 60, struct.pack('<II', downstream, id))
            start, end = 0x48acc, 0x48c10
        else:
            oracle.uc.mem_write(stack + 20, struct.pack('<I', id))
            self.regs.values[0x40401 + 2 * id] = downstream
            self.software_state = 2 if downstream & (1 << 18) else 0
            start, end = 0x47ac0, 0x47cd0
        oracle.uc.emu_start(start, end, count=20000)
        assert oracle.uc.reg_read(UC_ARM64_REG_PC) == end

    def recover(self, backend, id):
        oracle = self.relay
        stack = oracle.scratch + 0x1ff000
        oracle.uc.reg_write(UC_ARM64_REG_SP, stack)
        oracle.uc.mem_write(stack + (64 if backend != 1 else 20), struct.pack('<I', id))
        start = 0x48914 if backend != 1 else 0x479bc
        stops = (0x489ec,) if backend != 1 else (0x47ab0, 0x47ac0, 0x47cd0)
        handle = oracle.uc.hook_add(UC_HOOK_CODE,
            lambda uc, pc, size, data: uc.emu_stop() if pc in stops else None)
        try:
            oracle.uc.emu_start(start, 0, count=400000)
            assert oracle.uc.reg_read(UC_ARM64_REG_PC) in stops
        finally:
            oracle.uc.hook_del(handle)


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
    cases, release_cases, recovery_cases = [], [], []
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        source = '''#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#define BIT(n) (1U << (n))
#include "rtl9617c-fttr-onu.h"
#include "miniolt.h"
int kernel_needed(int backend, unsigned int upstream) {return rtl_fttr_onu_recovery_needed(backend, upstream);}
int userspace_needed(int backend, unsigned int upstream) {return fttr_miniolt_recovery_needed(backend, upstream);}
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
                        '-I' + str(headers), '-I' + str(ROOT/'port/packages/tyfza1-fttr/src'),
                        str(folder / 'native.c'), '-o', str(folder / 'native.so')], check=True)
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
            relay = args.relay_asic if backend != 1 else args.relay_fpga
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
            assert bool(mailbox_writes) == (backend == 1)
            dropped = Registers(5); dropped.drop = True; io = native_io(dropped)
            assert native.admin(C.byref(io), backend, 5, 1, serial) == -errno.EUCLEAN
            actual = Registers(5); io = native_io(actual)
            for invalid in ('64 1 ' + serial.hex(), '5 4 ' + serial.hex(), '5 1 xxxx',
                            '5 1 ' + serial.hex() + ' junk', '5 1 ' + serial.hex() + '0'):
                assert native.store(C.byref(io), backend, invalid.encode()) == -errno.EINVAL
                assert not actual.events
            command = f'5 1 {serial.hex()}\n'.encode()
            assert native.store(C.byref(io), backend, command) == len(command)
            # Verify why permanent administration must retain the block even
            # after the original offline scanner or ASIC 15s callback runs.
            for downstream in (0, 0x10000, 0x40000, 0x50000):
                for keep in (0, 1):
                    regs = Registers(5); regs.values[0x4040b] = downstream
                    stock = Stock(args.asic, relay, regs)
                    stock.offline_unblock(backend, 5, downstream, keep)
                    writes = [e for e in regs.events if e[0] == 'write']
                    cleared = bool(downstream & 0x10000) and not keep and (backend != 1 or not downstream & 0x40000)
                    assert writes == ([['write', 0x4040b, downstream & ~0x10000]] if cleared else [])
                    if not keep:
                        actual = Registers(5, seed=downstream); io = native_io(actual)
                        result = native.admin(C.byref(io), backend, 5, 2, serial)
                        assert result == (-errno.EAGAIN if backend == 1 and downstream == 0x50000 else 0)
                        assert [e for e in actual.events if e[0] == 'write'] == writes
                        assert all(not 0x40007 <= e[1] <= 0x4000a for e in actual.events)
                    release_cases.append({'backend': backend, 'downstream': downstream, 'keep': keep,
                                          'offline_clears_block': cleared})
            # Temporary FPGA release must keep grant/status bits, whereas
            # explicit resume writes0. Identity and every IO error still stop.
            actual = Registers(5, seed=0xa5fb5678); io = native_io(actual)
            assert native.admin(C.byref(io), backend, 5, 2, serial) == 0
            assert actual.values[0x4040b] == 0xa5fa5678
            trace = list(actual.events)
            for fail in range(1, len(trace) + 1):
                broken = Registers(5, seed=0xa5fb5678); broken.fail = fail; io = native_io(broken)
                assert native.admin(C.byref(io), backend, 5, 2, serial) == -errno.EIO
                assert broken.events == trace[:fail]
            stale = Registers(5, seed=0x10000); io = native_io(stale)
            assert native.admin(C.byref(io), backend, 5, 2, bytes(8)) == -errno.ESTALE
            assert all(e[0] == 'read' for e in stale.events)
            for state in (4, 5):
                regs = Registers(5); stock = Stock(args.asic, relay, regs)
                assert stock.relay.call('drv_gpon_onu_state_set', 0, 5, state) == 0
                if backend != 1:
                    seconds, callback, payload = stock.timer
                    assert (seconds, callback, payload) == (15, 0x4775c, b'\x05')
                    regs.events.clear(); data = stock.relay.scratch + 0x2000
                    stock.relay.uc.mem_write(data, payload)
                    stock.relay.symbols['unblock_callback'] = callback
                    stock.relay.call('unblock_callback', data)
                    assert bool([e for e in regs.events if e[0] == 'write']) == (state == 4)
                else:
                    assert stock.timer is None
            regs = Registers(5, seed=0x40000)
            stock = Stock(args.asic, relay, regs)
            for high in (0, 0x40000000, 0x80000000, 0xc0000000):
                for bits in range(32):
                    upstream = high | (bits << 20) | 0x12345
                    expected = Registers(5, seed=0x40000)
                    expected.values[0x40817] = upstream
                    stock.regs = expected
                    stock.recover(backend, 5)
                    triggered = any(e[0]=='write' and e[1]==0x4040b for e in expected.events)
                    assert native.kernel_needed(backend, upstream) == triggered
                    assert native.userspace_needed(backend, upstream) == triggered
                    actual = Registers(5, seed=0x40000); actual.values[0x40817] = upstream
                    io = native_io(actual)
                    assert native.admin(C.byref(io), backend, 5, 3, serial) == (0 if triggered else -errno.EAGAIN)
                    if triggered:
                        original = [e for e in expected.events if e[:2]!=['read',0x40040]]
                        assert actual.events[5:-1] == original[1:], (backend, upstream, actual.events, original)
                        assert actual.values == expected.values
                        if not high and bits == 1:
                            trace=list(actual.events)
                            for fail in range(1,len(trace)+1):
                                if trace[fail-1][0]=='delay':continue
                                broken=Registers(5,seed=0x40000);broken.values[0x40817]=upstream;broken.fail=fail
                                io=native_io(broken)
                                assert native.admin(C.byref(io),backend,5,3,serial)==-errno.EIO
                                assert broken.events==trace[:fail]
                    else:
                        assert all(e[0]=='read' for e in actual.events)
                    recovery_cases.append({'backend':backend,'upstream':upstream,'recovery':triggered})
            for blocked in (False,True):
                actual=Registers(5,seed=0x50000 if blocked else 0x40000)
                actual.values[0x40817]=0x100001;io=native_io(actual)
                assert native.admin(C.byref(io),backend,5,3,serial if blocked else bytes(8))==(-errno.EAGAIN if blocked else -errno.ESTALE)
                assert all(e[0]=='read' for e in actual.events)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'cases': cases, 'original_offline_release_cases': release_cases,
        'upstream_recovery_cases': recovery_cases,
        'original_asic_timer_and_keep_flag_checked': True, 'io_failures_stop': True,
        'stale_identity_read_only': True, 'mailbox_timeout_stops': True,
        'original_timeout_false_success_reproduced': True, 'state_readback_checked': True,
        'sysfs_parser_and_lock_checked': True, 'hardware_verified': False,
        'inputs': {name: hashlib.sha256(getattr(args, name).read_bytes()).hexdigest()
                   for name in ('asic', 'relay_asic', 'relay_fpga')}}, indent=2) + '\n')
    print(f'ONU admin: {len(cases)} state operations, {len(recovery_cases)} original upstream recovery cases, serial/IO/timeout/readback/sysfs checks passed')


if __name__ == '__main__':
    main()
