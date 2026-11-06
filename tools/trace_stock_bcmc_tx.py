#!/usr/bin/env python3
"""Execute target BCMC PN, PHL metadata and C/XB high-queue WD production."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--wifi', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
cam = 63
lookups = []


def boundary(name, args):
    if name == 'rtw_get_stainfo':
        lookups.append(bytes(oracle.uc.mem_read(args[1], 6)))
        return station
    if name == 'rtw_phl_get_sec_cam_idx':
        assert args[1:] == [phl_station, keyid, int(group)]
        return cam
    if name in ('_ldpc_mapping', 'check_rts'):
        return 0  # PHY policy is outside the queue/security comparison.
    if name == 'platform_memcpy':
        oracle.uc.mem_write(args[1], bytes(oracle.uc.mem_read(args[2], args[3])))
        return 0
    raise AssertionError(('unmodeled BCMC boundary', name, args))


names = ['update_attrib_sec_iv_info', 'fill_txreq_mdata',
         'rtw_sec_algo_drv2phl', '_sec_algo_drv2phl', 'rtw_phl_cvt_tid_to_cat', '_phl_fill_tx_meta_data']
names += [f'{base}_{chip}' for chip in ('8852c', '8192xb')
          for base in ('txdes_proc_data', 'mac_txdesc_len')]
oracle = ModuleOracle(a.wifi, names, boundary)
adapter, station, phl_station, dvobj = 0x6000000, 0x6100000, 0x6110000, 0x6120000
mac, ops, hwinfo, dbcc, wmm = (0x6130000 + i * 0x10000 for i in range(5))
oracle.uc.mem_map(adapter, 0x30000)
for address in (station, phl_station, dvobj, mac, ops, hwinfo, dbcc, wmm):
    oracle.uc.mem_map(address, 0x10000)
frame, request, output = oracle.scratch, oracle.scratch + 0x2000, oracle.scratch + 0x4000
attrib = frame + 16
ap = bytes.fromhex('020000001111')
oracle.uc.mem_write(adapter + 0x174, ap)
oracle.word(adapter + 192, dvobj)
oracle.word(dvobj + 4544, 0x1234)
oracle.word(station, phl_station)
oracle.uc.mem_write(phl_station + 28, struct.pack('<H', 7))
oracle.word(mac + 24, ops)
oracle.word(mac + 88, hwinfo)
oracle.word(mac + 1464, dbcc)
oracle.word(dbcc + 64, wmm)
oracle.uc.mem_write(wmm, bytes([4]) * 32)
oracle.word(ops + 88, oracle.callback('platform_memcpy'))
oracle.uc.mem_write(hwinfo + 8, struct.pack('<I', 2))  # PCIe
oracle.uc.mem_write(oracle.symbols['rtw_drv_log_level'][0], bytes(4))
oracle.uc.mem_write(oracle.symbols['high_queue_debug_enable'][0], b'\0')
oracle.uc.mem_write(oracle.symbols['DBG_PRINT_MDATA_ONCE'][0], b'\0')
cases = []
for receiver in (bytes.fromhex('01005e010203'), bytes.fromhex('333300010203'), b'\xff' * 6, ap):
    group = bool(receiver[0] & 1)
    for keyid in range(4):
        for pn in (0, 0xffff, 0xffffffff, 0x123456abcdef, 0xfffffffffffe, 0xffffffffffff):
            oracle.uc.mem_write(frame, bytes(512))
            oracle.uc.mem_write(request, bytes(256))
            oracle.word(frame + 440, request)
            oracle.word(attrib + 224, station)
            oracle.uc.mem_write(attrib + 58, receiver)
            oracle.uc.mem_write(attrib + 91, b'\x04')  # CCMP
            oracle.uc.mem_write(attrib + 128, bytes([keyid]))
            oracle.word(station + 18104, pn)
            assert oracle.call('update_attrib_sec_iv_info', adapter, attrib) == 1
            next_pn = (pn + 1) & ((1 << 48) - 1)
            assert oracle.unpack(station + 18104, 'Q')[0] == next_pn
            iv = bytes(oracle.uc.mem_read(attrib + 94, 8))
            raw_pn = next_pn.to_bytes(6, 'little')
            assert iv == raw_pn[:2] + bytes([0, 0x20 | (keyid << 6 if group else 0)]) + raw_pn[2:]
            oracle.uc.mem_write(attrib + 76, struct.pack('<H', 24))
            oracle.uc.mem_write(attrib + 82, struct.pack('<H', 0x123))
            oracle.uc.mem_write(frame + 52, struct.pack('<H', 0x800))
            oracle.uc.mem_write(request + 104, struct.pack('<I', 324))
            lookups.clear()
            oracle.call('fill_txreq_mdata', adapter, frame)
            metadata = bytes(oracle.uc.mem_read(request + 32, 64))
            assert lookups == [ap if group else receiver]
            category = struct.unpack_from('<I', metadata, 4)[0]
            assert category == (9 if group else 0)
            assert bool(metadata[23] & 128) == (not group)
            assert not metadata[24] & 1  # Original BCMC embeds the IV in the packet.
            assert metadata[24] >> 4 == 6 and metadata[24] >> 1 & 3 == keyid
            assert metadata[32] == cam and metadata[25] & 1
            if group:
                assert metadata[26:32] == raw_pn
            for chip in ('8852c', '8192xb'):
                # PHL PCIe assigns its selected category's DMA channel and
                # one payload address; neither is assigned by mdata above.
                oracle.uc.mem_write(request + 32, metadata)
                oracle.uc.mem_write(request + 65, bytes([metadata[33] | category]))
                oracle.uc.mem_write(request + 108, b'\x01')
                oracle.call('_phl_fill_tx_meta_data', request, 324)
                length = oracle.call('mac_txdesc_len_' + chip, mac, request + 32)
                assert length == 56
                assert oracle.call('txdes_proc_data_' + chip, mac, request + 32, output, length) == 0
                wd = oracle.unpack(output, '14I')
                assert wd[0] >> 16 & 15 == category
                assert wd[2] >> 17 & 63 == (0x11 if group else 0)
                assert bool(wd[0] & 64) == (not group) and not wd[1] & 64
                assert wd[1] & 63 == 6 | keyid << 4 and wd[10] & 511 == 256 | cam
                if group:
                    assert wd[4] >> 16 == next_pn & 65535 and wd[5] == next_pn >> 16
            cases.append(dict(receiver=receiver.hex(), keyid=keyid, pn=pn,
                              next_pn=next_pn, iv=iv.hex(), category=category,
                              hardware_pn=not group, descriptor_pn=False))

oracle.word(attrib + 224, 0)
assert oracle.call('update_attrib_sec_iv_info', adapter, attrib) == 0
a.output.write_text(json.dumps(dict(cases=cases,
    sha256=hashlib.sha256(a.wifi.read_bytes()).hexdigest(),
    findings=[
        'Target BCMC DATA always selects category9/HIQ, DMA9 and QSEL0x11, independent of a Linux SEND_AFTER_DTIM flag.',
        'Target BCMC increments the station software PN and embeds an IV; HW_SEC_IV and SW_SEC_IV are both clear. Native GTK instead supplies the same software-owned PN through SW_SEC_IV, with hardware IV insertion.',
        'The target producer wraps the maximum48-bit PN to0. This is evidence of original behavior, not a recommended key lifetime policy.',
        'Group RA selects GTK lookup; a unicast RA selects PTK/hardware PN even if the Ethernet destination was multicast.'],
    scope='Actual target PN producer, complete PHL metadata producer, cipher/category helpers and both DATA WD encoders. Station/CAM lookup and PHY policy are modeled; PCIe channel/address assignment is supplied at the boundary. No DTIM timing, crypto, air transmission or queue retirement was executed.'), indent=2) + '\n')
print(f'Original BCMC: {len(cases)} PN/metadata and {len(cases)*2} C/XB WD cases passed')
