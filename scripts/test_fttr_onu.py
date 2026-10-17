#!/usr/bin/env python3
"""Compare native ONU table, block control and PLOAM mailbox with original ARM64."""
import argparse
import ctypes
import errno
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_elf_oracle import ElfOracle


class Registers:
    def __init__(self, values=None, pending=0, stuck=False, fail_reg=None):
        self.values = dict(values or {})
        self.pending, self.stuck, self.fail_reg = pending, stuck, fail_reg
        self.events = []

    def read(self, reg):
        value = self.values.get(reg, 0)
        if reg == 0x40007:
            value = 2 if self.stuck or self.pending else 0
            if self.pending:
                self.pending -= 1
        self.events.append(['read', reg, value])
        return value

    def write(self, reg, value):
        self.events.append(['write', reg, value])
        self.values[reg] = value
        if reg == 0x40007:
            self.pending = int(value == 1)


class Stock:
    def __init__(self, asic, relay, registers):
        self.registers = registers
        overrides = ('FPGA_reg_read', 'FPGA_reg_write', 'drv_get_miniolt_chiptype')
        self.asic = ElfOracle(asic, self.handler, overrides)
        overrides = ('fpga_reg_read', 'fpga_reg_write', 'plr_log',
                     'plr_pondrv_log_module_id_get', 'plr_timer_timeout_handle_add')
        self.relay = ElfOracle(relay, self.handler, overrides)
        # Two local functions reached by the exported ONU_Online/state entry points.
        self.relay.symbols['stock_serial_read'] = 0x47104
        self.relay.symbols['stock_block'] = 0x4788c

    def handler(self, name, args):
        if name == 'FPGA_reg_read':
            return self.registers.read(args[0])
        if name == 'fpga_reg_read':
            self.relay.uc.mem_write(args[1], struct.pack('<I', self.registers.read(args[0])))
        elif name in ('FPGA_reg_write', 'fpga_reg_write'):
            self.registers.write(*args[:2])
        elif name in ('drv_get_miniolt_chiptype', 'open'):
            return 3
        elif name not in ('flock', 'close', 'pthread_mutex_lock', 'pthread_mutex_unlock',
                          'plr_log', 'plr_pondrv_log_module_id_get', 'plr_timer_timeout_handle_add'):
            raise AssertionError(('unmodeled external call', name))
        return 0

    def snapshot(self, id):
        asic, relay = self.asic, self.relay
        output = asic.scratch + 0x2000
        asic.call('drv_get_onu_ds_state_whole', id + 1, output)
        downstream = struct.unpack('<I', asic.uc.mem_read(output, 4))[0]
        asic.call('drv_get_onu_us_state_whole', id + 1, output)
        upstream = struct.unpack('<I', asic.uc.mem_read(output, 4))[0]
        sn = relay.scratch + 0x2000
        assert relay.call('stock_serial_read', 0, id, sn, sn + 16) == 0
        serial = bytes(relay.uc.mem_read(sn, 8))
        asic.call('drv_get_onu_eqd', id + 1, output)
        eqd = struct.unpack('<I', asic.uc.mem_read(output, 4))[0]
        return downstream, upstream, eqd, serial

    def send(self, words, repeat):
        address = self.asic.scratch + 0x2000
        self.asic.uc.mem_write(address, struct.pack('<III', *words))
        return ctypes.c_int(self.asic.call('SendDsPloamMsg', address, repeat)).value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asic', type=Path, required=True)
    parser.add_argument('--relay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    read_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.POINTER(ctypes.c_uint))
    write_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
    delay_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint)

    class IO(ctypes.Structure):
        _fields_ = [('context', ctypes.c_void_p), ('read', read_fn),
                    ('write', write_fn), ('delay', delay_fn)]

    class Onu(ctypes.Structure):
        _fields_ = [('downstream', ctypes.c_uint), ('upstream', ctypes.c_uint),
                    ('eqd', ctypes.c_uint), ('serial', ctypes.c_ubyte * 8)]

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text('''#include <stdbool.h>
#include <errno.h>
#define BIT(n) (1U << (n))
#include "rtl9617c-fttr-onu.h"
int snapshot(const struct rtl_fttr_reg_io *io, unsigned int id, struct rtl_fttr_onu *onu) {
 return rtl_fttr_onu_read(io, id, onu);
}
int block(const struct rtl_fttr_reg_io *io, unsigned int id, int blocked) {
 return rtl_fttr_onu_block(io, id, blocked);
}
int send_ploam(const struct rtl_fttr_reg_io *io, const unsigned int *words, unsigned int repeat) {
 return rtl_fttr_ploam_send(io, words, repeat);
}
''')
        headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I', str(headers), str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        library.snapshot.argtypes = [ctypes.POINTER(IO), ctypes.c_uint, ctypes.POINTER(Onu)]
        library.block.argtypes = [ctypes.POINTER(IO), ctypes.c_uint, ctypes.c_int]
        library.send_ploam.argtypes = [ctypes.POINTER(IO), ctypes.POINTER(ctypes.c_uint), ctypes.c_uint]

        def native_io(registers):
            def rd(context, reg, pointer):
                pointer[0] = registers.read(reg)
                return -errno.EIO if reg == registers.fail_reg else 0

            def wr(context, reg, value):
                registers.write(reg, value)
                return -errno.EIO if reg == registers.fail_reg else 0

            return IO(None, read_fn(rd), write_fn(wr), delay_fn(lambda *_: None))

        cases = []
        for id in (0, 5, 63):
            values = {0x40401 + 2 * id: 0x00fc0000, 0x40803 + 4 * id: 0xa5abcdef,
                      0x40801 + 4 * id: 0x54455354, 0x40800 + 4 * id: 0x01234567,
                      0x40802 + 4 * id: 0x9876}
            expected, actual = Registers(values), Registers(values)
            result = Stock(args.asic, args.relay, expected).snapshot(id)
            io, onu = native_io(actual), Onu()
            assert library.snapshot(ctypes.byref(io), id, ctypes.byref(onu)) == 0
            assert result == (onu.downstream, onu.upstream, onu.eqd, bytes(onu.serial))
            assert actual.events == expected.events
            cases.append({'operation': 'snapshot', 'id': id, 'stock_native_match': True})
        for seed in (0, 0xffffffff):
            for blocked in (0, 1):
                expected, actual = Registers({0x4040b: seed}), Registers({0x4040b: seed})
                stock = Stock(args.asic, args.relay, expected)
                assert stock.relay.call('stock_block', 5, blocked) == 0
                io = native_io(actual)
                assert library.block(ctypes.byref(io), 5, blocked) == 0
                assert actual.events == expected.events
                cases.append({'operation': 'block', 'blocked': blocked, 'seed': hex(seed),
                              'stock_native_register_match': True})
        words = (0x05050000, 0x01234567, 0x89abcdef)
        data = (ctypes.c_uint * 3)(*words)
        for repeat in (1, 3):
            expected, actual = Registers(pending=1), Registers(pending=1)
            assert Stock(args.asic, args.relay, expected).send(words, repeat) == 0
            io = native_io(actual)
            assert library.send_ploam(ctypes.byref(io), data, repeat) == 0
            assert actual.events == expected.events
            cases.append({'operation': 'ploam', 'repeat': repeat, 'stock_native_match': True})
        # The original checks its budget before accepting the final ready read.
        expected, actual = Registers(pending=1999), Registers(pending=1999)
        assert Stock(args.asic, args.relay, expected).send(words, 1) == -1
        io = native_io(actual)
        assert library.send_ploam(ctypes.byref(io), data, 1) == 0
        stuck = Registers(stuck=True)
        io = native_io(stuck)
        assert library.send_ploam(ctypes.byref(io), data, 1) == -errno.ETIMEDOUT
        assert len(stuck.events) == 2000 and all(e[0] == 'read' for e in stuck.events)
        broken = Registers(fail_reg=0x40009)
        io = native_io(broken)
        assert library.send_ploam(ctypes.byref(io), data, 1) == -errno.EIO
        assert broken.events[-1][:2] == ['write', 0x40009]
        broken = Registers(fail_reg=0x40803)
        io, onu = native_io(broken), Onu()
        assert library.snapshot(ctypes.byref(io), 0, ctypes.byref(onu)) == -errno.EIO
        assert len(broken.events) == 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'asic_sha256': hashlib.sha256(args.asic.read_bytes()).hexdigest(),
        'relay_sha256': hashlib.sha256(args.relay.read_bytes()).hexdigest(), 'cases': cases,
        'final_poll_ready_bug_reproduced_and_fixed': True, 'mailbox_timeout_stops': True,
        'register_error_stops': True, 'native_block_lifetime': 'explicit controller policy; no original 15-second timer',
        'hardware_verified': False}, indent=2) + '\n')
    print(f'ONU: {len(cases)} original/native comparisons passed; mailbox boundary/error checks passed')


if __name__ == '__main__':
    main()
