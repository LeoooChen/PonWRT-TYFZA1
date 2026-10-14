#!/usr/bin/env python3
"""Compare native ITU control IO with TYFZA1's original ARM64 functions."""
import ctypes
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
registers = 0x6000000
names = ['aal_gpon_vendor_id_set', 'aal_gpon_vssn_set', 'aal_gpon_passwd_set',
         'aal_gpon_mac_onu_cfg_set', 'aal_gpon_active_set', 'aal_gpon_ds_ploam_msg_get',
         'aal_gpon_aes_key_set',
         'aal_gpon_send_ploam_us', 'aal_ploam_txbuf_data_set', 'aal_ploam_rxbuf_get',
         'aal_ploam_txbuf_ctrl_set', '__xgpon_ploam_isr',
         'aal_gpon_ds_frame_thrsd_set', 'aal_gpon_tcont_set',
         'aal_gpon_ds_gem_port_set', 'aal_gpon_us_gem_port_set',
         'aal_xgpon_bwmp_tcont_set', 'aal_xgpon_dsfp_xgem_set',
         'aal_xgpon_usbg_xgem_cfg_set']
events = []


def callback(name, args):
    if name in ('ca_spin_lock', 'ca_spin_unlock', 'ca_printf', 'printk',
                '__tasklet_schedule', 'queue_work_on', 'queue_work'):
        return 0
    if name == 'ca_mdelay':
        oracle.uc.mem_write(registers + 0x60e0, bytes(4))
        return 0
    if name == 'memset':
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name == 'memcpy':
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    if name in ('aal_pon_kmsg_send', '__kfifo_in'):
        events.append(bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[2] if name == '__kfifo_in' else 0
    raise RuntimeError((name, args))


oracle = ModuleOracle(root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko', names, callback)
oracle.uc.mem_map(registers, 0x10000)
oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0], registers)
stock_writes = []
oracle.uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, address, size, value, data:
                  stock_writes.append((address - registers, value)),
                  begin=registers, end=registers + 0xffff)
def complete_indirect(uc, access, address, size, value, data):
    if address in (registers + 0x612c, registers + 0x6134, registers + 0x6170,
                   registers + 0x4838, registers + 0x4618):
        word = struct.unpack('<I', uc.mem_read(address, 4))[0]
        uc.mem_write(address, struct.pack('<I', word & 0x7fffffff))

oracle.uc.hook_add(UC_HOOK_MEM_READ, complete_indirect,
                  begin=registers, end=registers + 0xffff)
memory, writes = {}, []
read_fn = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
write_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)
indirect_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)


class IO(ctypes.Structure):
    _fields_ = [('context', ctypes.c_void_p), ('read', read_fn),
                ('write', write_fn), ('indirect', indirect_fn)]


@read_fn
def read(context, address):
    return memory.get(address, 0)


@write_fn
def write(context, address, value):
    writes.append((address, value))
    memory[address] = value


@indirect_fn
def indirect(context, address, value):
    write(context, address, value)
    return 0


