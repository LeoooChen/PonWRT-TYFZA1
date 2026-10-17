#!/usr/bin/env python3
"""Execute original P6 initialization, MAC and profile loader with modeled MMIO."""
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE


def trace(module, original, mode, nway, seed):
    records = []
    source = Path(original) / ('p6_serdes_usxgmii_10g.txt' if mode == 9 else 'p6_serdes_sgmii_2g5.txt')
    revision = source.with_name(source.stem + '_C.txt')
    if revision.exists():
        source = revision
    rows = [tuple(int(word, 0) for word in line.split()) for line in source.read_text().splitlines()
            if line.strip().startswith('0x')]

    def callback(name, args):
        if name == '__get_chip_revision':
            return 67
        if name == 'load_p6_serdes':
            assert args[0] == (5 if mode == 9 else 1)
            header, data = oracle.scratch + 0x500, oracle.scratch + 0x1000
            oracle.uc.mem_write(data, b''.join(struct.pack('<IIII', *row) for row in rows))
            oracle.uc.mem_write(header + 16, struct.pack('<Qiii', data, 2, len(rows), 1))
            return header
        if name not in ('ca_printf', 'ca_udelay', 'ca_mdelay', 'free_serdes'):
            raise AssertionError(('unmodeled original call', name, args))
        return 0

    selected = ['aal_sds_mode_set', '__p6_mac_1g_config', '__p6_mac_10g_config',
        '__p6_serdes_ind_read', '__p6_serdes_ind_write', '__p6_sds_status_get',
        '__p6_prof_load_from_file', 'aal_sds_nway_set', 'aal_ni_xge_mac_frame_len_set',
        'aal_ni_pkt_len_set']
    oracle = ModuleOracle(module, selected, callback)
    for index, name in enumerate(['g_glb_reg_iobase', 'g_ne_qx_reg_iobase', 'g_ne_reg_iobase']):
        address = 0x6000000 + index * 0x10000
        oracle.uc.mem_map(address, 0x10000)
        oracle.uc.mem_write(address, struct.pack('<I', seed) * (0x10000 // 4))
        oracle.word(oracle.symbols[name][0], address)
    oracle.uc.mem_write(0x6010058, struct.pack('<I', seed | 0xe000))
    oracle.uc.mem_write(0x6010054, struct.pack('<I', 0x111))

    def write(uc, kind, address, size, value, data):
        region = (address - 0x6000000) // 0x10000
        records.append([region, address & 0xffff, value])

    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, write, begin=0x6000000, end=0x602ffff)
    assert oracle.call('aal_sds_mode_set', 0, 6, 4 if mode == 9 else 1,
                       5 if mode == 9 else 3, instruction_budget=300000) == 0
    assert oracle.call('aal_sds_nway_set', 0, 6, int(not nway)) == 0
    data = oracle.scratch + 0x600
    oracle.uc.mem_write(data, struct.pack('<HHI', 0, 2000, 0))
    assert oracle.call('aal_ni_xge_mac_frame_len_set', 0, 0, 2, data) == 0
    if (seed & 0x3fff) < 2000:
        assert oracle.call('aal_ni_pkt_len_set', 0, 2, data) == 0
    return records, source
