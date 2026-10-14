#!/usr/bin/env python3
"""Check short IPv4 UDP and full-size IPv4/IPv6 in the linked PE RX entry."""
from pathlib import Path
import json
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2
from unicorn.mips_const import UC_MIPS_REG_RA, UC_MIPS_REG_SP, UC_MIPS_REG_PC, UC_MIPS_REG_V0


def checksum(data):
    data += bytes(len(data) & 1)
    value = sum(struct.unpack('>%dH' % (len(data) // 2), data))
    while value >> 16:
        value = (value & 65535) + (value >> 16)
    return ~value & 65535


def packet(ipv6, payload_bytes):
    data = bytes((i * 31 + 7) & 255 for i in range(payload_bytes))
    udp = struct.pack('>4H', 55000, 55001, len(data) + 8, 0) + data
    if ipv6:
        addresses = bytes.fromhex('20010db800000000000000000000000120010db8000000000000000000000002')
        pseudo = addresses + struct.pack('>I3xB', len(udp), 17)
        header = struct.pack('>IHBB', 6 << 28, len(udp), 17, 64) + addresses
    else:
        addresses = bytes.fromhex('c0a801d4c6120002')
        pseudo = addresses + struct.pack('>BBH', 0, 17, len(udp))
        header = struct.pack('>BBHHHBBH', 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0) + addresses
        header = header[:10] + struct.pack('>H', checksum(header)) + header[12:]
    udp = udp[:6] + struct.pack('>H', checksum(pseudo + udp) or 65535) + udp[8:]
    return bytes.fromhex('86dd' if ipv6 else '0800'), header + udp


def case(directory, slot, ipv6, payload_bytes):
    directory = Path(directory)
    symbols = subprocess.check_output(['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True)
    entry = next(int(s.split()[0], 16) for s in symbols.splitlines() if s.endswith(' rx_uplink'))
    base, states, buf, descriptors = 0x04000000, 0x05000000, 0x06000000, 0x06100000
    mb, vp = base + 0x2000, 0x13001100 + (9 + slot) * 0xa0
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in [(0x1fc00000, 0x4000), (base, 0x20000), (states, 0x20000),
                     (buf, 0x4000), (descriptors, 0x1000), (0x13000000, 0x2000)]:
        u.mem_map(at, size)
    u.mem_write(0x1fc00000, (directory / 'pe-tx.bin').read_bytes())
    put = lambda at, value: u.mem_write(at, struct.pack('<I', value))
    get = lambda at: struct.unpack('<I', u.mem_read(at, 4))[0]
    peer, ap, dst = bytes.fromhex('067afb78b287'), bytes.fromhex('02e04c076805'), bytes.fromhex('0050c2010200')
    u.mem_write(mb + 64, ap)
    u.mem_write(base + 0xe410, peer)
    put(base + 0x11a08, 1)
    put(states + 0x10008, 1 << 31 | (slot + 1) << 8)
    put(states + 2 * 512, 1 << 31 | 0x123)
    for index, value in [(88, vp | 0xa0000000), (89, descriptors), (93, 1), (106, states)]:
        put(mb + index * 4, value)
    ethertype, network = packet(ipv6, payload_bytes)
    header = struct.pack('<HH', 0x4188, 0) + ap + peer + dst + struct.pack('<H', 0x123 << 4) + bytes(2)
    frame = header + bytes.fromhex('0200002000000000aaaa03000000') + ethertype + network + bytes(12)
    raw = bytearray(36) + frame
    struct.pack_into('<II', raw, 0, len(raw) | 0xc000 | 1 << 16, len(frame) | 1 << 31)
    struct.pack_into('<I', raw, 16, 4)  # Successful hardware decrypt, no errors.
    struct.pack_into('<I', raw, 24, 1 << 28 | 2 << 16)
    u.mem_write(buf, bytes(raw))
    for reg, value in [(UC_MIPS_REG_A0, mb | 0xa0000000), (UC_MIPS_REG_A1, buf | 0xa0000000),
                       (UC_MIPS_REG_A2, buf), (UC_MIPS_REG_SP, (base + 0x17ff0) | 0xa0000000),
                       (UC_MIPS_REG_RA, 0xbfc03ff0)]:
        u.reg_write(reg, value)
    u.emu_start(entry, 0xbfc03ff0, count=100000)
    assert u.reg_read(UC_MIPS_REG_PC) == 0xbfc03ff0
    result = u.reg_read(UC_MIPS_REG_V0)
    if result:
        addr, info = get(descriptors), get(descriptors + 4)
        expected = dst + peer + ethertype + network
        expected += bytes(max(0, 60 - len(expected)))
        assert result == 0x80000001 and get(vp + 0x18) == 1
        assert info == 0xa5000001 | len(expected) << 8 | (slot + 1) << 1
        assert bytes(u.mem_read(addr, len(expected))) == expected
        assert get(states + 2 * 512) == 1 << 31 | 0x124
        assert get(states + 2 * 512 + 4) == 2
        assert get(mb + 94 * 4) == 0
    else:
        assert get(vp + 0x18) == 0 and bytes(u.mem_read(buf, len(raw))) == bytes(raw)
    return dict(slot=slot, NI_VP=9 + slot, ipv6=ipv6, UDP_payload_bytes=payload_bytes,
                PE_to_NI=bool(result), exact_ethernet=bool(result), ARM_handoff=False if result else 'required')


before = [case(sys.argv[1], slot, False, 0) for slot in range(2)]
after = [case(sys.argv[2], slot, ipv6, size) for slot in range(2)
         for ipv6, size in [(False, 0), (False, 1472), (True, 1452)]]
assert not any(row['PE_to_NI'] for row in before)
assert all(row['PE_to_NI'] for row in after)
print(json.dumps(dict(scope='Actual linked rx_uplink; synthetic already-decrypted RX WD/data; verifies NI descriptor, interface identity, PN/SN and Ethernet bytes, not radio/ASIC timing.', before=before, after=after), indent=2))
