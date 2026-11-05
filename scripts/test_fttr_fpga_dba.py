#!/usr/bin/env python3
"""Execute retained backend-specific DBA access and FPGA resource allocation."""
import argparse
import ctypes as C
import errno
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_mib import Stock as StockOmci
from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_CODE


class Stock:
    def __init__(self, binary, backend):
        self.backend, self.events, self.values = backend, [], {}
        self.oracle = ElfOracle(binary, self.io, ('FPGA_reg_read', 'FPGA_reg_write',
                                                  'drv_get_miniolt_chiptype'))

    def io(self, name, a):
        u = self.oracle.uc
        if name == 'FPGA_reg_read':
            value = self.values.get(a[0], 0)
            self.events.append(['read', a[0], value])
            return value
        if name == 'FPGA_reg_write':
            self.values[a[0]] = a[1]
            self.events.append(['write', *a[:2]])
        elif name == 'drv_get_miniolt_chiptype':
            return self.backend
        elif name == 'memset':
            u.mem_write(a[0], bytes([a[1] & 255]) * a[2])
            return a[0]
        elif name == 'open':
            return 3
        elif name not in ('flock', 'close'):
            raise AssertionError(('unmodeled original call', name))
        return 0


def relay_bandwidth(binary):
    """Execute bandwidth wrappers through their actual low-level implementations."""
    events, writes, entered = [], [], []
    def boundary(name, args):
        assert name in ('plr_log', 'plr_ponsrv_log_module_id_get'), name
        events.append(name)
        return 0
    oracle = ElfOracle(binary, boundary, ('plr_log', 'plr_ponsrv_log_module_id_get'))
    config = oracle.symbols['drv_gpon_alloc_id_config']
    clear = oracle.symbols['drv_gpon_alloc_id_clear']
    def write(uc, kind, address, size, value, unused):
        # Local stack stores are not hardware or persistent state changes.
        if not oracle.scratch + 0x1f0000 <= address < oracle.scratch + 0x200000:
            writes.append((address, size, value))
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, write)
    oracle.uc.hook_add(UC_HOOK_CODE, lambda uc, address, size, unused:
        entered.append('config' if address == config else 'clear') if address in (config, clear) else None)
    count = 0
    for id in (0, 5, 63):
        for alloc in (256 + id, 512 + id):
            for rates in ((0, 0, 0), (50, 100, 200), (65535, 65535, 65535)):
                for function, args, expected in (
                    ('drv_gpon_alloc_id_config', (0, id, alloc, *rates), ['config']),
                    ('plr_ponsrv_hw_bw_set', (1, id, alloc, *rates), ['config']),
                    ('drv_gpon_alloc_id_clear', (0, alloc), ['clear']),
                    ('plr_ponsrv_hw_bw_clear', (1, alloc), ['clear'])):
                    writes.clear(); entered.clear(); events.clear()
                    assert oracle.call(function, *args) == 0
                    assert entered == expected and not writes, (function, entered, writes)
                    if function.startswith('drv_'): assert not events
                    count += 1
    return dict(sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), calls=count,
                config_address=hex(config), clear_address=hex(clear),
                nonstack_writes=0, hardware_calls=0,
                finding='Bandwidth set/clear wrappers reach real no-op driver implementations and return success; this does not establish physical Alloc-ID binding or retirement.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--asic', type=Path, required=True)
    p.add_argument('--relay', type=Path, required=True)
    p.add_argument('--asic-relay', type=Path, help='also execute the ASIC high-level bandwidth path')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    headers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    read_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))
    write_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint)
    delay_fn = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)

    class IO(C.Structure):
        _fields_ = [('context', C.c_void_p), ('read', read_fn), ('write', write_fn), ('delay', delay_fn)]

    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        (folder / 'native.c').write_text('''#include <stdbool.h>
#include <errno.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "rtl9617c-fttr-core.h"
int program(const struct rtl_fttr_reg_io *io, int backend, unsigned id, unsigned slot,
 unsigned fixed, unsigned assured, unsigned maximum) {
 return rtl_fttr_tcont_program(io, backend, id, slot, fixed, assured, maximum);
}
int read_grant(const struct rtl_fttr_reg_io *io, int backend, unsigned id, unsigned slot,
 unsigned *fixed, unsigned *limits) {
 return rtl_fttr_tcont_read(io, backend, id, slot, fixed, limits);
}
''')
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I' + str(headers), str(folder / 'native.c'),
                        str(ROOT / 'port/packages/tyfza1-fttr/src/miniolt.c'),
                        '-o', str(folder / 'native.so')], check=True)
        native = C.CDLL(str(folder / 'native.so'))
        values, events, fail_at, cases = {}, [], 0, []

        def rd(ctx, reg, output):
            output[0] = values.get(reg, 0)
            events.append(['read', reg, output[0]])
            return -errno.EIO if len(events) == fail_at else 0

        def wr(ctx, reg, value):
            events.append(['write', reg, value]); values[reg] = value
            return -errno.EIO if len(events) == fail_at else 0

        io = IO(None, read_fn(rd), write_fn(wr), delay_fn(lambda *_: None))
        for backend in (1, 2, 3):
            stock = Stock(args.asic, backend)
            o, data = stock.oracle, stock.oracle.scratch + 0x2000
            for id in (0, 5, 63):
                for slot in ((0,) if backend == 1 else (0, 1, 7)):
                    for fixed, assured, maximum in ((0, 0, 0), (50, 100, 200), (65535, 65535, 65535)):
                        base = 0x40400 + id * 2 if backend == 1 else 0x41000 + id * 32 + slot * 4
                        initial = {base: 0x12345678, base + 1: 0xa5fe5678,
                                   0x40803 + id * 4: 1 << slot}
                        stock.values = dict(initial); stock.events.clear()
                        values.clear(); values.update(initial); events.clear()
                        o.uc.mem_write(data, struct.pack('<5H', 0, 0, fixed, assured, maximum))
                        assert o.call('drv_set_onu_tcont_dba_profile', id + 1, slot, data) == 0
                        assert native.program(C.byref(io), backend, id, slot, fixed, assured, maximum) == 0
                        assert events == stock.events and values == stock.values
                        expected = list(events)
                        for fail_at in range(1, len(expected) + 1):
                            values.clear(); values.update(initial); events.clear()
                            assert native.program(C.byref(io), backend, id, slot, fixed, assured, maximum) == -errno.EIO
                            assert events == expected[:fail_at]
                        fail_at = 0
                        values.clear(); values.update(stock.values); events.clear(); stock.events.clear()
                        assert o.call('drv_get_onu_tcont_dba_profile', id + 1, slot, data) == 0
                        profile = struct.unpack('<5H', o.uc.mem_read(data, 10))
                        f, limits = C.c_uint(), C.c_uint()
                        assert native.read_grant(C.byref(io), backend, id, slot, C.byref(f), C.byref(limits)) == 0
                        assert (f.value, limits.value) == (profile[2], profile[3] << 16 | profile[4])
                        # Native raw readback deliberately works after a grant is cleared;
                        # original diagnostic getter first requires the registration bitmap.
                        assert events == stock.events[0 if backend == 1 else 1:]
                        expected = list(events)
                        for fail_at in (1, 2):
                            events.clear()
                            assert native.read_grant(C.byref(io), backend, id, slot, C.byref(f), C.byref(limits)) == -errno.EIO
                            assert events == expected[:fail_at]
                        fail_at = 0
                        cases.append({'backend': backend, 'onu': id, 'slot': slot,
                                      'grant': [fixed, assured, maximum], 'io_match': True})
            if backend == 1:
                for slot in range(1, 8):
                    events.clear()
                    assert native.program(C.byref(io), backend, 5, slot, 1, 2, 3) == -errno.EINVAL
                    assert not events

        # Execute the full original occupancy predicate with its real per-PON bitmap.
        def relay_io(name, a):
            if name in ('plr_log', 'plr_ponsrv_log_module_id_get'):
                return 0
            raise AssertionError(('unmodeled relay call', name))

        relay = ElfOracle(args.relay, relay_io, ('plr_log', 'plr_ponsrv_log_module_id_get'))
        wire = StockOmci(args.relay)
        wire.oracle.symbols['stock_set'] = 0x62530
        relay.uc.mem_write(0x170ab8, struct.pack('<Q', relay.scratch + 0x4000))
        assert relay.call('plr_gpon_const_val_init') == 0
        assert relay.call('plr_gpon_min_allocId_get') == 256
        assert relay.call('plr_gpon_max_allocId_get') == 319
        data = relay.scratch + 0x2000
        for id in range(64):
            assert relay.call('plr_ponsrv_alloc_id_get', 1, id + 1, 1, data) == 0
            alloc = struct.unpack('<H', relay.uc.mem_read(data, 2))[0]
            body = wire.me_request(0x48, 262, 0x8765, 0x8000,
                                   struct.pack('<H', alloc) + bytes(11))
            assert body == (struct.pack('>HBBHHHH', 0x8123, 0x48, 10,
                                       262, 0x8765, 0x8000, alloc) + bytes(31) + b'\x28')
            for backend in (1,):
                assert native.fttr_miniolt_alloc_id(backend, id, 1) == alloc
                for slot in (0, 2, 7, 8):
                    assert native.fttr_miniolt_alloc_id(backend, id, slot) < 0
                    assert relay.call('plr_ponsrv_alloc_id_get', 1, id + 1, slot, data) != 0
            for slot in range(1,8):
                assert native.fttr_miniolt_alloc_id(2,id,slot)==256+id*8+slot
            occupied = relay.scratch + 0x4000 + 142 + alloc // 8
            relay.uc.mem_write(occupied, bytes([1 << (alloc % 8)]))
            assert relay.call('plr_ponsrv_alloc_id_get', 1, id + 1, 1, data) != 0
            relay.uc.mem_write(occupied, b'\0')
        # The original FPGA per-ONU OMCI setup is empty, unlike the ASIC callback.
        for id in (0, 5, 63):
            assert relay.call('drv_gpon_onu_omci_gem_port_config', 0, id) == 0

        device = folder / 'device'; device.mkdir()
        fd = os.open(device, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert native.fttr_miniolt_backend(fd) == -errno.ENOENT
            for name, backend in (('fpga1', 1), ('fpga2', 2), ('asic9690', 3)):
                (device / 'backend').write_text(name + '\n')
                assert native.fttr_miniolt_backend(fd) == backend
                (device / 'management_grant').write_text('unchanged')
                assert native.fttr_miniolt_management(fd, 5, 1) == 0
                assert (device / 'management_grant').read_text() == ('5 1\nanged' if backend != 1 else 'unchanged')
                # FPGA has no WTG peer status attribute. Its real P6 state is
                # still mandatory; missing/malformed/down states cannot pass.
                if backend != 3:
                    assert native.fttr_miniolt_links_ready(fd, fd) == -errno.ENOENT
                    for clock, link, error, expected in ((1, 1, 0, 1), (0, 1, 0, 0),
                                                       (1, 0, 0, 0), (1, 1, -5, -5)):
                        (device / 'fttr_link').write_text(
                            f'configured=1 mode=5 nway=1 clock={clock} error={error} link={link}\n')
                        assert native.fttr_miniolt_links_ready(fd, fd) == expected
                    (device / 'fttr_link').unlink()
                (device / 'tcont').write_text('')
                assert native.fttr_miniolt_tcont(fd, 5, 1, 50, 100, 200) == 0
                physical_slot = 0 if backend == 1 else 1
                assert (device / 'tcont').read_text() == f'5 {physical_slot} 50 100 200\n'
                state = bytearray(4096)
                struct.pack_into('<II', state, (5 * 8 + physical_slot) * 8, 50, 100 << 16 | 200)
                (device / 'tcont_state').write_bytes(state)
                grant = (C.c_uint * 2)()
                assert native.fttr_miniolt_tcont_read(fd, 5, 1, C.byref(grant)) == 0
                assert list(grant) == [50, 100 << 16 | 200]
            for text in ('', 'fpga9\n', 'asic9690', 'asic9690 trailing\n'):
                (device / 'backend').write_text(text)
                assert native.fttr_miniolt_backend(fd) == -errno.EPROTO
        finally:
            os.close(fd)
    report = {'asic_sha256': hashlib.sha256(args.asic.read_bytes()).hexdigest(),
              'relay_sha256': hashlib.sha256(args.relay.read_bytes()).hexdigest(), 'cases': cases,
              'allocator_cases': 64, 'relay_bandwidth': [relay_bandwidth(path) for path in
                  ([args.relay, args.asic_relay] if args.asic_relay else [args.relay])], 'scope': 'original ARM64 IO, allocator and native sysfs ABI; no physical grant delivery or traffic'}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(cases)} DBA IO/readback groups, 64 allocators with occupancy and native backend ABI')


if __name__ == '__main__':
    main()
