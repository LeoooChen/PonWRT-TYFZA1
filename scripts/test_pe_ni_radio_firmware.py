#!/usr/bin/env python3
"""Execute NI EPP consumption, wireless gather DMA, and RPQ-gated EQ recycling."""
import json
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

binary = (Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'artifacts/live-radio-pe-ni/pe-tx.bin').read_bytes()
base, mailbox, ring, pages, headers, payload, bar, ni = (0x04000000, 0x04002000,
    0x05000000, 0x05100000, 0x05200000, 0x06000000, 0x12000000, 0x10000000)
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
for at, size in ((0x1fc00000, 16384), (base, 131072), (ring, 16384), (pages, 49152),
                 (headers, 49152), (payload, 65536), (bar, 65536), (ni, 65536)):
    u.mem_map(at, size)
u.mem_write(0x1fc00000, binary)
u.mem_map(0x10020000, 4096)
u.mem_map(0x10029000, 4096)


def put(at, value): u.mem_write(at, struct.pack('<I', value))
def get(at): return struct.unpack('<I', u.mem_read(at, 4))[0]


for index, value in ((2, 0xb2000000), (11, ring), (12, pages), (14, headers),
                     (24, 1), (25, base + 0xc000), (27, 1), (3, 1),
                     (116, 0x6380), (117, 6), (140, mailbox | 0xa0000000)):
    put(mailbox + index * 4, value)
ap = bytes.fromhex('020000000001')
u.mem_write(mailbox + 64, ap)
peer = [0, 3, 26 << 16, 0x8f | 1 << 12,
        1 << 24 | 1 << 18 | 1 << 16 | 1 << 12 | 7, 7 << 16 | 32, 0, 0]
u.mem_write(base + 0xd020, struct.pack('<8I', *peer))
put(ni + 0x6340, 0x8000007c)
put(ni + 0x6380, 0x80000000)
doorbells, returns, timer_events = [], [], []
timer_loads = []
u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
           doorbells.append((at, value)), begin=bar, end=bar + 65535)
u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
           returns.append(value), begin=ni + 0x6384, end=ni + 0x6387)
u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
           timer_events.append(value), begin=0x100290b4, end=0x100290b7)
u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, access, at, size, value, data:
           timer_loads.append(value), begin=0x100290d0, end=0x100290d3)
# Enter the linked C firmware with the same control/context layout as ARM.
# This does not execute or patch the chip's CP3/cache bootstrap.
u.reg_write(UC_MIPS_REG_A0, mailbox | 0xa0000000)
u.reg_write(UC_MIPS_REG_SP, (base + 0x1fff0) | 0xa0000000)
pc = 0xbfc00400
completion_producer = 0


def run():
    global pc
    u.emu_start(pc, 0xbfc03000, count=50000)
    pc = u.reg_read(UC_MIPS_REG_PC)


