#!/usr/bin/env python3
"""Execute the linked PE on short ACK/UDP aggregates and check NI bytes."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys

from check_pe_uplink_small_udp import checksum, packet
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2
from unicorn.mips_const import UC_MIPS_REG_RA, UC_MIPS_REG_SP, UC_MIPS_REG_PC, UC_MIPS_REG_V0


def tcp_ack():
    addresses = bytes.fromhex('c0a801d4c6120002')
    tcp = struct.pack('>HHIIBBHHH', 55000, 443, 100, 200, 5 << 4, 0x10, 65535, 0, 0)
    check = checksum(addresses + struct.pack('>BBH', 0, 6, len(tcp)) + tcp)
    tcp = tcp[:16] + struct.pack('>H', check) + tcp[18:]
    ip = struct.pack('>BBHHHBBH', 0x45, 0, 40, 0, 0, 64, 6, 0) + addresses
    ip = ip[:10] + struct.pack('>H', checksum(ip)) + ip[12:]
    return bytes.fromhex('0800'), ip + tcp


def replay(directory, networks, slot):
    directory = Path(directory)
    binary = (directory / 'pe-tx.bin').read_bytes()
    symbols = subprocess.check_output(['llvm-nm-18', str(directory / 'pe-wd.elf')], text=True)
    entry = next(int(s.split()[0], 16) for s in symbols.splitlines() if s.endswith(' rx_uplink'))
    base, states, buf, descriptors = 0x04000000, 0x05000000, 0x06000000, 0x06100000
    mb, vp = base + 0x2000, 0x13001100 + (9 + slot) * 0xa0
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in ((0x1fc00000, 0x10000), (base, 0x20000), (states, 0x20000),
                     (buf, 0x4000), (descriptors, 0x1000), (0x13000000, 0x2000),
                     (0x10000000, 0x10000)):
        u.mem_map(at, size)
    u.mem_write(0x1fc00000, binary)
    put = lambda at, value: u.mem_write(at, struct.pack('<I', value))
    get = lambda at: struct.unpack('<I', u.mem_read(at, 4))[0]
    put(0x10002300, 1 << 31 | 1 << 13)
    put(0x100038c4, 0xa0000000)
    peer, ap, dst = [bytes.fromhex(x) for x in ('067afb78b287', '02e04c076805', '0050c2010200')]
    u.mem_write(mb + 64, ap)
    u.mem_write(base + 0xe410, peer)
    put(base + 0x11a08, 1)
    put(states + 0x10008, 1 << 31 | (slot + 1) << 8)
    state = states + 2 * 512
    put(state, 1 << 31 | 0x123)
    for index, value in ((88, vp | 0xa0000000), (89, descriptors), (90, 63), (91, 63),
                         (93, 1), (106, states)):
        put(mb + index * 4, value)
    body, expected, offsets = b'', [], []
    for ethertype, network in networks:
        body += b'\x5a' * (-len(body) & 3)
        offsets.append(len(body))
        llc = bytes.fromhex('aaaa03000000') + ethertype + network
        body += dst + peer + struct.pack('>H', len(llc)) + llc
        expected.append(dst + peer + ethertype + network)
    header = struct.pack('<HH', 0x4188, 0) + ap + peer + dst + struct.pack('<H', 0x123 << 4) + b'\x80\x00'
    frame = header + bytes.fromhex('0200002000000000') + body + bytes(12)
    raw = bytearray(36) + frame
    struct.pack_into('<II', raw, 0, len(raw) | 0xc000 | 1 << 16, len(frame) | 1 << 31)
    struct.pack_into('<I', raw, 16, 4)
    struct.pack_into('<I', raw, 24, 1 << 28 | 2 << 16)
    u.mem_write(buf, bytes(raw))
    for reg, value in ((UC_MIPS_REG_A0, mb | 0xa0000000), (UC_MIPS_REG_A1, buf | 0xa0000000),
                       (UC_MIPS_REG_A2, buf), (UC_MIPS_REG_SP, (base + 0x17ff0) | 0xa0000000),
                       (UC_MIPS_REG_RA, 0x9fc0fff0)):
        u.reg_write(reg, value)
    u.emu_start(entry, 0x9fc0fff0, count=100000)
    assert u.reg_read(UC_MIPS_REG_PC) == 0x9fc0fff0
    result = u.reg_read(UC_MIPS_REG_V0)
    if result:
        assert result == 0x80000000 | (63 + len(networks))
        assert get(mb + 90 * 4) == 63 + len(networks)
        assert get(vp + 0x18) == (63 + len(networks)) & 63
        assert get(state) == 1 << 31 | 0x124 and get(state + 4) == 2
        assert get(state + 12) == 1 and get(state + 20) == len(networks)
        rewritten = bytearray(raw)
        for n, ethernet in enumerate(expected):
            pos = 36 + 26 + 8 + offsets[n] + 8
            address, info = struct.unpack('<II', u.mem_read(descriptors + ((63 + n) & 63) * 8, 8))
            assert address == buf + pos
            assert info == 0xa5000001 | len(ethernet) << 8 | (slot + 1) << 1
            assert bytes(u.mem_read(address, len(ethernet))) == ethernet
            rewritten[pos:pos + 12] = dst + peer
        # Only the Ethernet addresses change: adjacent headers, padding,
        # network checksums, crypto trailer and payload remain intact.
        assert bytes(u.mem_read(buf, len(raw))) == bytes(rewritten)
    else:
        assert get(mb + 90 * 4) == 63 and get(vp + 0x18) == 0
        assert get(state) == 1 << 31 | 0x123 and get(state + 4) == 0
        assert bytes(u.mem_read(buf, len(raw))) == bytes(raw)
    return dict(PE_sha256=hashlib.sha256(binary).hexdigest(), NI_VP=9 + slot,
                Ethernet_lengths=[len(x) for x in expected], fastpath=bool(result))


if __name__ == '__main__':
    rows = []
    cases = [([tcp_ack(), tcp_ack()], 0, False),
             ([packet(False, 0), tcp_ack(), packet(False, 1472)], 1, False),
             ([packet(False, 1472)] * 2, 0, True)]
    for networks, slot, old_fastpath in cases:
        before, after = [replay(path, networks, slot) for path in sys.argv[1:3]]
        assert before['fastpath'] == old_fastpath and after['fastpath']
        rows.append(dict(before=before, after=after))
    print(json.dumps(dict(scope='Actual linked RX entry, synthetic hardware-decrypted MPDU. '
                         'Exact NI bytes, ring wrap and PN/SN; no radio timing or Mbps model.', cases=rows), indent=2))
