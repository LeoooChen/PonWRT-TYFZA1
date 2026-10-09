#!/usr/bin/env python3
"""Validate the native format using four original AP images and segment oracles."""
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from radio_inputs import HEADER, NativeRadioFormat

study = root / json.loads((root / 'manifests/wireless-study-current.json').read_text())['snapshot']
oracle = json.loads((study / 'evidence/derived/stock-wifi-firmware-manifest.json').read_text())
format = NativeRadioFormat()
reports = []
try:
    for name, expected in oracle['firmwares'].items():
        data = (study / expected['extracted_path']).read_bytes()
        image = format.parse(data)
        assert (image.cut, list(image.version), image.header_bytes) == (
            expected['cut_id'], expected['version'], expected['header_length'])
        assert image.section_count == len(expected['sections'])
        assert hashlib.sha256(data).hexdigest() == expected['sha256']
        hardware_chip = 0x52 if '8852c' in name else 0x53
        cv = image.cut - 1
        assert not format.library.match(hardware_chip, cv, ctypes.byref(image))
        assert format.library.match(hardware_chip ^ 1, cv, ctypes.byref(image)) < 0
        assert format.library.match(hardware_chip, not cv, ctypes.byref(image)) < 0
        header = format.header(data, image)
        assert len(header) == 96 and header[:28] == data[:28] and header[30:] == data[30:96]
        assert struct.unpack_from('<H', header, 28)[0] == 2020
        for section, original in zip(image.sections, expected['sections']):
            payload = data[section.offset:section.offset + section.bytes]
            assert (section.address, section.offset, section.bytes, section.type) == (
                int(original['device_load_address'], 16), original['offset'], original['stored_size'], original['type'])
            assert hashlib.sha256(payload).hexdigest() == original['sha256']
            position, chunks = 0, []
            while position < len(payload):
                length = format.library.chunk(len(payload) - position)
                assert 0 < length <= 2020
                chunks.append(payload[position:position + length])
                position += length
            assert b''.join(chunks) == payload
        for candidate in (data[:31], data[:-1], data + b'\0'):
            try:
                format.parse(candidate)
            except ValueError:
                pass
            else:
                raise AssertionError('Incomplete or overlong image accepted')
        reports.append({'image': name, 'sections': image.section_count,
                        'download_header_bytes': len(header), 'sha256': expected['sha256']})
    for chip, family in ((2, '8852c'), (3, '8192xb')):
        for cv in range(9):
            assert format.library.name(chip, cv).decode().endswith(f'rtl{family}-u{2 if cv else 1}-ap.bin')
    assert format.library.name(4, 0) is None
finally:
    format.close()

unit = r'''
#include <assert.h>
#include <stdio.h>
#include "fw.h"
int main(void){
 unsigned char bytes[160]={};struct rtl_ax_ap_image image;
 for(unsigned int n=0;n<32;n++)assert(rtl_ax_ap_parse(bytes,n,&image)==-EINVAL);
 bytes[14]=160;bytes[25]=4;bytes[30]=1;bytes[96]=64;
 assert(!rtl_ax_ap_parse(bytes,160,&image));
 unsigned char header[96];assert(rtl_ax_ap_download_header(bytes,&image,header,95)==-ENOSPC);
 assert(rtl_ax_ap_download_header(bytes,&image,header,96)==96);
 bytes[96]=63;assert(rtl_ax_ap_parse(bytes,160,&image)==-EINVAL);bytes[96]=64;
 bytes[36]=255;bytes[37]=255;bytes[38]=255;assert(rtl_ax_ap_parse(bytes,160,&image)==-EINVAL);
 bytes[25]=7;assert(rtl_ax_ap_parse(bytes,160,&image)==-EINVAL);
 puts("Native AP firmware:short header,dynamic extent,section overrun and bounded download header passed");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-fw-sanitize-') as name:
    work = Path(name)
    source, binary = work / 'format.c', work / 'format'
    source.write_text(unit)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', '-I' + str(HEADER.parent),
                    str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/radio-firmware-format-test.json').write_text(json.dumps({
    'native_header_sha256': hashlib.sha256(HEADER.read_bytes()).hexdigest(),
    'original_images': reports, 'section_payloads_and_addresses_match_oracle': True,
    'chunk_reassembly_exact': True, 'host_cv_selection_vectors': 18,
    'sanitizers': ['ASan', 'UBSan'], 'radio_driver_or_hardware_test': False,
}, indent=2) + '\n')
print('Native radio firmware:four exact original images,96-byte phase1 header and2020-byte section chunks verified')
