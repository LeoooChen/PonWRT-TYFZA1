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
                              (PAGES, 0x32000), (PAYLOAD, 0x10000),
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
        self.hardware = [0x8140 + ring for ring in range(6)]
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
        self.u.hook_add(UC_HOOK_MEM_READ, self.read,
                        begin=RING, end=RING + 0x3fff)
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
        elif RING <= address < RING + 0x4000 or PAGES <= address < PAGES + 0x31800:
            kind = 'descriptor'
        elif TXMB + WRITE * 4 <= address <= TXMB + READ * 4:
            kind = 'handoff'
        elif address in (RXMB + 4 * 4, TXMB + 4 * 4):
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
        if RING <= address & 0x1fffffff < RING + 0x4000:
            self.events.append(('drain', self.actor, address & 0x1fffffff, size, 0))

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
            assert self.get(descriptor + 4) == PAGES + ring * 264 * 128
            page = bytes(self.u.mem_read(PAGES + ring * 264 * 128, 128))
            assert struct.unpack_from('<I', page)[0] >> 16 & 15 == CHANNELS[ring]
            length, address = struct.unpack_from('<HI', page, 64)
            assert length == 0x8000 | 80 and address == PAYLOAD + ring * 256
            assert bytes(self.u.mem_read(address, 80)) == bytes([0x30 + ring]) * 80

    def snapshot(self):
        return (bytes(self.u.mem_read(TXMB + WRITE * 4, 2 * 4)),
                bytes(self.u.mem_read(RING, 0x4000)),
                bytes(self.u.mem_read(PAGES, 0x32000)))


def check_publish(machine, events, rings, sequence):
    doorbells = [event for event in events if event[0] == 'doorbell']
    assert doorbells == [('doorbell', 'PE1', BAR + REGISTERS[ring], 2, NEXT[ring])
                         for ring in rings], doorbells
    acks = [event for event in events if event[0] == 'handoff' and event[2] == TXMB + READ * 4]
    assert acks and acks[-1][4] == sequence, acks
    for doorbell in doorbells:
        at = events.index(doorbell)
        last_descriptor = max(i for i, event in enumerate(events[:at])
                              if event[0] == 'descriptor')
        assert any(event[0] == 'sync' for event in events[last_descriptor + 1:at])
        ring = REGISTERS.index(doorbell[2] - BAR)
        last = (doorbell[4] - 1) & 255
        assert ('drain', 'PE1', RING + (ring * 256 + last) * 8 + 4, 4, 0) in events[last_descriptor + 1:at]
    assert events.index(acks[-1]) > events.index(doorbells[-1])
    for ring, register in enumerate(REGISTERS):
        assert machine.get(BAR + register) >> 16 == machine.hardware[ring]


for initial_sequence in (0, 15):
    m = Machine(initial_sequence)
    assert not [e for e in m.run('PE0') + m.run('PE1') if e[0] == 'doorbell']
    idle_publications = []
    hook = m.u.hook_add(UC_HOOK_CODE,
        lambda uc, address, size, data: idle_publications.append(address),
        begin=SYMBOLS['pe_tx_publish'], end=SYMBOLS['pe_tx_publish'])
    m.run('PE1')
    m.u.hook_del(hook)
    assert not idle_publications
    m.queue()
    pending = m.snapshot()
    assert not [e for e in m.run('PE0') if e[0] in ('doorbell', 'descriptor', 'handoff')]
    assert m.snapshot() == pending
    events = m.run('PE1')
    sequence = (initial_sequence + 6) & 15
    check_publish(m, events, range(6), sequence)
    assert m.get(TXMB + WRITE * 4) == m.get(TXMB + READ * 4) == sequence
    assert m.get(TXMB + 9 * 4) == 4
    m.descriptors(range(6))
    m.put(TXMB + 3 * 4, 2)
    m.run('PE1')
    assert m.get(TXMB + 4 * 4) == 2
    assert not [e for e in m.run('PE1') if e[0] in ('doorbell', 'descriptor', 'handoff')]
    m.put(RXMB + 3 * 4, 2)
    assert not [e for e in m.run('PE0') if e[0] in ('doorbell', 'descriptor', 'handoff')]
    assert m.get(RXMB + 4 * 4) == 2

# Both staging and publication use the same bounded eight-MPDU turn.
burst = Machine()
burst.put(TXMB + 9 * 4, 0)
burst.put(TXMB + 8 * 4, 12)
for index in range(12):
    request = [0] * 32
    request[0], request[1] = index, index << 8 | (index + 1) << 16
    request[2], request[3] = 80 | 1 << 24, 4
    request[8], request[9] = PAYLOAD + index * 256, 80
    burst.u.mem_write(TX + 0x4000 + index * 128, struct.pack('<32I', *request))
events = burst.run('PE1', 50000)
assert [e[4] for e in events if e[0] == 'doorbell'] == [8, 12]
assert burst.get(TXMB + 9 * 4) == 12

