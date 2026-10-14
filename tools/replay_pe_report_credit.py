#!/usr/bin/env python3
"""Execute PE ingest/select/RPQ/recycle with count-triggered completion reports."""
from pathlib import Path
import json
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2
from unicorn.mips_const import UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_V0, UC_MIPS_REG_PC

out = ROOT / 'artifacts/report-credit63'
out.mkdir(exist_ok=True)
(out / 'credit.c').write_text('#define PE_UPLINK\n#include "' + str(ROOT / 'port/pe/rtl9617c-radio-wd.c') + '"\n' + r'''
u32 credit_step(volatile u32 *mailbox, u8 *memory, u32 *request)
{
    pe_rpq(mailbox, memory);
    ni_recycle(mailbox, memory);
    ni_ingest(mailbox, memory);
    ni_ingest(mailbox, memory);
    return ni_select(mailbox, memory, request);
}
''')
(out / 'credit.ld').write_text('ENTRY(credit_step)\nSECTIONS { . = 0xbfc00000; .text : { *(.text*) *(.rxq) *(.rodata*) } /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.pdr) *(.comment) } }\n')
subprocess.run(['clang', '--target=mipsel-none-elf', '-march=mips32', '-msoft-float', '-mno-abicalls', '-fno-pic', '-G0', '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-ffunction-sections', '-fdata-sections', '-O2', '-c', str(out / 'credit.c'), '-o', str(out / 'credit.o')], check=True)
subprocess.run([str(ROOT / 'artifacts/llvm-linker/usr/lib/llvm-18/bin/ld.lld'), '-m', 'elf32ltsmip', '--gc-sections', '-T', str(out / 'credit.ld'), str(out / 'credit.o'), '-o', str(out / 'credit.elf')], check=True)
subprocess.run(['llvm-objcopy-18', '-O', 'binary', str(out / 'credit.elf'), str(out / 'credit.bin')], check=True)
symbols = subprocess.check_output(['llvm-nm-18', str(out / 'credit.elf')], text=True)
entry = next(int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(' credit_step'))


def replay(report_batch):
    base, mailbox, ni, bar = 0x04000000, 0x04002000, 0x10000000, 0x11000000
    payload, rpq, forward, request = 0x05300000, 0x05400000, 0x05500000, base + 0x4000
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in [(0x1fc00000, 0x10000), (base, 0x20000), (ni, 0x10000),
                     (bar, 0x10000), (payload, 0x100000), (rpq, 0x20000), (forward, 0x20000)]:
        u.mem_map(at, size)
    u.mem_write(0x1fc00000, (out / 'credit.bin').read_bytes())
    put = lambda at, value: u.mem_write(at, struct.pack('<I', value))
    get = lambda at: struct.unpack('<I', u.mem_read(at, 4))[0]
    for index, value in [(2, bar | 0xa0000000), (25, base + 0x18000), (68, rpq),
                         (72, forward), (114, 0x7060), (115, 0x7160), (116, 0x6380), (117, 6)]:
        put(mailbox + index * 4, value)
    mac = bytes.fromhex('067afb78b287')
    u.mem_write(base + 0xe410, mac)
    put(base + 0x11a08, 1)
    put(base + 0xe208, 3839 << 18 | 1 << 17)
    put(base + 0x12040, 64)
    u.mem_write(base + 0xd040, struct.pack('<8I', 0, 0, 26 << 16, 0x20100,
                2 << 24 | 1 << 18 | 1 << 16, 3 << 16 | 64, 0, 0))
    put(ni + 0x6340, 0x800001fc)
    put(ni + 0x6380, 0x80000000)
    total = 248
    for n in range(total):
        frame = mac + bytes.fromhex('0050c20102000800') + struct.pack('>I', n) + bytes(1496)
        address = payload + n * 2048
        u.mem_write(address, bytes(32) + struct.pack('>II', 0x80000000, 1522 << 15) + bytes(8) + frame)
        u.mem_write(base + 0x18000 + n * 8, struct.pack('<II', address | 0x50, 1530 | 1 << 14))
    put(ni + 0x7060, total * 8)
    queued, returned, emitted, tx_frames = [], [], [], []
    producer, tag = 0, 0

    def report(count):
        nonlocal producer, tag
        entries = queued[:count]
        del queued[:count]
        tag = tag % 8191 + 1
        raw = struct.pack('<II', (20 + count * 4) | 0xc000 | tag << 16, 7 << 24 | count * 4)
        raw += bytes(12) + struct.pack('<%dI' % count, *entries)
        u.mem_write(rpq + producer * 512, raw)
        producer = (producer + 1) & 255
        put(bar + 0x121c, producer << 16 | (get(bar + 0x121c) & 65535))
        emitted.append(count)

    def write(uc, access, at, size, value, data):
        if at & 0x1fffffff == ni + 0x6384:
            returned.append(value)

    u.hook_add(UC_HOOK_MEM_WRITE, write)

    def run():
        for _ in range(150):
            for reg, value in [(UC_MIPS_REG_A0, mailbox), (UC_MIPS_REG_A1, base),
                               (UC_MIPS_REG_A2, request), (UC_MIPS_REG_SP, base + 0xfff0)]:
                u.reg_write(reg, value | 0xa0000000)
            u.reg_write(UC_MIPS_REG_RA, 0xbfc0fff0)
            u.emu_start(entry, 0xbfc0fff0, count=100000)
            assert u.reg_read(UC_MIPS_REG_PC) == 0xbfc0fff0
            count = u.reg_read(UC_MIPS_REG_V0)
            if not count:
                continue
            assert count == 2
            req = struct.unpack('<12I', u.mem_read(request, 48))
            for address, length in [(req[8], req[9]), (req[10], req[11])]:
                assert length == 1514
                ethernet = bytes(u.mem_read(address, length))
                assert ethernet[:6] == mac and ethernet[18:] == bytes(1496)
                tx_frames.append(struct.unpack_from('>I', ethernet, 14)[0])
            queued.append(req[0] << 16 | 2)
            if len(queued) >= report_batch:
                report(report_batch)

    run()
    before_timer = dict(sent=len(tx_frames), pending=get(mailbox + 30 * 4),
                        MPDU=len(tx_frames) // 2, reports=get(mailbox + 75 * 4),
                        pending_reports=len(queued), returned=len(returned))
    # Publish residual reports as a timer would, without inventing its duration.
    while queued:
        report(len(queued))
        run()
    assert tx_frames == list(range(total))
    assert len(returned) == total and len(set(returned)) == total
    assert get(mailbox + 30 * 4) == 0 and get(mailbox + 75 * 4) == total // 2
    return dict(report_batch=report_batch, before_timer=before_timer,
                report_packets=emitted, after_timer_sent=len(tx_frames),
                after_timer_returned=len(returned), exact_payload_order=True)


old, new = replay(121), replay(30)
assert old['before_timer']['sent'] == 124 and old['before_timer']['reports'] == 0
assert new['before_timer']['sent'] == 248 and new['before_timer']['reports'] == 120
print(json.dumps(dict(scope='Current PE ingest/select/RPQ/recycle compiled together for MIPS with a callable wrapper. Immediate wireless completion and count/timer report triggers are modeled; no PHY, cache, main-loop timing or throughput simulation.', stock_batch=old, mainline_batch=new), indent=2))