io = IO(None, read, write, indirect)
source = r'''
#include "rtl9617c-gpon-hw.h"
#include "rtl9617c-xgpon-hw.h"
#include "rtl9617c-pon-omci-format.h"
void next_key(const struct rtl_dq_io *io, const unsigned char *key) { rtl_gpon_next_key(io, key); }
unsigned int omci_crc(const unsigned char *data, unsigned int length) { return rtl_omci_gpon_crc(data, length); }
void identity(const struct rtl_dq_io *io, const unsigned char *serial, const unsigned char *password)
{ rtl_gpon_identity(io, serial, password); }
void gpon_tx(const struct rtl_dq_io *io, unsigned char type, const unsigned char *data)
{ rtl_gpon_ploam_transmit(io, type, data); }
unsigned int gpon_rx(const struct rtl_dq_io *io, unsigned char *data)
{ return rtl_gpon_ploam_receive(io, data); }
void xgpon_tx(const struct rtl_dq_io *io, const unsigned char *data, int defkey)
{ rtl_xgpon_ploam_transmit(io, data, defkey); }
void xgpon_rx(const struct rtl_dq_io *io, unsigned int buffer, unsigned char *data)
{ rtl_xgpon_ploam_receive(io, buffer, data); }
unsigned int order(unsigned int status0, unsigned int status1, unsigned char *data)
{ return rtl_xgpon_ploam_order(status0, status1, data); }
void parameters(const struct rtl_dq_io *io) { rtl_gpon_mac_parameters(io); }
void active(const struct rtl_dq_io *io, int enabled) { rtl_gpon_active(io, enabled); }
int tcont(const struct rtl_dq_io *io, unsigned int alloc_id, unsigned int index, int ploam, int omci, int software)
{ return rtl_gpon_tcont_set(io, alloc_id, index, ploam, omci, software); }
int ds_gem(const struct rtl_dq_io *io, unsigned int gem_id, unsigned int index, int valid, int aes, int software)
{ return rtl_gpon_downstream_gem_set(io, gem_id, index, valid, aes, software); }
int us_gem(const struct rtl_dq_io *io, unsigned int index, unsigned int gem_id)
{ return rtl_gpon_upstream_gem_set(io, index, gem_id); }
int xg_tcont(const struct rtl_dq_io *io, unsigned int index, unsigned int alloc_id, int ploam, int omci)
{ return rtl_xgpon_tcont_set(io, index, alloc_id, ploam, omci); }
void xg_ds_gem(const struct rtl_dq_io *io, unsigned int index, unsigned int gem_id, int valid, int multicast)
{ rtl_xgpon_downstream_gem_set(io, index, gem_id, valid, multicast); }
int xg_us_gem(const struct rtl_dq_io *io, unsigned int index, unsigned int gem_id, int valid, int aes, int multicast)
{ return rtl_xgpon_upstream_gem_set(io, index, gem_id, valid, aes, multicast); }
'''


def initialise(values):
    memory.clear()
    memory.update(values)
    oracle.uc.mem_write(registers, bytes(0x10000))
    for address, value in values.items():
        oracle.uc.mem_write(registers + address, struct.pack('<I', value))
    writes.clear()
    stock_writes.clear()
    events.clear()