for batch in range(64):
    expected = []
    for i in range(8):
        number = batch * 8 + i
        frame = bytes.fromhex('aa6c681fe0d00257464e450188b5') + b'TYFPE-NI' + struct.pack('>I', number) + bytes([number & 255]) * 34
        address = payload + i * 2048
        compact = batch & 1
        header = struct.pack('>II', 0x80000000, (60 + 8) << 15) + bytes(8)
        u.mem_write(address, bytes(32) + header + frame)
        entry = (batch * 8 + i) * 8 & 1023
        u.mem_write(base + 0xc000 + entry,
                    struct.pack('<II', address | 0x50, 76 | (1 << 14 if compact else 0)))
        expected.append(frame)
    put(ni + 0x7060, ((batch + 1) * 64) & 1023)
    if batch == 0:
        put(mailbox + 256, 0xb00290b0)
        put(base + 0xe810, 1)
        run()
        assert get(mailbox + 112) == 0 and not doorbells
        assert get(mailbox + 152) == 8
        assert timer_events == [0, 0x80] and timer_loads == [16] and get(mailbox + 260) == 1
        run()
        assert timer_events == [0, 0x80] and timer_loads == [16]  # No repeated load for a held queue.
        put(mailbox + 264, get(mailbox + 276))
        put(base + 0xe81c, 1)  # U-APSD, one granted frame ends the service period.
        put(base + 0xe814, 1)
        run()
        assert get(mailbox + 112) == 1 and len(doorbells) == 1
        assert get(base + 0xc404) == (1 << 16 | 1 << 8 | 7 << 17 | 1 << 15)
        run()
        assert get(mailbox + 112) == 1  # No second frame without another grant.
        put(base + 0xe810, 0)
    run()
    if batch == 0:
        assert timer_events == [0, 0x80, 0, 0x80] and timer_loads == [16, 16] and get(mailbox + 260) == 0
        put(mailbox + 256, 0)
    assert get(mailbox + 112) == (batch + 1) * 8 and get(mailbox + 120) == 8
    assert len(returns) == batch * 8, 'A buffer was returned before its RPQ completion'
    for i, frame in enumerate(expected):
        page = bytes(u.mem_read(pages + (256 + i) * 128, 128))
        count = struct.unpack_from('<I', page, 4)[0] >> 26
        gathered = b''
        for n in range(count):
            length, address = struct.unpack_from('<HI', page, 64 + n * 6)
            gathered += bytes(u.mem_read(address, length & 2047))
            assert bool(length & 0x8000) == (n + 1 == count)
        service = batch == 0 and i == 0
        header = bytes.fromhex('88620000' if service else '88420000') + frame[:6] + ap + frame[6:12]
        header += struct.pack('<H', (batch * 8 + i) << 4) + bytes([16 if service else 0, 0]) + bytes.fromhex('aaaa0300000088b5')
        assert gathered == header + frame[14:]
        assert bytes(u.mem_read(payload + i * 2048 + 62, 46)) == frame[14:]
        assert struct.unpack_from('<H', page, 56)[0] & 4095 == 256 + i
        assert struct.unpack_from('<I', page, 8)[0] >> 17 & 31 == 0
        assert struct.unpack_from('<I', page, 12)[0] & 4096
    completed = list(range(8)) if batch & 1 else list(reversed(range(8)))
    for i in completed:
        put(base + 0xc404 + i * 8, 0x80000000)
        put(base + 0xc800 + completion_producer * 4, i)
        completion_producer = (completion_producer + 1) & 127
        put(mailbox + 180, completion_producer)
    # Completion alone is insufficient when the hardware return port has no credit.
    put(ni + 0x6340, 0x80000000)
    run()
    assert get(mailbox + 120) == 8 and len(returns) == batch * 8
    put(ni + 0x6340, 0x8000007c)
    run()
    assert get(mailbox + 120) == 0 and len(returns) == (batch + 1) * 8
    assert returns[-8:] == [payload + i * 2048 | 6 for i in completed]
    assert get(ni + 0x7160) == ((batch + 1) * 64) & 1023
assert len(doorbells) == 512 and get(mailbox + 116) == 512
assert doorbells == [(bar + 0x1058, (n + 1) & 255) for n in range(512)]
for n, (flags, size) in enumerate(((0, 76), (0x50, 8), (0x50, 2064))):
    u.mem_write(base + 0xc000 + n * 8, struct.pack('<II', payload | flags, size | 1 << 14))
    put(ni + 0x7060, (n + 1) * 8)
    run()
    assert len(doorbells) == 512 and get(mailbox + 160) == n + 1
    assert get(mailbox + 120) == 0 and returns[-1] == payload | 6
    assert get(mailbox + 116) == 513 + n
put(base + 0xe204, 1 << 1 | 1 << 16)
for n, number in enumerate((512, 544)):
    put(mailbox + 112, number)
    put(mailbox + 224, number)
    u.mem_write(base + 0xc000 + (3 + n) * 8, struct.pack('<II', payload | 0x50, 76 | 1 << 14))
    put(ni + 0x7060, (4 + n) * 8)
    run()
    rate = get(base + 0xe000)
    assert rate == (0x8f | 1 << 12 if n == 0 else 0x81 | 1 << 12 | 1 << 19)
    assert get(pages + 256 * 128 + 28) >> 16 & 511 == rate & 511
    put(base + 0xc800 + completion_producer * 4, 0)
    completion_producer = (completion_producer + 1) & 127
    put(mailbox + 180, completion_producer)
    run()
