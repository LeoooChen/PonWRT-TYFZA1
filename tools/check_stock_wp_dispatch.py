#!/usr/bin/env python3
"""Execute stock RPQ dispatch while the ordinary RX pool is unavailable."""
from pathlib import Path
import hashlib
import json
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_X4, UC_ARM64_REG_X5

module = ROOT / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
digest = hashlib.sha256(module.read_bytes()).hexdigest()
assert digest == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
PHL, HCI, COMMON, POOLS, BD, RX, REPORT = [0x6000000 + i * 0x10000 for i in range(7)]
calls = []


def callback(name, args):
    calls.append(name)
    if name == '_phl_indic_new_rxpkt':
        return 0
    if name == 'rtw_hal_rx_res_query':
        assert args[1] == 1
        return 1
    if name == 'phl_get_single_rx':
        assert args[2] == 1
        oracle.word(args[3], RX)
        return 0
    if name == 'rtw_hal_handle_wp_rpt':
        assert args[1] == REPORT and args[2] == 4
        channel = oracle.uc.reg_read(UC_ARM64_REG_X4)
        sequence = oracle.uc.reg_read(UC_ARM64_REG_X5)
        status = oracle.unpack(oracle.uc.reg_read(UC_ARM64_REG_SP), 'Q')[0]
        oracle.uc.mem_write(channel, b'\x00')
        oracle.uc.mem_write(sequence, struct.pack('<H', 17))
        oracle.uc.mem_write(status, b'\x00')
        return 4
    if name == 'phl_recycle_payload':
        assert args[:4] == [PHL, 0, 17, 0]
        return 0
    if name in ('phl_rx_wp_report_record_sts', 'phl_recycle_rx_buf', '_phl_refill_rxbd'):
        return 0
    raise AssertionError((name, args))


oracle = ModuleOracle(module, ['phl_rx_pcie', '_phl_rx_handle_wp_report'], callback,
                      {'vabits_actual': 0x500a000})
oracle.uc.mem_map(PHL, 0x80000)
oracle.word(0x500a000, 48)
oracle.word(PHL + 23840, HCI)
oracle.word(PHL + 4648, COMMON)
oracle.word(HCI + 40, POOLS)
oracle.word(HCI + 16, BD)
oracle.uc.mem_write(HCI + 1, b'\x02')
# RX channel 0 has no idle payload; channel 1 has one WP report available.
oracle.uc.mem_write(POOLS + 80 + 40, struct.pack('<H', 1))
oracle.uc.mem_write(RX + 16, struct.pack('<I', 4))
oracle.word(RX + 152, REPORT)
oracle.uc.mem_write(RX + 168, struct.pack('<H', 4))
oracle.uc.mem_write(REPORT, struct.pack('<I', 17 << 16 | 1))
oracle.call('phl_rx_pcie', PHL)
assert calls == ['_phl_indic_new_rxpkt', 'rtw_hal_rx_res_query', 'phl_get_single_rx',
                 'rtw_hal_handle_wp_rpt', 'phl_rx_wp_report_record_sts',
                 'phl_recycle_payload', 'phl_recycle_rx_buf', '_phl_refill_rxbd']
print(json.dumps({
    'factory_module_sha256': digest,
    'actual_functions': ['phl_rx_pcie@0x197454', '_phl_rx_handle_wp_report@0x197328'],
    'ordinary_RX_idle_buffers': 0,
    'WP_report_dispatched_and_payload_recycled': True,
    'calls': calls,
    'scope': 'Actual dispatch and WP loop. Hardware resource query, single RX fetch, '
             'report decoder and release/refill callees are modeled. Proves dispatch '
             'separation, not radio timing or a factory ARM-to-PE FIFO implementation.'
}, indent=2))