# Hardware backpressure fills the local selected-MPDU queue. The sixteenth
# request remains on ARM's ring until PE1 can publish, independent of PE0.
full = Machine()
full.run('PE1')
full.queue()
for ring, register in enumerate(REGISTERS):
    producer = (NEXT[ring] - 1) & 255
    full.put(BAR + register, NEXT[ring] << 16 | producer)
for index in range(6, 20):
    request = [0] * 32
    request[0] = index
    request[1] = (index - 5) << 8 | (index - 4) << 16
    request[2] = 80 | 1 << 24
    request[3] = 4
    request[8], request[9] = PAYLOAD + index * 256, 80
    full.u.mem_write(PAYLOAD + index * 256, bytes([index]) * 80)
    full.u.mem_write(TX + 0x4000 + ((254 + index) & 255) * 128,
                     struct.pack('<32I', *request))
full.put(TXMB + 8 * 4, 18)
assert not [e for e in full.run('PE1', 50000) if e[0] == 'doorbell']
assert full.get(TXMB + WRITE * 4) == 15 and full.get(TXMB + 9 * 4) == 13
saved = bytes(full.u.mem_read(TX + BUILD, 16 * 128))
full.run('PE1')
assert bytes(full.u.mem_read(TX + BUILD, 16 * 128)) == saved
assert not [e for e in full.run('PE0') if e[0] in ('doorbell', 'descriptor', 'handoff')]
for ring, register in enumerate(REGISTERS):
    full.put(BAR + register, full.hardware[ring] << 16 | ((NEXT[ring] - 1) & 255))
full.run('PE1', 50000)
assert full.get(TXMB + 9 * 4) == 18
assert full.get(TXMB + WRITE * 4) == full.get(TXMB + READ * 4) == 4

# PE1 keeps each radio's selected requests in private DMEM. Block both
# channels, poison the former DDR staging area, then drain independently.
dual = Machine()
dual.queue()
second = TX + 0x20000
second_mb = second + 0x2000
dual.u.mem_map(second, 0x20000)
dual.u.mem_map(PAGES + 0x40000, 0x32000)
dual.u.mem_map(RING + 0x10000, 0x10000)
for index, value in ((2, BAR | 0xa0000000), (3, 1), (8, 1),
                     (11, RING + 0x10000), (12, PAGES + 0x40000)):
    dual.put(second_mb + index * 4, value)
request = [0] * 32
request[0], request[2], request[3] = 10, 80 | 1 << 24, 4
request[1] = 1 | 6 << 8 | 7 << 16
request[8], request[9] = PAYLOAD + 8192, 80
dual.u.mem_write(PAYLOAD + 8192, bytes([0x77]) * 80)
dual.u.mem_write(second + 0x4000, struct.pack('<32I', *request))
dual.put(TXMB + 141 * 4, second_mb | 0xa0000000)
dual.put(TXMB + 143 * 4, 1)
for ring, register in enumerate(REGISTERS):
    dual.put(BAR + register, NEXT[ring] << 16)
dual.run('PE1')
assert dual.get(TXMB + WRITE * 4) == 6 and dual.get(TXMB + READ * 4) == 0
assert dual.get(second_mb + WRITE * 4) == 1
dual.u.mem_write(TX + BUILD, bytes([0xa5]) * 2048)
dual.u.mem_write(second + BUILD, bytes([0x5a]) * 2048)
for register in REGISTERS:
    dual.put(BAR + register, 128 << 16)
dual.run('PE1', 30000)
dual.descriptors(range(6))
assert dual.get(TXMB + READ * 4) == 6 and dual.get(second_mb + READ * 4) == 1
page = bytes(dual.u.mem_read(dual.get(RING + 0x10000 + (256 + 6) * 8 + 4), 128))
assert struct.unpack_from('<HI', page, 64) == (0x8000 | 80, PAYLOAD + 8192)

# Seven Ethernet buffers must remain one NI MPDU through local publication.
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
ni.run('PE0')
assert ni.get(TXMB + 28 * 4) == 0
ni.run('PE1')
page = bytes(ni.u.mem_read(ni.get(RING + 4), 128))
assert ni.get(TXMB + 28 * 4) == 7
assert struct.unpack_from('<I', page, 4)[0] >> 26 == 7
for part in range(7):
    length,address = struct.unpack_from('<HI', page, 64 + part * 6)
    assert (length & 0x7fff) == (148 if not part else 124)
    assert bool(length & 0x8000) == (part == 6)
    assert bytes(ni.u.mem_read(PAYLOAD + part * 512 + 94, 100)) == bytes([part]) * 100

