#!/usr/bin/env python3
"""Execute P6 queue setup against stock profile selection and real CB setters."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--stock-project', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()
sys.argv = [str(root / 'tools/trace_stock_qm_contract.py'), '--p6', '--stock-project',
            str(args.stock_project), '--output-dir', str(args.output.parent)]
stock = runpy.run_path(sys.argv[0])
o, base, tables = stock['oracle'], stock['ni'], stock['tables']
assert stock['p6_contract'] == {'eq_profile': 32, 'port14_profile': 0, 'credit_profile': 2,
                               'voq112_profile': 2, 'deepq_demux': 0, 'qm_return_l2tm': 1}
# Replay the exact setters with native allocation counts; reserved bits start zero.
ptr = o.scratch+0x1000
low, count = 512*2//3, 512
for reg in (0x9588, 0x95a0):
    o.uc.mem_write(base+reg, bytes(4))
tables[0x95a8][14] = [0]
tables[0x95b0][2] = [0, 0, 0]
o.uc.mem_write(ptr, struct.pack('<IH2xIH2x', 1, 512, 1, 512))
assert o.call('aal_l3_te_cb_port_free_buf_cnt_set', 0, 14, 15, ptr) == 0
o.uc.mem_write(ptr, struct.pack('<5H', 25, 512, low, 0, 0))
assert o.call('aal_l3_te_cb_voq_threshold_profile_set', 0, 2, 31, ptr) == 0
o.uc.mem_write(ptr, struct.pack('<5H', 0, low*2, low, 0, 0))
assert o.call('aal_l3_te_cb_port_threshold_profile_set', 0, 2, 6, ptr) == 0
assert o.call('aal_l3_te_cb_port_profile_sel_set.part.0', 14, 2) == 0
for q in range(112, 120):
    assert o.call('aal_l3_te_cb_voq_profile_sel_set.part.0', q, 2) == 0
expected = {'registers': {reg: o.unpack(base+reg, 'I')[0] for reg in (0x9560, 0x9588, 0x9598, 0x95a0, 0x963c)},
            'credits': tables[0x95a8][14], 'threshold': tables[0x95b0][2],
            'backpressure': {i: tables[0x9654][i] for i in range(8)}}
ni_binary = args.stock_project / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
# The pool can live in native DRAM: confirm the actual DRAM EQ0 builder's
# fields with matching count/BID and relocated address. Cache/AXI attributes
# differ deliberately because Linux supplies a coherent allocation.
def pool_boundary(name, values):
    if name == 'printk': return 0
    if name == 'aal_l3_te_cb_port_free_buf_cnt_set': return 0
    raise AssertionError((name, values))
pool = stock['ModuleOracle'](ni_binary, ['aal_l3qm_init_DQ_shared_dram.constprop.0',
    'aal_l3qm_calc_jumbo_buf_count', 'aal_l3qm_get_buffer_size_index'], pool_boundary)
pool.uc.mem_map(base, 65536)
pool.word(pool.symbols['g_ne_reg_iobase'][0], base)
for name, value in [('l3qm_eq_profile_dq_pool1_buf_count', 1536),
                    ('l3qm_eq_profile_dq_pool1_buf_sz', 2048), ('DQ_bid_start', 6528)]:
    pool.uc.mem_write(pool.symbols[name][0], struct.pack('<I', value))
pool.uc.mem_write(pool.symbols['l3qm_eq_share_pool_id'][0], bytes([2]))
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_SP, UC_ARM64_REG_PC
for reg, value in ((UC_ARM64_REG_X0,0),(UC_ARM64_REG_X1,0),(UC_ARM64_REG_X2,0x08000000),
                   (UC_ARM64_REG_SP,pool.scratch+0xff00)):
    pool.uc.reg_write(reg,value)
start = pool.functions['aal_l3qm_init_DQ_shared_dram.constprop.0']
pool.uc.emu_start(start,start+0x378,count=100000)  # after private EQ0, before shared-pool changes
assert pool.uc.reg_read(UC_ARM64_REG_PC) == start+0x378
assert [pool.unpack(base+r, 'I')[0] for r in (0x61e8,0x61ec,0x61f0)] == [0x08000001,(512<<16)|6528,5]
headers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
shim = r'''
#include "rtl9617c-fttr-qm.h"
int prepare(unsigned int address,unsigned int bid,
 unsigned int (*read)(void *,unsigned int),void (*write)(void *,unsigned int,unsigned int),
 int (*indirect)(void *,unsigned int,unsigned int)) {
 const struct rtl_dq_io io={0,read,write,indirect};
 return rtl_fttr_qm_prepare(&io,address,bid);
}
'''
with tempfile.TemporaryDirectory(prefix='fttr-qm-') as temp:
    work = Path(temp)
    (work/'test.c').write_text(shim)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-I'+str(headers),
                    str(work/'test.c'), '-o', str(work/'test.so')], check=True)
    lib = C.CDLL(str(work/'test.so'))
    regs, entries, accesses = {}, {}, []
    fail = 0
    def read(ctx, reg):
        return regs.get(reg, 0)
    def write(ctx, reg, value):
        regs[reg] = value
    def indirect(ctx, reg, value):
        accesses.append((reg, value))
        if len(accesses) == fail:
            return -110
        assert value & 0xc0000000 == 0xc0000000
        data = {0x95a8: [0x95ac], 0x95b0: [0x95bc, 0x95b8, 0x95b4],
                0x95c0: [0x95c4], 0x9654: [0x9658]}[reg]
        entries[reg, value & 255] = [regs[x] for x in data]
        return 0
    rd = C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint)(read)
    wr = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint)(write)
    ind = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint)(indirect)
    # Port/COS bits must be independent of inherited neighboring settings.
    for seed in (0, 0xffffffff, 0x5a5aa5a5):
        regs = {reg: seed for reg in range(0, 65536, 4)}
        entries, accesses = {}, []
        assert lib.prepare(0x08000000, 6528, rd, wr, ind) == 0
        assert [regs[r] for r in (0x61e8,0x61ec,0x61f0,0x61f4,0x61f8)] == [0x08000001,(512<<16)|6528,5,0x61008060,2]
        assert regs[0x6128] == 0x20 and regs[0x6180] == 0
        assert regs[0x9560] == expected['registers'][0x9560]
        assert regs[0x95a0] == (seed & ~(3<<28)) | expected['registers'][0x95a0]
        for reg in (0x9588, 0x9598, 0x963c):
            assert regs[reg] == expected['registers'][reg], (hex(reg), hex(regs[reg]), expected)
        assert entries[0x95a8,14] == expected['credits']
        assert entries[0x95b0,2] == expected['threshold']
        assert {q: entries[0x9654,q] for q in range(8)} == expected['backpressure']
        for q in range(112,120): assert entries[0x95c0,q] == [2]
        assert regs[0xa1c8] == seed | (1<<6)
        assert regs[0x63c4+14*4] == 255
        assert regs[0x6444+14*8] == regs[0x6448+14*8] == 0x01010101
        changed = {r for r,v in regs.items() if v != seed}
        allowed = {0x61e8,0x61ec,0x61f0,0x61f4,0x61f8,0x6128,0x6180,0x9560,
                   0x9588,0x9598,0x95a0,0x95ac,0x95b4,0x95b8,0x95bc,0x963c,0x9658,0x95c4,
                   0x63c4+14*4,0x6444+14*8,0x6448+14*8,0xa1c8}
        assert changed <= allowed
    # Every failed indirect operation stops setup; never enable the engines here.
    operations = len(accesses)
    for fail in range(1, operations+1):
        regs, entries, accesses = {}, {}, []
        assert lib.prepare(0x08000000, 6528, rd, wr, ind) == -110
        assert len(accesses) == fail and 0x6104 not in regs and 0x6108 not in regs
report = {'native_header_sha256': hashlib.sha256((headers/'rtl9617c-fttr-qm.h').read_bytes()).hexdigest(),
          'stock_p6': stock['p6_contract'], 'adapted_CB_setters': expected,
          'seed_cases': 3, 'failure_cases': operations, 'native_private_BIDs': [6528, 7039],
          'scope': 'actual original mapping/CB initialization and threshold setters; native setup with modeled MMIO; no physical queue or throughput acceptance'}
args.output.write_text(json.dumps(report, indent=2)+'\n')
print(f'P6 QM: stock port14/EQ0/CB2, native pool/credits and {operations} error cutoffs passed')
