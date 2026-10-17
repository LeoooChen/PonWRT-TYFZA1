#!/usr/bin/env python3
"""Compare MiniOLT OMCI framing and reproduce the original extended RX drop."""
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

HOST = bytes.fromhex('020304050607')
OLT = bytes.fromhex('0a0b0c0d0e0f')


class ReceiveComplete(Exception):
    pass


class StockOmci:
    def __init__(self, binary, asic):
        self.binary, self.asic = binary, asic
        self.oracle = ElfOracle(binary, self.handler, ('fpga_reg_read', 'fpga_omci_send_msg',
            'plr_gponomci_recv_packet', 'plr_task_name_set', 'plr_pondrv_log_module_id_get', 'plr_log'))
        self.oracle.uc.mem_write(self.oracle.symbols['g_pfpacket_info'] + 0x28, HOST + OLT)
        assert self.oracle.call('plr_omci_sw_crc32_table_init') == 0
        self.data = self.oracle.scratch + 0x2000

    def handler(self, name, args):
        uc = self.oracle.uc
        if name == 'memset':
            uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
            return args[0]
        if name == 'memcpy':
            uc.mem_write(args[0], bytes(uc.mem_read(args[1], args[2])))
            return args[0]
        if name in ('htons', 'ntohs', 'htonl', 'ntohl'):
            size = 2 if name.endswith('s') else 4
            return int.from_bytes((args[0] & ((1 << (size * 8)) - 1)).to_bytes(size, 'little'), 'big')
        if name == 'fpga_reg_read':
            assert args[0] == 0x30005
            uc.mem_write(args[1], struct.pack('<I', self.mode))
        elif name == 'fpga_omci_send_msg':
            self.sent = bytes(uc.mem_read(args[0], args[1]))
        elif name == 'recvfrom':
            if self.recv_calls:
                raise ReceiveComplete()
            self.recv_calls += 1
            uc.mem_write(args[1], self.frame)
            return len(self.frame)
        elif name == 'plr_gponomci_recv_packet':
            self.received.append((args[1], bytes(uc.mem_read(args[2], args[3]))))
        elif name not in ('snprintf', 'plr_task_name_set', 'plr_pondrv_log_module_id_get',
                          'plr_log', 'fprintf'):
            raise AssertionError(('unmodeled external function', name))
        return 0

    def send(self, body, mode, onu):
        self.mode, self.sent = mode, None
        self.oracle.uc.mem_write(self.data, body)
        name = 'drv_gponomci_send_packet' if body[3] == 10 else 'drv_gponomci_extended_send_packet'
        assert self.oracle.call(name, 0, onu, self.data, len(body) + 4) == 0
        return self.sent

    def receive(self, frame):
        self.recv_calls, self.received, self.frame = 0, [], frame
        try:
            self.oracle.call('fpga_omci_msg_recv')
        except ReceiveComplete:
            pass
        return self.received

    def initialize(self, registers, writes):
        def raw_io(name, args):
            if name in ('fpga_reg_read', 'FPGA_reg_read'):
                value = registers.get(args[0], 0)
                if name == 'FPGA_reg_read':
                    return value
                current.uc.mem_write(args[1], struct.pack('<I', value))
            elif name in ('fpga_reg_write', 'FPGA_reg_write'):
                registers[args[0]] = args[1]
                writes.append(args[:2])
            elif name == 'drv_get_miniolt_chiptype':
                return 3
            elif name == 'memset':
                current.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
                return args[0]
            elif name not in ('pthread_mutex_init', 'pthread_mutex_lock', 'pthread_mutex_unlock'):
                raise AssertionError(name)
            return 0

        # The original caller and relay use two distinct shared libraries.
        current = ElfOracle(self.asic, raw_io, ('FPGA_reg_read', 'FPGA_reg_write'))
        current.uc.mem_write(current.scratch + 0x2000, HOST)
        assert current.call('drv_set_omci_dst_mac', current.scratch + 0x2000) == 0
        current = ElfOracle(self.binary, raw_io, ('fpga_reg_read', 'fpga_reg_write'))
        assert current.call('plr_fpga_init') == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', required=True, type=Path)
    parser.add_argument('--asic', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()

    class Link(ctypes.Structure):
        _fields_ = [('host_mac', ctypes.c_ubyte * 6), ('olt_mac', ctypes.c_ubyte * 6),
                    ('ethertype', ctypes.c_ushort), ('downstream_length', ctypes.c_bool)]

    class Message(ctypes.Structure):
        _fields_ = [('onu', ctypes.c_ushort), ('body', ctypes.c_void_p), ('length', ctypes.c_size_t)]

    source = root / 'port/packages/tyfza1-fttr/src/omci-wire.c'
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'registers.c').write_text('''#include <stdbool.h>
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "rtl9617c-fttr-omci.h"
int configure_link(const struct rtl_fttr_reg_io *io, const unsigned char *host) {
 return rtl_fttr_omci_init(io, host);
}
int read_link(const struct rtl_fttr_reg_io *io, struct rtl_fttr_omci_link *link) {
 return rtl_fttr_omci_link_read(io, link);
}
''')
        headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-I', str(headers), str(source), str(path / 'registers.c'),
                        '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        library.fttr_omci_pack.argtypes = [ctypes.POINTER(Link), ctypes.c_ushort, ctypes.c_char_p,
                                         ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        library.fttr_omci_unpack.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                           ctypes.c_ushort, ctypes.POINTER(Message)]
        library.fttr_omci_crc.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        library.fttr_omci_crc.restype = ctypes.c_uint
        stock = StockOmci(args.stock, args.asic)
        read_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
                                  ctypes.POINTER(ctypes.c_uint))
        write_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
        delay_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint)

        class IO(ctypes.Structure):
            _fields_ = [('context', ctypes.c_void_p), ('read', read_fn),
                        ('write', write_fn), ('delay', delay_fn)]

        library.configure_link.argtypes = [ctypes.POINTER(IO), ctypes.c_char_p]
        library.read_link.argtypes = [ctypes.POINTER(IO), ctypes.POINTER(Link)]
        for initial_type in (0x123488a8, 0x12348888):
            initial = {0x30002: 0x0c0d0e0f, 0x30003: 0x0a0b,
                       0x30004: initial_type, 0x30005: 8, 0x4000c: 0x80000000}
            registers, writes = dict(initial), []
            stock.initialize(registers, writes)
            values, actual = dict(initial), []

            def rd(context, reg, pointer):
                pointer[0] = values.get(reg, 0)
                return 0

            def wr(context, reg, value):
                values[reg] = value
                actual.append([reg, value])
                return 0

            io = IO(None, read_fn(rd), write_fn(wr), delay_fn(lambda *_: None))
            assert library.configure_link(ctypes.byref(io), HOST) == 0
            assert actual == writes and registers == values
            link = Link()
            assert library.read_link(ctypes.byref(io), ctypes.byref(link)) == 0
            assert bytes(link.host_mac) == HOST and bytes(link.olt_mac) == OLT
            assert link.ethertype == 0x8888 and link.downstream_length
        vector = b'123456789'
        stock.oracle.uc.mem_write(stock.data, vector)
        assert stock.oracle.call('plr_omci_crc32_get', stock.data, len(vector)) == 0xfc891918
        assert library.fttr_omci_crc(vector, len(vector)) == 0xfc891918

        def unpack(frame):
            message = Message()
            ret = library.fttr_omci_unpack(frame, len(frame), 0x8888, ctypes.byref(message))
            return ret, message

        for extended, body_length in ((False, 44), (True, 41), (True, 1970)):
            body = bytearray((i * 11 + 3) & 255 for i in range(body_length))
            body[3] = 11 if extended else 10
            if extended:
                body[8:10] = struct.pack('>H', len(body) - 10)
            body = bytes(body)
            for mode in (0, 8):
                expected = stock.send(body, mode, 5)
                link = Link((ctypes.c_ubyte * 6)(*HOST), (ctypes.c_ubyte * 6)(*OLT), 0x8888, bool(mode))
                output = ctypes.create_string_buffer(1996)
                size = library.fttr_omci_pack(ctypes.byref(link), 5, body, len(body), output, len(output))
                assert size == len(expected) and output.raw[:size] == expected
                cases.append({'extended': extended, 'downstream_length': bool(mode),
                              'frame_bytes': size, 'stock_native_match': True})
                assert library.fttr_omci_pack(ctypes.byref(link), 5, body, len(body), output, size - 1) == -errno.EMSGSIZE

            # Upstream envelope remains 16 bytes; exercise CRC ID zero and normal ID five.
            for onu in (0, 5):
                down = stock.send(body, 0, onu)
                frame = HOST + OLT + down[12:]
                ret, message = unpack(frame)
                assert ret == 0 and message.onu == onu
                assert ctypes.string_at(message.body, message.length) == body
                delivered = stock.receive(frame)
                if extended:
                    assert delivered == []  # actual original RX branch drops it
                else:
                    assert delivered == [(onu, down[16:])]
                assert unpack(frame[:16 + len(body) + 3])[0] == -errno.EMSGSIZE
                if onu == 0:
                    corrupt = frame[:-1] + bytes([frame[-1] ^ 1])
                    assert unpack(corrupt)[0] == -errno.EBADMSG
                    assert stock.receive(corrupt) == []

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'cases': cases, 'crc_vector': 'fc891918', 'original_extended_rx_drop_reproduced': True,
        'native_extended_rx_decoded': True, 'link_configuration_writes_match': True,
        'asic_sha256': hashlib.sha256(args.asic.read_bytes()).hexdigest(),
        'upstream_crc_policy': 'original ONU ID zero policy',
        'frame_length_and_crc_errors_rejected': True, 'hardware_verified': False}, indent=2) + '\n')
    print(f'OMCI: link register writes and {len(cases)} original/native frames match; extended RX decoded')


if __name__ == '__main__':
    main()
