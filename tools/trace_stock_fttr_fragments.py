#!/usr/bin/env python3
"""Execute retained syncserv receive/reassembly; capture actual dispatch bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from trace_stock_fttr_sync import SyncOriginal


class FragmentOriginal(SyncOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols.update(receive_json=0x405760, reset_fragments=0x405498)
        self.oracle.hooks[0x40d198] = 'capture_reply'
        self.dispatches = []
        self.freed = []
        self.next_packet = self.oracle.scratch + 0x60000

    def handler(self, name, a):
        if name == 'capture_reply':
            self.dispatches.append((a[0], bytes(self.oracle.uc.mem_read(a[1], a[2]))))
            return 0
        if name == 'pktbuf_free':
            self.freed.append(a[0])
            return 0
        if name == '__assert_fail':
            raise AssertionError(('original assertion', self.string(a[0]), a[2]))
        return super().handler(name, a)

    def receive(self, body, flags, slot=5):
        # The real ingress has removed Ethernet and resolved the local slot.
        packet = self.next_packet
        self.next_packet += 0x1000
        assert self.next_packet < self.oracle.scratch + 0x180000
        data = packet + 64
        self.oracle.uc.mem_write(data, bytes([slot << 4 | 7, flags]) + body)
        self.oracle.uc.mem_write(packet, struct.pack('<QQQQI', data, data,
                                  data + len(body) + 2, data + 2048, slot))
        self.oracle.call('receive_json', packet)
        return packet


def fragments(body, count, sequence=0):
    assert 2 <= count <= 8 and 0 <= sequence <= 3
    return [(body[len(body)*i//count:len(body)*(i+1)//count],
             (count-1) << 5 | i << 2 | sequence) for i in range(count)]


def verify(binary):
    body = b'\0\0\0\x14{"RPCMethod":"Test"}'
    cases = []
    for count in range(2, 9):
        for sequence in range(4):
            for reverse in (False, True):
                stock = FragmentOriginal(binary)
                parts = fragments(body, count, sequence)
                order = list(range(count))[::(-1 if reverse else 1)]
                packets = []
                for index in order:
                    packets.append(stock.receive(*parts[index], slot=index))
                    assert len(stock.dispatches) == int(index == order[-1])
                assert bytes(stock.oracle.uc.mem_read(0x42bdc0, len(body))) == body
                # The retained binary fills the joined buffer, but dispatches
                # the last ARRIVING packet's slice, using its mapped slot.
                assert stock.dispatches == [(order[-1], parts[order[-1]][0])]
                assert sorted(stock.freed) == sorted(packets)
                cases.append({'count': count, 'sequence': sequence, 'reverse': reverse})
    stock = FragmentOriginal(binary)
    first = fragments(body, 2, 0)
    old = stock.receive(*first[0])
    second = fragments(body, 2, 1)
    stock.receive(*second[1])
    assert stock.freed == [old] and not stock.dispatches
    stock.receive(*second[0])
    assert stock.dispatches == [(5, second[0][0])]
    stock = FragmentOriginal(binary)
    stock.receive(*first[0])
    try:
        stock.receive(*first[0])
    except AssertionError as error:
        assert error.args[0][0] == 'original assertion'
    else:
        raise AssertionError('original duplicate did not assert')
    # Only count bits choose reassembly: low sequence bits are legal in a
    # single frame. An unfragmented message also retires incomplete state.
    for sequence in range(4):
        stock = FragmentOriginal(binary)
        old = stock.receive(*first[0])
        packet = stock.receive(body, sequence)
        assert stock.dispatches == [(5, body)] and sorted(stock.freed) == sorted([old, packet])
    return {'controller_sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
            'fragment_cases': len(cases), 'flags': 'count-1: bits5..7; index: bits2..4; sequence: bits0..1',
            'findings': ['all 2..8 counts and 2-bit sequences concatenate in index order',
                         'retained bug: dispatch receives last arriving slice, not joined buffer',
                         'retained bug: table/sequence shared across local peer slots',
                         'duplicate asserts; sequence change frees previous partial state',
                         'single-frame low sequence bits accepted; single frame clears fragments'],
            'scope': 'Actual original receive, pointer operations, concatenation and cleanup; packet allocation and final reply dispatch captured. Synthetic fragment bodies; no slave fragment transmitter or hardware acceptance.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = verify(args.controller)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
