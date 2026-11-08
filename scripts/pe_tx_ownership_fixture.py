"""Initialize the native DDR ownership tables used by linked PE tests."""
import struct

def initialize(cpu, base, requests=2048, occupied=0):
    cpu.mem_write(base + 0x39000, struct.pack('<128I', *([10896 | 7 << 16] * 128)))
    cpu.mem_write(base + 0x2e000, struct.pack('<2048H', *range(2048)))
    cpu.mem_write(base + 0x58000, struct.pack('<4095H', *range(4095)))
    cpu.mem_write(base + 0x34000, struct.pack('<10I', occupied, requests, 0, 0, *([1] * 6)))

def map_report(cpu, base, report, request=None):
    tag = (report >> 16) & 4095
    qsel = (report >> 8) & 31
    ring = qsel & 3 if qsel < 8 else 4 if qsel == 0x12 else 5
    request = tag if request is None else request
    cpu.mem_write(base + 0x40000 + (ring * 4096 + tag) * 4, struct.pack('<I', request + 1))
    if 256 <= request < 2304:
        cpu.mem_write(base + 0x35000 + (request - 256) * 4, struct.pack('<I', ring << 12 | tag))
