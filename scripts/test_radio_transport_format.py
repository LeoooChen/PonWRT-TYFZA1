#!/usr/bin/env python3
"""Compare AP download WD/H2C packets with this board's real ARM64 encoders."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/transport.h'
names = [f'{n}_{family}' for family in ('8852c', '8192xb')
         for n in ('mac_txdesc_len', 'txdes_proc_h2c_fwdl')] + ['h2c_pkt_set_hdr_fwdl']

def handler(name, args):
    assert name == 'h2cb_push' and args[1] == 8, name
    packet = args[0]
    length = oracle.unpack(packet + 68, 'I')[0]
    oracle.uc.mem_write(packet + 68, struct.pack('<I', length + 8))
    return oracle.scratch + 0x7000

oracle = ModuleOracle(module, names, handler)
scratch = oracle.scratch
oracle.word(scratch, scratch + 0x1000)
oracle.word(scratch + 88, scratch + 0x4000)
oracle.uc.mem_write(scratch + 0x4000 + 8, struct.pack('<I', 2))  # PCIe, not USB padding mode.
source = '''#include "transport.h"
int packet(unsigned char *out,size_t capacity,const unsigned char *data,unsigned int bytes,unsigned char sequence,int section){return rtl_ax_fw_packet(out,capacity,data,bytes,sequence,section);}
void txbd(unsigned char *out,unsigned int dma,unsigned int bytes){rtl_ax_txbd(out,dma,bytes);}
'''
count = 0
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-packet-') as directory:
    work = Path(directory)
    program, binary = work / 'packet.c', work / 'packet.so'
    program.write_text(source)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    '-I' + str(header.parent), str(program), '-o', str(binary)], check=True)
    native = C.CDLL(str(binary))
    native.packet.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p, C.c_uint, C.c_ubyte, C.c_int]
    native.txbd.argtypes = [C.c_void_p, C.c_uint, C.c_uint]
    buffer = C.create_string_buffer(2048)
    for family in ('8852c', '8192xb'):
        oracle.word(scratch + 0x1000 + 520, oracle.functions['mac_txdesc_len_' + family])
        for section in (0, 1):
            for length in range(1, 2021):
                sequence = length % 256
                data = bytes((n + length) % 256 for n in range(length))
                source_data = C.create_string_buffer(data)
                result = native.packet(buffer, len(buffer), source_data, length, sequence, section)
                assert result == length + (16 if section else 24)
                payload = length if section else length + 8
                oracle.uc.mem_write(scratch + 0x6000, struct.pack('<I4xH', 4 if section else 2, payload))
                assert oracle.call('mac_txdesc_len_' + family, scratch, scratch + 0x6000) == 16
                oracle.uc.mem_write(scratch + 0x7100, bytes(16))
                assert not oracle.call('txdes_proc_h2c_fwdl_' + family,
                                       scratch, scratch + 0x6000, scratch + 0x7100, 16)
                expected = bytes(oracle.uc.mem_read(scratch + 0x7100, 16))
                if not section:
                    oracle.uc.mem_write(scratch + 110, bytes([sequence]))
                    oracle.uc.mem_write(scratch + 0x6800 + 68, struct.pack('<I', length))
                    assert not oracle.call('h2c_pkt_set_hdr_fwdl', scratch, scratch + 0x6800, 0, 1, 3, 0, 0, 0)
                    expected += bytes(oracle.uc.mem_read(scratch + 0x7000, 8))
                expected += data
                assert buffer.raw[:result] == expected, (family, section, length)
                descriptor = C.create_string_buffer(8)
                dma = (0x102000 + length * 0x1000) & 0xffffffff
                native.txbd(descriptor, dma, result)
                assert descriptor.raw == struct.pack('<II', (1 << 30) | result, dma)
                count += 1
    # Exact-capacity writes leave adjacent memory untouched. The real routine
    # is also compiled with ASan/UBSan below, using the complete length range.
unit = r'''
#include <assert.h>
#include <stdio.h>
#include "transport.h"
int main(void){unsigned char input[2021]={},out[2048];
 for(unsigned int section=0;section<2;section++)for(unsigned int bytes=0;bytes<=2020;bytes++){
  unsigned int total=bytes+(section?16:24);memset(out,0xa5,sizeof(out));
  assert(rtl_ax_fw_packet(out,total,input,bytes,0x7f,section)==(int)total);
  assert(out[total]==0xa5);
  assert(rtl_ax_fw_packet(out,total-1,input,bytes,0,section)==-ENOSPC);
 }
 assert(rtl_ax_fw_packet(out,sizeof(out),input,2021,0,1)==-ENOSPC);
 puts("Native firmware packet ASan/UBSan:all 0..2020 byte packets and bounded output passed");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-packet-sanitize-') as directory:
    work = Path(directory)
    program, binary = work / 'unit.c', work / 'unit'
    program.write_text(unit)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', '-I' + str(header.parent),
                    str(program), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
report = {'stock_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'native_transport_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
          'stock_machine_code_packet_vectors': count,
          'both_PCIe_chips_actual_WD_length': 16, 'firmware_header_has_8_byte_H2C': True,
          'section_packets_have_no_H2C_command_header': True,
          'TXBD_LS_bit': 30, 'DMA_mask': 32,
          'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False}
(root / 'logs/radio-transport-format-test.json').write_text(json.dumps(report, indent=2) + '\n')
print('Native AP transport:8080 actual stock WD/H2C packets match;32bit one-buffer TXBD format and bounded encoder passed')
