#!/usr/bin/env python3
"""Execute the UDP module with an NI cursor left above63 by the TCP fixture."""
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm64_const import UC_ARM64_REG_X4

module = Path(sys.argv[1])
dma, ram, device = 0x6000000, 0x8000000, 0x7000000
vp = dma + 0x100 + 11 * 0xa0
clock, consumed, publications, allocation = 0, 0, 0, 0
pending, retained = [], []


def boundary(name, args):
    global clock, allocation
    if name == 'dev_get_by_name':
        return device
    if name in ('ioremap', 'ioremap_prot', '__ioremap_prot'):
        assert args[:2] == [0x4f7001000, 0x1000]
        return dma
    if name == 'dma_alloc_attrs':
        allocation = args[1]
        oracle.word(args[2], ram)
        return ram
    if name == 'dma_free_attrs':
        assert args[1:3] == [allocation, ram]
        return 0
    if name in ('memset', '__memset'):
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name in ('memcpy', '__memcpy'):
        oracle.uc.mem_write(args[0], bytes(oracle.uc.mem_read(args[1], args[2])))
        return args[0]
    if name == 'ip_send_check':
        header = bytearray(oracle.uc.mem_read(args[0], 20))
        header[10:12] = bytes(2)
        checksum = sum(int.from_bytes(header[i:i+2], 'big') for i in range(0, 20, 2))
        while checksum >> 16:
            checksum = (checksum & 65535) + (checksum >> 16)
        header[10:12] = (checksum ^ 65535).to_bytes(2, 'big')
        oracle.uc.mem_write(args[0], bytes(header))
        return 0
    if name == 'csum_partial':
        data = bytes(oracle.uc.mem_read(args[0], args[1]))
        return (sum(int.from_bytes(data[i:i+2], 'little')
                    for i in range(0, len(data), 2)) + args[2]) & 0xffffffff
    if name == 'csum_tcpudp_nofold':
        data = (struct.pack('<II', *args[:2]) + bytes((0, args[3])) +
                args[2].to_bytes(2, 'big'))
        return (sum(int.from_bytes(data[i:i+2], 'little') for i in range(0, 12, 2)) +
                oracle.uc.reg_read(UC_ARM64_REG_X4)) & 0xffffffff
    if name in ('ktime_get', 'ktime_get_ns'):
        clock += 10000
        return clock
    if name in ('usleep_range_state', 'usleep_range'):
        clock += args[0] * 1000
        return 0
    if name == 'mul_u64_u64_div_u64':
        return args[0] * args[1] // args[2]
    if name in ('dev_put', '__dev_put', 'iounmap', '_printk', 'printk'):
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['init_module', 'readl', 'wait_deadline', 'put_unaligned_be32'], boundary,
                      external_symbols={'__stack_chk_guard': 0x5007000,
                                        'init_net': device + 0x8000,
                                        'arm64_use_ng_mappings': 0x5007010})
for address, length in ((dma, 4096), (device, 0x10000), (ram, 0x200000)):
    oracle.uc.mem_map(address, length)


def get(address):
    return oracle.unpack(address, 'I')[0]


def put(address, value):
    oracle.uc.mem_write(address, struct.pack('<I', value))


put(vp + 0x1c, 898)
put(vp + 0x18, 898)
put(vp + 0x0c, 0x9000000 | 10)
put(oracle.symbols['packets'][0], 2440)
put(oracle.symbols['rate_mbps'][0], 1900)
pattern = bytes((i * 31 + 7) & 255 for i in range(64, 1514))


def mmio_write(uc, access, address, size, value, unused):
    global consumed, publications, retained
    assert size == 4
    if address == vp + 4:
        pending.append((address, value & 0x7fffffff))
    if address != vp + 0x68 or value != 1:
        return
    depth = get(vp + 0x0c) & 15
    read, write = get(vp + 0x1c), get(vp + 0x18)
    assert read < 1 << depth and write < 1 << depth, (read, write, 1 << depth)
    assert depth == 10
    for pointer, data in retained:
        assert bytes(uc.mem_read(pointer, len(data))) == data
    batch = (write - read) & 1023
    assert 0 < batch <= 61
    latest = []
    for i in range(batch):
        slot = (read + i) & 1023
        pointer = get(ram + slot * 8)
        assert ram + 8192 <= pointer and pointer + 1514 <= ram + allocation
        assert get(ram + slot * 8 + 4) == 0x81000001 | 7 << 25 | 1514 << 8
        packet = bytes(uc.mem_read(pointer, 1514))
        assert packet[50:60] == b'TYFPPP-SIM'
        assert int.from_bytes(packet[60:64], 'big') == consumed
        assert packet[64:] == pattern
        for checked in (packet[22:42], packet[34:42] + b'\x00\x11\x05\xc0' + packet[42:]):
            checksum = sum(int.from_bytes(checked[j:j+2], 'big')
                           for j in range(0, len(checked), 2))
            while checksum >> 16:
                checksum = (checksum & 65535) + (checksum >> 16)
            assert checksum == 65535
        consumed += 1
        latest.append((pointer, packet))
    retained = latest[-2:]
    publications += 1
    pending.append((vp + 0x1c, write))


def complete_mmio(uc, address, size, unused):
    while pending:
        register, value = pending.pop(0)
        put(register, value)


oracle.uc.hook_add(UC_HOOK_MEM_WRITE, mmio_write, begin=dma, end=dma + 4095)
oracle.uc.hook_add(UC_HOOK_CODE, complete_mmio)
assert oracle.call('init_module', instruction_budget=40000000) == 0
assert allocation == 1024 * (1536 + 8)
assert consumed == 2440 and publications == 40
assert get(vp) == 0
print('UDP module: cursor898,2440 payloads,1024-slot wrap,61-packet batches and retained buffers passed')