# The final sparse packet must notify ARM without another descriptor arriving.
ni.put(TXMB + 64 * 4, 0xb0020000)
activity_event = ni.get(TXMB + 67 * 4)
ni.put(BAR + 0xc438, 9999)
ni.run('PE1')
assert ni.get(TX + 0x1c020) == 7
assert ni.get(TX + 0x1d004) == 1
assert ni.get(TXMB + 67 * 4) == activity_event
ni.put(BAR + 0xc438, 10000)
ni.run('PE1')
assert ni.get(TX + 0x1d000) == 10000
assert ni.get(TX + 0x1d004) == 0
assert ni.get(TXMB + 67 * 4) == activity_event + 1
ni.put(BAR + 0xc438, 20000)
ni.run('PE1')
assert ni.get(TXMB + 67 * 4) == activity_event + 1
# A wrapped TSF uses the same elapsed-time comparison.
ni.put(TX + 0x1d000, 0xffffec78)
ni.put(TX + 0x1d004, 1)
ni.put(BAR + 0xc438, 5000)
ni.run('PE1')
assert ni.get(TX + 0x1d004) == 0
assert ni.get(TXMB + 67 * 4) == activity_event + 2

# ARM and NI share the same channel even though their WP IDs are disjoint.
# One free TXBD must admit exactly one request, including on a pause boundary.
credit = Machine()
credit.put(TXMB + 24 * 4, 1)
credit.put(TXMB + 14 * 4, PAGES)
credit.put(TXMB + 32 * 4, 254)
credit.put(BAR + REGISTERS[0], 254)  # Hardware consumer0, producer254: one free.
credit.put(TXMB + 8 * 4, 2)
credit.put(TXMB + 9 * 4, 0)
for index, identifier in enumerate((256, 1)):
    address = PAYLOAD + index * 256 + 80
    frame = bytes.fromhex('0200000000020200000000030800') + bytes([index]) * 66
    credit.u.mem_write(address, frame)
    request = [0] * 32
    request[0] = identifier
    request[1] = (254 + index) << 8 | ((255 + index) & 255) << 16
    request[2] = 1 << 24 | (26 << 16 | 100 if not index else 80)
    request[3] = 4
    request[4] = 1 << 24 | 1 << 18 | 1 << 16 if not index else 0
    request[8], request[9] = address, 80
    credit.u.mem_write(TX + 0x4000 + index * 128, struct.pack('<32I', *request))
protected = bytes(credit.u.mem_read(PAGES + 128, 128))
credit.put(TXMB + 64 * 4, 0xb0020000)
credit.put(BAR + 0xc438, 15000)
activity_event = credit.get(TXMB + 67 * 4)
credit.run('PE1')
assert credit.get(TX + 0x1c020) == 1
assert credit.get(TX + 0x1d000) == 15000
assert credit.get(TXMB + 67 * 4) == activity_event + 1
assert credit.get(TXMB + WRITE * 4) == 2
assert credit.get(TXMB + READ * 4) == 1
assert credit.get(BAR + REGISTERS[0]) == 255
assert bytes(credit.u.mem_read(PAGES + 128, 128)) == protected
pending = credit.snapshot()
credit.run('PE1')
assert credit.snapshot() == pending
assert credit.get(TX + 0x1c020) == 1
credit.put(TXMB + 3 * 4, 2)
credit.run('PE1')
assert credit.get(TXMB + 4 * 4) == 1  # Full TXBD prevents PE1 pause ACK.
assert credit.snapshot() == pending
credit.put(RXMB + 3 * 4, 2)
events = credit.run('PE0')
assert credit.get(RXMB + 4 * 4) == 2  # Uplink pause does not depend on TXBD.
assert not [e for e in events if e[0] in ('descriptor', 'doorbell', 'handoff')]
credit.put(BAR + REGISTERS[0], 1 << 16 | 255)
events = credit.run('PE1')
assert credit.get(TXMB + READ * 4) == 2
assert credit.get(BAR + REGISTERS[0]) == 1 << 16
assert credit.get(TXMB + 4 * 4) == 2
ack = next(i for i, e in enumerate(events)
           if e[0] == 'command_ack' and e[2] == TXMB + 16 and e[4] == 2)
assert ack > max(i for i, e in enumerate(events) if e[0] == 'doorbell')
assert not [e for e in credit.run('PE1') if e[0] in ('descriptor', 'doorbell', 'handoff')]

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
    'PE0_never_consumes_or_publishes_downlink_requests': True,
    'PE1_builds_descriptors_and_publishes_all_six_channels': True,
    'payloads_and_hardware_consumer_halfwords_preserved': True,
    'queue_wrap_and_full_backpressure_preserve_requests': True,
    'paused_PE1_drains_before_command_ACK': True,
    'PE0_pause_independent_of_TXBD_backpressure': True,
    'seven_subframe_NI_AMSDU_retains_all_payloads': True,
    'sparse_TX_activity_notifies_once_and_handles_TSF_wrap': True,
    'shared_ARM_NI_TXBD_capacity_and_pause_boundary': True,
    'downlink_runs_without_receive_context': True,
    'two_radio_private_staging_survives_DDR_poison': True,
}, indent=2))