for padding in range(4):
    frames = []
    for n, length in enumerate((64 - padding, 67 + padding)):
        ethernet = bytes.fromhex('aa6c681fe0d00257464e45010800') + bytes([padding * 2 + n]) * (length - 14)
        frames.append(ethernet)
        at = payload + n * 2048
        u.mem_write(at, bytes(32) + struct.pack('>II', 0x80000000, (length + 8) << 15) + bytes(8) + ethernet)
        u.mem_write(base + 0xc000 + (5 + padding * 2 + n) * 8,
                    struct.pack('<II', at | 0x50, length + 16 | 1 << 14))
    put(base + 0xe204, 3839 << 18)
    put(ni + 0x7060, (7 + padding * 2) * 8)
    old_sent, old_mpdu = get(mailbox + 112), get(mailbox + 224)
    sequence = get(base + 0x3000 + 8 * 4)
    run()
    assert get(mailbox + 112) == old_sent + 2 and get(mailbox + 224) == old_mpdu + 1
    assert get(mailbox + 228) == padding + 1 and get(mailbox + 120) == 1
    page = bytes(u.mem_read(pages + 256 * 128, 128))
    count = struct.unpack_from('<I', page, 4)[0] >> 26
    assert count == 2
    gathered = b''
    for n in range(count):
        length, address = struct.unpack_from('<HI', page, 64 + n * 6)
        gathered += bytes(u.mem_read(address, length & 2047))
        assert bool(length & 0x8000) == (n == count - 1)
    expected = bytes.fromhex('88420000') + frames[0][:6] + ap + ap
    expected += struct.pack('<H', sequence << 4) + bytes([128, 0])
    for n, frame in enumerate(frames):
        if n: expected += bytes(padding)
        expected += frame[:12] + struct.pack('>H', len(frame) - 6) + bytes.fromhex('aaaa030000000800') + frame[14:]
    assert gathered == expected
    assert struct.unpack_from('<I', page, 8)[0] & 0x3fff == len(gathered)
    assert get(base + 0xc404) & (1 << 15) and get(base + 0xc408) == 0
    assert get(payload) == payload + 2048 and get(payload + 2048) == 0
    for n, frame in enumerate(frames):
        assert bytes(u.mem_read(payload + n * 2048 + 62, len(frame) - 14)) == frame[14:]
    put(base + 0xc800 + completion_producer * 4, 0)
    completion_producer = (completion_producer + 1) & 127
    put(mailbox + 180, completion_producer)
    run()
    assert get(mailbox + 120) == 0 and returns[-2:] == [payload | 6, payload + 2048 | 6]
put(base + 0xe204, 3839 << 18 | 1 << 17 | 1)
put(mailbox + 224, 512)  # Would probe MCS0 on the software-controlled path.
for n, frame in enumerate(frames):
    u.mem_write(base + 0xc000 + (13 + n) * 8,
                struct.pack('<II', payload + n * 2048 | 0x50, len(frame) + 16 | 1 << 14))
put(ni + 0x7060, 15 * 8)
run()
assert get(mailbox + 224) == 513 and get(mailbox + 120) == 1
assert get(base + 0xe000) & (1 << 20) and not get(base + 0xe000) & (1 << 19)
assert not get(pages + 256 * 128 + 28) & (1 << 31)
# Actual factory DATA builder: firmware RA clears the disable-fallback bits.
assert get(pages + 256 * 128 + 32) == 0
put(base + 0xc800 + completion_producer * 4, 0)
completion_producer = (completion_producer + 1) & 127
put(mailbox + 180, completion_producer)
run()
assert get(mailbox + 120) == 0
old_doorbells, old_recycled = len(doorbells), get(mailbox + 116)
put(base + 0xe810, 1)
put(mailbox + 248, 1)
for n in range(2):
    u.mem_write(base + 0xc000 + (15 + n) * 8,
                struct.pack('<II', payload + n * 2048 | 0x50, 76 | 1 << 14))
put(ni + 0x7060, 17 * 8)
run()
assert len(doorbells) == old_doorbells
assert get(mailbox + 116) == old_recycled + 2 and get(mailbox + 120) == 0
assert get(mailbox + 252) == 2 and get(ni + 0x7160) == 17 * 8
record = dict(linked_MIPS_C_firmware=True, NI_packets=512, full_and_compact_EPP=True,
              exact_wireless_headers_and_gathered_payload=True, no_payload_copy=True,
              no_EQ_return_before_RPQ=True, no_EQ_return_without_hardware_credit=True,
              NI_TID0_queue_matches_header=True, Block_ACK_configuration_preserved=True,
              sleeping_peer_holds_EPP=True, one_service_grant_one_frame=True,
              UAPSD_EOSP_and_More_Data=True, awake_drains_remaining_frames=True,
              invalid_EPP_recycled_without_DMA=True,
              RPQ_completion_FIFO_wraps=4,
              rate_probes_match_peer_HT_mask=True, RPQ_actual_rate_recorded=True,
              firmware_RA_clears_fixed_rate_and_skips_host_probes=True,
              retired_sleeping_peer_returns_EPP_without_wireless_TX=True,
              PE_timer_event_only_on_buffered_state_changes=True,
              CP3_instruction_memory_not_emulated=True,
              two_frame_AMSDU_padding_vectors=4, AMSDU_headers_payload_and_DMA_bounds_exact=True,
              NI_prefixes_reused_without_payload_copy=True,
              AMSDU_buffers_retained_until_RPQ=True,
              EPP_wraps=4, PCIe_producer_wraps=2, hardware_test=False)
(root / 'logs/pe-ni-radio-firmware-test.json').write_text(json.dumps(record, indent=2) + '\n')
print('Linked PE NI-to-radio firmware: 512 exact frames, EPP/PCIe wraps, and RPQ/credit-gated buffer ownership passed')
