#!/usr/bin/env python3
"""Execute the original P6 queue flush/status code with modeled occupancy."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE


def trace(binary, cos, busy_reads, seed):
    control, status = 0x234c, 0x23d8
    base = 0x6000000
    writes, reads = [], []

    def boundary(name, args):
        raise AssertionError(('unmodeled original call', name, args))

    oracle = ModuleOracle(binary, ['aal_l2_tm_es_voq_flush_set',
        'aal_l2_tm_es_voq_status_get'], boundary)
    oracle.uc.mem_map(base, 0x10000)
    oracle.word(oracle.symbols['g_ne_reg_iobase'][0], base)
    oracle.uc.mem_write(base+control, struct.pack('<I', seed))

    def read(uc, kind, address, size, value, data):
        if address == base+status:
            # A set status bit means occupied, the original getter inverts it.
            word = 1 << (16+cos) if len(reads) < busy_reads else 0
            uc.mem_write(address, struct.pack('<I', word))
            reads.append(word)

    def write(uc, kind, address, size, value, data):
        writes.append([address-base, value])

    oracle.uc.hook_add(UC_HOOK_MEM_READ, read, begin=base, end=base+0xffff)
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, write, begin=base, end=base+0xffff)
    result = oracle.call('aal_l2_tm_es_voq_flush_set', 0, 6, cos, instruction_budget=400000)
    command = (seed & ~0x700) | cos << 8 | 0x8000
    assert writes == [[control, command], [control, command & ~0x8000]], writes
    assert result == 0
    return {'cos': cos, 'seed': seed, 'busy_reads_requested': busy_reads,
        'status_reads': len(reads), 'last_status': reads[-1], 'writes': writes, 'return': result}


def dma_completion(binary):
    freed = []
    hardware_read = 0

    def boundary(name, args):
        if name == 'aal_ni_get_tx_read_ptr':
            return hardware_read
        if name == '__ca_free_skb_any':
            freed.append((args[0]-oracle.scratch-0x1000)//256)
            return 0
        raise AssertionError(('unmodeled DMA boundary', name, args))

    oracle = ModuleOracle(binary, ['ca_dma_tx_complete_easy'], boundary)
    ring = oracle.symbols['ni_info_data'][0]
    results = []
    for software_read, hardware_read, expected in [(0, 2, []), (0, 3, [0]), (62, 2, [62, 63])]:
        oracle.uc.mem_write(ring, bytes(0x2850))
        oracle.uc.mem_write(ring+64, struct.pack('<H', software_read))
        oracle.uc.mem_write(ring+112, struct.pack('<I', 64))
        for i in range(64):
            oracle.word(ring+136+i*8, oracle.scratch+0x1000+i*256)
        freed.clear()
        result = oracle.call('ca_dma_tx_complete_easy', 0, 0)
        assert freed == expected and result == len(expected), (freed, expected, result)
        results.append({'software_read':software_read,'hardware_read':hardware_read,'freed':list(freed),
            'software_read_after':oracle.unpack(ring+64,'H')[0]})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ni', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = [trace(args.ni, cos, busy, 0xa55a20ff) for cos, busy in [(0, 0), (7, 3), (3, 10000)]]
    assert records[-1]['last_status'] == 1 << 19 and records[-1]['return'] == 0
    report = {'stock_sha256': hashlib.sha256(args.ni.read_bytes()).hexdigest(),
        'records': records, 'dma_completion': dma_completion(args.ni),
        'finding': 'P6 flush uses 0x234c COS[10:8]/enable[15], occupancy 0x23d8 bits16..23. '
            'The original fabricates empty after polling exhaustion, clears enable and returns success while still occupied.',
        'scope': 'Actual original ARM64 flush, status and DMA completion functions. Queue occupancy, DMA read pointer and skb release are modeled; no live NI/PE or MiniOLT queue.'}
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
