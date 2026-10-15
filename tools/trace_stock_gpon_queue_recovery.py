#!/usr/bin/env python3
"""Execute TYFZA1's GPON datapath drain, including its original PUC setters."""
from pathlib import Path
import hashlib, json, struct
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
names = ['aal_gpon_datapath_reset', 'aal_puc_cfg_get', 'aal_puc_cfg_set',
         'aal_puc_voq_valid_get', 'aal_puc_voq_valid_set', 'aal_puc_voq_drop_get',
         'aal_puc_voq_drop_set', 'aal_puc_voq_flush_by_idx', 'aal_puc_voq_flush_get',
         'aal_puc_voq_flush_set', 'aal_puc_port_voq_map_get', 'aal_puc_port_voq_map_get.part.0',
         'aal_puc_port_voq_map_set', 'aal_puc_voq_map_cfg_get',
         'aal_puc_voq_total_queue_len_mem_get', 'aal_gpon_tcont_set', 'aal_gpon_mac_ctrl_set']
base = 0x6000000
records = []
for case, byte_count, packet_count in (('empty', 0, 0), ('complete', 160, 2),
                                       ('partial', 160, 0), ('flush-timeout', 160, 2)):
    calls, writes, flushes, probes = [], [], [], []
    pvt, alloc, lengths = {}, {17: 3, 1063: 7}, {}
    if byte_count:
        lengths[11] = (byte_count, packet_count)

    def callback(name, args):
        if name == 'aal_pon_mac_mode_get':
            return 3
        if name == 'memset':
            oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
            return args[0]
        if name == '__netdev_alloc_skb':
            skb, data = oracle.scratch + 0x1000, oracle.scratch + 0x2000
            oracle.uc.mem_write(skb, bytes(256))
            oracle.word(skb + 200, data)
            return skb
        if name == '__rtk_ni_start_xmit':
            # The actual original function builds the packet and Header A.
            header = oracle.unpack(args[1], 'Q')[0]
            skb = args[0]
            length = oracle.unpack(skb + 112, 'I')[0]
            data = oracle.unpack(skb + 200, 'Q')[0]
            probes.append(dict(header=hex(header), length=length,
                               packet=bytes(oracle.uc.mem_read(data, length)).hex()))
            low = header >> 32
            queue = (((low >> 3) & 63) - 32) * 8 + (low & 7)
            queued_bytes, queued_packets = lengths.get(queue, (0, 0))
            lengths[queue] = (queued_bytes + length, queued_packets + 1)
            return 0
        if name in ('ca_spin_lock', 'ca_spin_unlock', 'ca_printf', 'printk', 'ca_udelay',
                    'ca_mdelay', 'aal_puc_qm_buf_internal_check'):
            if name in ('ca_udelay', 'ca_mdelay'):
                calls.append(dict(function=name, arguments=args))
            return 0
        raise RuntimeError((name, args))

    oracle = ModuleOracle(module, names, callback)
    oracle.uc.mem_map(base, 0x10000)
    oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0], base)

    def put(address, value):
        oracle.uc.mem_write(address, struct.pack('<I', value))

    for name, value in [('tcont_num_per_chip', 32), ('queue_num_per_tcont', 8)]:
        assert oracle.unpack(oracle.symbols[name][0], 'I')[0] == value
    for index, alloc_id in ((0, 17), (1, 1063)):
        oracle.uc.mem_write(oracle.symbols['gpon_tmp_alloc_id_cfg'][0] + index * 12,
                            struct.pack('<H2xII', alloc_id, 1, 1))
    for index in range(32):
        value = sum(((index * 8 + q) | (1 << 8)) << (q * 9) for q in range(8))
        pvt[index] = [(value >> (32 * word)) & 0xffffffff for word in range(5)]
        pvt[index][2] |= 1 << 12
    saved_pvt = {index: list(words) for index, words in pvt.items()}
    saved_alloc = dict(alloc)
    initial = {0x808c: 0x20030002, 0x80e0: 0x12000006,
               0x6114: 0x00460062, 0x61c4: 0x20000}
    initial.update({0x81bc - i * 4: 0xffff if i == 0 else 0 for i in range(8)})
    initial.update({0x8090 + i * 4: 0x55555555 for i in range(16)})
    for address, value in initial.items():
        put(base + address, value)

    def read(uc, kind, address, size, value, data):
        offset = address - base
        if offset in (0x8000, 0x802c, 0x612c, 0x80dc):
            word = oracle.unpack(address, 'I')[0]
            if offset != 0x80dc or case != 'flush-timeout':
                put(address, word & ~(1 << 31))

    def write(uc, kind, address, size, value, data):
        offset = address - base
        writes.append([offset, value])
        if offset == 0x8000 and value & (1 << 31):
            index = value & 255
            if value & (1 << 30):
                pvt[index] = [oracle.unpack(base + 0x8014 - i * 4, 'I')[0] for i in range(5)]
            else:
                for i, word in enumerate(pvt[index]):
                    put(base + 0x8014 - i * 4, word)
        elif offset == 0x802c and value & (1 << 31):
            length, packets = lengths.get(value & 255, (0, 0))
            put(base + 0x8034, length | (packets & 2047) << 21)
            put(base + 0x8030, packets >> 11)
        elif offset == 0x612c and value & (1 << 31):
            index = value & 4095
            if value & (1 << 30):
                requested = oracle.unpack(base + 0x6130, 'I')[0]
                alloc[index] = requested if value & (1 << 16) else (
                    requested & ~1) | (alloc.get(index, 0) & 1)
            else:
                put(base + 0x6130, alloc.get(index, 0))
        elif offset == 0x80dc and value & (1 << 31):
            flushes.append(value)
            if case != 'flush-timeout':
                lengths[value & 255] = (0, 0)

    oracle.uc.hook_add(UC_HOOK_MEM_READ, read, begin=base, end=base + 0xffff)
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, write, begin=base, end=base + 0xffff)
    ret = oracle.call('aal_gpon_datapath_reset', 0, instruction_budget=15000000)
    assert ret == 0 and pvt == saved_pvt and alloc == saved_alloc
    if case == 'flush-timeout':
        assert lengths[11] == (160, 2)
    else:
        assert not any(length or packets for length, packets in lengths.values())
    for address in (0x808c, 0x80e0, 0x6114, *range(0x81a0, 0x81c0, 4), *range(0x8090, 0x80d0, 4)):
        assert oracle.unpack(base + address, 'I')[0] == initial[address], hex(address)
    assert len(probes) == int(case == 'partial') and len(flushes) == int(bool(byte_count)), (case, probes, flushes)
    records.append(dict(case=case, result=ret, probes=probes,
                        flush_commands=[hex(value) for value in flushes],
                        remaining_queues=lengths, actual_mmio_writes=writes, modeled_delays=calls))

result = dict(factory_module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
              actual_functions=names, compiled_tcont_count=32, compiled_queues_per_tcont=8, records=records,
              scope='Original GPON drain and selected AAL instructions. MMIO table contents, hardware completion and counters, NI probe submission, temporary PVT buffer-policy tuning, delays, locking and logs modeled; no FIFO timing or optical proof.')
(root / 'logs/stock-gpon-queue-recovery.json').write_text(json.dumps(result, indent=2) + '\n')
print('Original GPON drain: empty/complete/partial queues restore configuration; only partial needs a probe. A stuck flush is logged but the original returns success with modeled bytes still queued.')
