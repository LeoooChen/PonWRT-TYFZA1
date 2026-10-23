#!/usr/bin/env python3
"""Execute both linked PE main loops against shared TX descriptors and MMIO."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import (Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN,
                     UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_MEM_READ)
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_SP,
                                UC_MIPS_REG_PC)

DIRECTORY = Path(sys.argv[1])
BINARY = (DIRECTORY / 'pe-tx.bin').read_bytes()
SYMBOLS = {s[2]: int(s[0], 16) for line in subprocess.check_output(
    ['llvm-nm-18', str(DIRECTORY / 'pe-wd.elf')], text=True).splitlines()
    if len(s := line.split()) == 3}
RX, TX, BAR, RING, PAGES, PAYLOAD, TABLE, RX_BUFFER = (
    0x04000000, 0x04500000, 0x12000000, 0x05000000,
    0x05100000, 0x06000000, 0x07100000, 0x0a000000)
RXMB, TXMB = RX + 0x2000, TX + 0x2000
WRITE, READ, BUILD = 193, 194, 0x1b000
REGISTERS = (0x1058, 0x105c, 0x1060, 0x1064, 0x1078, 0x107c)
CHANNELS = (0, 1, 2, 3, 8, 9)
NEXT = (0, 7, 28, 93, 194, 255)


class Machine:
    def __init__(self, sequence=0, paired=True):
        self.u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        for address, size in ((0x1fc00000, 0x10000), (RX, 0x20000),
                              (TX, 0x20000), (BAR, 0x10000), (RING, 0x4000),
                              (PAGES, 0xc000), (PAYLOAD, 0x10000),
                              (TABLE, 0x1000), (RX_BUFFER, 0x4000),
                              (0x10000000, 0x10000), (0x10020000, 0x1000)):
            self.u.mem_map(address, size)
        self.u.mem_write(0x1fc00000, BINARY)
        for mailbox in (TXMB, RXMB):
            for index, value in ((2, BAR | 0xa0000000), (3, 1),
                                 (140, mailbox | 0xa0000000), (142, 1)):
                self.put(mailbox + index * 4, value)
        for index, value in ((8, 254), (9, 254), (11, RING), (12, PAGES),
                             (188, RXMB if paired else 0),
                             (WRITE, sequence), (READ, sequence)):
            self.put(TXMB + index * 4, value)
        self.put(RXMB + 187 * 4, TX)
        self.put(RXMB + 80 * 4, TABLE)
        self.put(TABLE, RX_BUFFER)
        self.hardware = [0x8100 + ring for ring in range(6)]
        for ring, register in enumerate(REGISTERS):
            self.put(BAR + register, self.hardware[ring] << 16 | ((NEXT[ring] - 1) & 255))
        self.contexts = {}
        for name, mailbox, allocation in (('PE1', TXMB, TX), ('PE0', RXMB, RX)):
            self.u.reg_write(UC_MIPS_REG_A0, mailbox | 0xa0000000)
            self.u.reg_write(UC_MIPS_REG_SP, (allocation + 0x1fff0) | 0xa0000000)
            self.u.reg_write(UC_MIPS_REG_PC, SYMBOLS['pe_main'])
            self.contexts[name] = self.u.context_save()
        self.actor = None
        self.events = []
        self.pause_at_ready = False
        self.pause_at_doorbell = False
        self.stop_next_instruction = False
        self.u.hook_add(UC_HOOK_MEM_READ, self.read,
                        begin=TXMB + WRITE * 4, end=TXMB + WRITE * 4 + 3)
        self.u.hook_add(UC_HOOK_CODE, self.step)
        self.u.hook_add(UC_HOOK_BLOCK, self.block)

    def put(self, address, value):
        self.u.mem_write(address, struct.pack('<I', value))

    def get(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def write(self, uc, access, address, size, value, unused):
        address &= 0x1fffffff
        kind = None
        if address in [BAR + register for register in REGISTERS]:
            kind = 'doorbell'
        elif RING <= address < RING + 0x4000 or PAGES <= address < PAGES + 0xc000:
            kind = 'descriptor'
        elif TXMB + WRITE * 4 <= address <= TXMB + READ * 4:
            kind = 'handoff'
        elif address == RXMB + 4 * 4:
            kind = 'command_ack'
        if kind:
            self.events.append((kind, self.actor, address, size, value))
        if self.pause_at_doorbell and kind == 'doorbell':
            self.pause_at_doorbell = False
            self.stop_next_instruction = True
        if self.pause_at_ready and address == TX + BUILD + 3 * 128 + 36:
            self.pause_at_ready = False
            self.stop_next_instruction = True

    def read(self, uc, access, address, size, value, unused):
        if address & 0x1fffffff == TXMB + WRITE * 4:
            self.events.append(('read_write', self.actor, address, size, 0))

    def step(self, uc, address, size, unused):
        if self.stop_next_instruction:
            self.stop_next_instruction = False
            uc.emu_stop()
            return
        self.remaining -= 1
        instruction = self.get(address & 0x1fffffff)
        # Unicorn's memory-write hook on a delay-slot SH replays the first
        # branch-target instruction. Observe stores here without replacing them.
        opcode = instruction >> 26
        if opcode in (0x28, 0x29, 0x2b):
            base = uc.reg_read(UC_MIPS_REG_0 + ((instruction >> 21) & 31))
            offset = (instruction & 0xffff) - (0x10000 if instruction & 0x8000 else 0)
            target = (base + offset) & 0x1fffffff
            width = {0x28: 1, 0x29: 2, 0x2b: 4}[opcode]
            value = uc.reg_read(UC_MIPS_REG_0 + ((instruction >> 16) & 31))
            self.write(uc, None, target, width, value & ((1 << (width * 8)) - 1), None)
        if instruction == 0xf:
            self.events.append(('sync', self.actor, address, size, 0))

    def block(self, uc, address, size, unused):
        # Yield after complete basic blocks, never with a pending delay slot.
        if self.remaining <= 0:
            uc.emu_stop()

    def run(self, actor, count=12000):
        self.actor = actor
        self.u.context_restore(self.contexts[actor])
        start = len(self.events)
        self.remaining = count
        self.u.emu_start(self.u.reg_read(UC_MIPS_REG_PC), 0xbfc0fff0)
        self.contexts[actor] = self.u.context_save()
        return self.events[start:]

    def queue(self):
        for ring in range(6):
            payload = bytes([0x30 + ring]) * 80
            address = PAYLOAD + ring * 256
            self.u.mem_write(address, payload)
            request = [0] * 32
            request[0] = ring
            request[1] = ring | ((NEXT[ring] - 1) & 255) << 8 | NEXT[ring] << 16
            request[2] = len(payload) | 1 << 24
            request[3] = 4
            request[8], request[9] = address, len(payload)
            self.u.mem_write(TX + 0x4000 + ((254 + ring) & 255) * 128,
                             struct.pack('<32I', *request))
        self.put(TXMB + 8 * 4, 4)

    def descriptors(self, rings):
        for ring in rings:
            descriptor = RING + (ring * 256 + ((NEXT[ring] - 1) & 255)) * 8
            assert self.get(descriptor) == 1 << 30 | 70
            assert self.get(descriptor + 4) == PAGES + ring * 128
            page = bytes(self.u.mem_read(PAGES + ring * 128, 128))
            assert struct.unpack_from('<I', page)[0] >> 16 & 15 == CHANNELS[ring]
            length, address = struct.unpack_from('<HI', page, 64)
            assert length == 0x8000 | 80 and address == PAYLOAD + ring * 256
            assert bytes(self.u.mem_read(address, 80)) == bytes([0x30 + ring]) * 80

    def snapshot(self):
        return (bytes(self.u.mem_read(TXMB + WRITE * 4, 2 * 4)),
                bytes(self.u.mem_read(RING, 0x4000)),
                bytes(self.u.mem_read(PAGES, 0xc000)))


def check_receive(machine, events, rings, sequence):
    doorbells = [event for event in events if event[0] == 'doorbell']
    assert doorbells == [('doorbell', 'PE0', BAR + REGISTERS[ring], 2, NEXT[ring])
                         for ring in rings], doorbells
    acks = [event for event in events if event[0] == 'handoff' and event[2] == TXMB + READ * 4]
    assert acks and acks[-1][4] == sequence, acks
    for doorbell in doorbells:
        at = events.index(doorbell)
        assert any(event[0] == 'descriptor' for event in events[:at])
        assert any(event[0] == 'sync' for event in events[:at])
    assert events.index(acks[-1]) > events.index(doorbells[-1])
    for ring, register in enumerate(REGISTERS):
        assert machine.get(BAR + register) >> 16 == machine.hardware[ring]


for initial_sequence in (0, 15):
    m = Machine(initial_sequence)
    assert not [e for e in m.run('PE0') + m.run('PE1') if e[0] == 'doorbell']
    m.queue()
    published = m.run('PE1')
    sequence = (initial_sequence + 6) & 15
    assert m.get(TXMB + WRITE * 4) == sequence
    assert m.get(TXMB + 9 * 4) == 4
    assert not [e for e in published if e[0] in ('doorbell', 'descriptor')]
    pending = m.snapshot()
    m.run('PE1')
    assert m.snapshot() == pending
    m.put(TXMB + 3 * 4, 2)
    m.run('PE1')
    assert m.get(TXMB + 4 * 4) == 2 and m.snapshot() == pending
    m.put(RXMB + 3 * 4, 2)
    events = m.run('PE0')
    check_receive(m, events, range(6), sequence)
    m.descriptors(range(6))
    assert m.get(RXMB + 4 * 4) == 2
    final_ack = next(i for i,e in enumerate(events) if e[0] == 'command_ack' and e[4] == 2)
    assert final_ack > max(i for i,e in enumerate(events) if e[0] == 'doorbell')
    paused = m.run('PE0')
    assert not [e for e in paused if e[0] in ('read_write', 'doorbell', 'handoff')]
    for ring, register in enumerate(REGISTERS):
        assert m.get(BAR + register) == m.hardware[ring] << 16 | NEXT[ring]

# Fill the actual cross-core queue; the sixteenth request must stay with PE1.
full = Machine()
full.run('PE0');full.run('PE1');full.queue()
for index in range(6,20):
    request = [0] * 32
    request[0] = index
    request[1] = 0 | ((index - 5) & 255) << 8 | ((index - 4) & 255) << 16
    request[2] = 80 | 1 << 24;request[3] = 4
    request[8],request[9] = PAYLOAD + index * 256,80
    full.u.mem_write(PAYLOAD + index * 256, bytes([index])*80)
    full.u.mem_write(TX+0x4000+((254+index)&255)*128,struct.pack('<32I',*request))
full.put(TXMB+8*4,18)
full.run('PE1',50000)
assert full.get(TXMB+WRITE*4)==15 and full.get(TXMB+9*4)==13
saved = bytes(full.u.mem_read(TX+BUILD,16*128))
full.run('PE1')
assert bytes(full.u.mem_read(TX+BUILD,16*128))==saved
full.run('PE0',50000);full.run('PE1',50000);full.run('PE0',50000)
assert full.get(TXMB+9*4)==18 and full.get(TXMB+WRITE*4)==full.get(TXMB+READ*4)==4

# Seven Ethernet buffers must remain one NI MPDU across the PE boundary.
ni = Machine()
ni.put(TXMB + 24 * 4, 1)
ni.put(TXMB + 14 * 4, PAGES)
request = [0] * 32
request[0] = 256
request[2] = 7 << 24 | 26 << 16 | 878
request[3] = 4
request[4] = 1 << 24 | 1 << 18 | 1 << 21 | 1 << 16
request[5] = 64
for part in range(7):
    address = PAYLOAD + part * 512 + 80
    frame = bytes.fromhex('0200000000020200000000030800') + bytes([part]) * 100
    ni.u.mem_write(address, frame)
    request[8 + part * 2],request[9 + part * 2] = address,len(frame)
ni.u.mem_write(TX + 0x4000 + 254 * 128, struct.pack('<32I', *request))
ni.put(TXMB + 8 * 4, 255)
ni.run('PE1')
assert ni.get(TXMB + 28 * 4) == 0
ni.run('PE0')
page = bytes(ni.u.mem_read(PAGES + 256 * 128, 128))
assert ni.get(TXMB + 28 * 4) == 7
assert struct.unpack_from('<I', page, 4)[0] >> 26 == 7
for part in range(7):
    length,address = struct.unpack_from('<HI', page, 64 + part * 6)
    assert (length & 0x7fff) == (148 if not part else 124)
    assert bool(length & 0x8000) == (part == 6)
    assert bytes(ni.u.mem_read(PAYLOAD + part * 512 + 94, 100)) == bytes([part]) * 100

single = Machine(paired=False)
single.queue()
events = single.run('PE1')
assert [e for e in events if e[0] == 'doorbell'] == [
    ('doorbell', 'PE1', BAR + register, 2, NEXT[ring])
    for ring, register in enumerate(REGISTERS)]
assert single.get(TXMB + 9 * 4) == 4
single.descriptors(range(6))
for ring, register in enumerate(REGISTERS):
    assert single.get(BAR + register) == single.hardware[ring] << 16 | NEXT[ring]
assert not [e for e in single.run('PE1') if e[0] == 'doorbell']

print(json.dumps({
    'scope': 'Actual linked PE main loops; shared memory and PCIe MMIO modeled, no RF simulation.',
    'PE_sha256': hashlib.sha256(BINARY).hexdigest(),
    'PE1_prepares_requests_without_writing_DMA_descriptors': True,
    'PE0_builds_descriptors_and_publishes_all_six_channels': True,
    'payloads_and_hardware_consumer_halfwords_preserved': True,
    'queue_wrap_and_full_backpressure_preserve_requests': True,
    'paused_PE0_drains_before_command_ACK': True,
    'no_peer_access_after_pause_ACK': True,
    'seven_subframe_NI_AMSDU_retains_all_payloads': True,
    'single_PE_direct_fallback': True,
}, indent=2))