with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='itu-contract-') as work:
    work = Path(work)
    (work / 'native.c').write_text(source)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    '-I' + str(drivers), str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    signatures = {
        'identity': [ctypes.c_void_p, ctypes.c_void_p],
        'gpon_tx': [ctypes.c_uint, ctypes.c_void_p], 'gpon_rx': [ctypes.c_void_p],
        'xgpon_tx': [ctypes.c_void_p, ctypes.c_int], 'xgpon_rx': [ctypes.c_uint, ctypes.c_void_p],
        'parameters': [], 'active': [ctypes.c_int],
        'next_key': [ctypes.c_void_p],
        'tcont': [ctypes.c_uint] * 5, 'ds_gem': [ctypes.c_uint] * 5,
        'us_gem': [ctypes.c_uint] * 2,
        'xg_tcont': [ctypes.c_uint] * 4, 'xg_ds_gem': [ctypes.c_uint] * 4,
        'xg_us_gem': [ctypes.c_uint] * 5,
    }
    for name, arguments in signatures.items():
        getattr(native, name).argtypes = [ctypes.POINTER(IO)] + arguments
    native.order.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    native.omci_crc.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    native.omci_crc.restype = ctypes.c_uint
    key = bytes(range(16))
    for pointer in (0, 1):
        for index in (0, 253, 254, 255):
            initialise({0x6078: (pointer << 24) | (1 << 25),
                        0x602c: (index << 16) | (index << 24) | 1})
            oracle.uc.mem_write(oracle.scratch, key)
            assert oracle.call('aal_gpon_aes_key_set', 0, oracle.scratch) == 0
            native.next_key(ctypes.byref(io), key)
            assert writes == stock_writes, ('GPON next key', pointer, index, writes, stock_writes)
    crc_oracle = ModuleOracle(root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-rtk.ko',
                             ['_rtl8277c_gpon_omci_gen_crc'], callback)
    for length in (14, 44, 76, 1976):
        data = bytes((i * 37 + 11) & 255 for i in range(length))
        crc_oracle.uc.mem_write(crc_oracle.scratch, data)
        original = crc_oracle.call('_rtl8277c_gpon_omci_gen_crc', crc_oracle.scratch, length)
        actual = native.omci_crc(data, length)
        assert actual.to_bytes(4, 'big') == (original & 0xffffffff).to_bytes(4, 'little')
    serial, password = b'TEST\x12\x34\x56\x78', b'0123456789'
    initialise({})
    oracle.uc.mem_write(oracle.scratch, password)
    assert oracle.call('aal_gpon_vendor_id_set', 0, int.from_bytes(serial[:4], 'big')) == 0
    assert oracle.call('aal_gpon_vssn_set', 0, int.from_bytes(serial[4:], 'big')) == 0
    assert oracle.call('aal_gpon_passwd_set', 0, oracle.scratch) == 0
    native.identity(ctypes.byref(io), serial, password)
    assert writes == stock_writes, ('identity', writes, stock_writes)
    initialise({0x6000: 0x3a983352})
    oracle.uc.mem_write(oracle.scratch, struct.pack('<7I', 0, 0, 0, 0, 0x3fff, 0, 0))
    assert oracle.call('aal_gpon_mac_onu_cfg_set', 0) == 0
    assert oracle.call('aal_gpon_ds_frame_thrsd_set', 0, 16, oracle.scratch) == 0
    native.parameters(ctypes.byref(io))
    assert writes == stock_writes, ('MAC parameters', writes, stock_writes)
    initialise({0x6114: 0x00460060})
    assert oracle.call('aal_gpon_active_set', 0, 1) == 0
    native.active(ctypes.byref(io), 1)
    assert writes == stock_writes
    for software in (0, 1):
        initialise({0x6130: 0xa5000000})
        oracle.uc.mem_write(oracle.scratch, struct.pack('<H2xII', 17, 1, 1))
        assert oracle.call('aal_gpon_tcont_set', 0, software, 0x123, 7, oracle.scratch) == 0
        assert native.tcont(ctypes.byref(io), 0x123, 17, 1, 1, software) == 0
        assert writes == stock_writes, ('T-CONT', writes, stock_writes)
        initialise({0x6138: 0xa5000004})
        oracle.uc.mem_write(oracle.scratch, struct.pack('<H2xIII', 200, 1, 1, 1))
        assert oracle.call('aal_gpon_ds_gem_port_set', 0, software, 0xabc, 13, oracle.scratch) == 0
        assert native.ds_gem(ctypes.byref(io), 0xabc, 200, 1, 1, software) == 0
        assert writes == stock_writes, ('downstream GEM', writes, stock_writes)
    initialise({0x6174: 0xa5000000})
    assert oracle.call('aal_gpon_us_gem_port_set', 0, 200, 0xabc) == 0
    assert native.us_gem(ctypes.byref(io), 200, 0xabc) == 0
    assert writes == stock_writes, ('upstream GEM', writes, stock_writes)
    initialise({0x483c: 0x60000000})
    oracle.uc.mem_write(oracle.scratch, struct.pack('<3I', 0x2345, 1, 1))
    assert oracle.call('aal_xgpon_bwmp_tcont_set', 0, 0, 17, 7, oracle.scratch) == 0
    assert native.xg_tcont(ctypes.byref(io), 17, 0x2345, 1, 1) == 0
    assert writes == stock_writes, ('XG T-CONT', writes, stock_writes)
    initialise({0x412c + 200 * 4: 0xa5000000})
    oracle.uc.mem_write(oracle.scratch, struct.pack('<3I', 0xabcd, 1, 0))
    assert oracle.call('aal_xgpon_dsfp_xgem_set', 0, 200, 7, oracle.scratch) == 0
    native.xg_ds_gem(ctypes.byref(io), 200, 0xabcd, 1, 0)
    assert writes == stock_writes, ('XG downstream GEM', writes, stock_writes)
    initialise({0x461c: 0xa5000000})
    oracle.uc.mem_write(oracle.scratch, struct.pack('<4I', 0xabcd, 1, 1, 0))
    assert oracle.call('aal_xgpon_usbg_xgem_cfg_set', 0, 200, 15, oracle.scratch) == 0
    assert native.xg_us_gem(ctypes.byref(io), 200, 0xabcd, 1, 1, 0) == 0
    assert writes == stock_writes, ('XG upstream GEM', writes, stock_writes)
    payload = bytes(range(0x81, 0x8b))
    initialise({0x60e4: 0x005a00, 0x60e0: 0})
    oracle.uc.mem_write(oracle.scratch, payload)
    assert oracle.call('aal_gpon_send_ploam_us', 0, 0, 9, oracle.scratch) == 0
    native.gpon_tx(ctypes.byref(io), 9, payload)
    assert writes == stock_writes, ('GPON TX', writes, stock_writes)
    wire = b'\x5a\x0a' + bytes(range(10)) + b'\x83'
    fifo = b'\x07' + wire
    values = {0x60f8: int.from_bytes(fifo[:2], 'big')}
    values.update({0x60fc + i * 4: int.from_bytes(fifo[2 + i * 4:6 + i * 4], 'big') for i in range(3)})
    initialise(values)
    assert oracle.call('aal_gpon_ds_ploam_msg_get', 0, 0, oracle.scratch, oracle.scratch + 64) == 0
    actual = bytes(oracle.uc.mem_read(oracle.scratch, 14))
    received = (ctypes.c_ubyte * 48)()
    remaining = native.gpon_rx(ctypes.byref(io), received)
    assert actual == fifo and bytes(received[:13]) == wire and remaining == 7
    assert writes == stock_writes
    message = bytes(range(40))
    initialise({})
    oracle.uc.mem_write(oracle.scratch, message)
    assert oracle.call('aal_ploam_txbuf_data_set', 0, oracle.scratch) == 0
    native.xgpon_tx(ctypes.byref(io), message, 1)
    assert writes[:10] == stock_writes and writes[-1] == (0x4afc, 0x80000001)
    for buffer in (0, 1):
        message = bytes((i + buffer * 64) & 255 for i in range(48))
        initialise({0x4b80 + buffer * 48 + i: int.from_bytes(message[i:i + 4], 'big') for i in range(0, 48, 4)})
        assert oracle.call('aal_ploam_rxbuf_get', 0, buffer, 1, oracle.scratch) == 0
        native.xgpon_rx(ctypes.byref(io), buffer, received)
        assert bytes(received) == bytes(oracle.uc.mem_read(oracle.scratch, 48)) == message
        assert writes[-1] == (0x4b28 + buffer * 4, 0)
    orders = []
    for turn0, turn1 in ((0, 0), (0, 1), (1, 0), (1, 1)):
        status0, status1 = 0x80000000 | turn0 << 30, 0x80000000 | turn1 << 30
        values = {0x4b28: status0, 0x4b2c: status1}
        for buffer in (0, 1):
            values.update({0x4b80 + buffer * 48 + i: buffer * 0x01010101 for i in range(0, 48, 4)})
        initialise(values)
        assert oracle.call('__xgpon_ploam_isr', 0, 0xc0000000) == 0
        original_order = [packet[8] for packet in events]
        result = (ctypes.c_ubyte * 2)()
        count = native.order(status0, status1, result)
        assert count == 2 and list(result) == original_order, (turn0, turn1, list(result), original_order)
        orders.append({'turn0': turn0, 'turn1': turn1, 'original_and_native_order': original_order})
report = {'identity_registers_match_stock': True, 'GPON_PLOAM_TX_writes_match_stock': True,
          'GPON_key_switch_bank_and_index_match_stock': True,
          'OMCI_GTC_CRC_bytes_match_board_original': True,
          'GPON_MAC_parameters_and_activation_match_stock': True,
          'GPON_AllocID_TCONT_and_bidirectional_GEM_table_IO_match_stock': True,
          'XG_14bit_AllocID_16bit_XGEM_tables_match_stock': True,
          'GPON_FIFO_metadata_removed_from_wire_message': True,
          'XG_PLOAM_TX_40_bytes_RX_48_bytes_match_stock': True,
          'XG_receive_buffer_order': orders, 'hardware_optical_test': False}
(root / 'logs/itu47-original-IO-contract.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
