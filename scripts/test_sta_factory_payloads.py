#!/usr/bin/env python3
"""Compare compiled native STA CAM/CCTL with executed factory ELF traces."""
import ctypes
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/role.h').read_text()

def function(name):
    start = re.search(r'^static .*?\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end, depth = source.index('{', start) + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

common = '''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;
#define ETH_ALEN 6
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
static void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
'''
wrapper = '''
void cam(u8 *out,const u8 *self,const u8 *peer,unsigned aid,bool assoc,bool valid){
 rtl_ax_client_cam(out,1,self,peer,aid,assoc,valid);}
void cmac(u8 *out,unsigned id,bool band,unsigned count){
 rtl_ax_station_cmac_connected(out,id,band,count);rtl_ax_station_cmac_client(out);}
'''
roles = json.loads(Path(sys.argv[1]).read_text())
tables = json.loads(Path(sys.argv[2]).read_text())
assert roles['original_host_sha256'] == tables['original_sha256']
with tempfile.TemporaryDirectory(prefix='sta-factory-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + '\n'.join(function(name) for name in
        ('rtl_ax_client_cam', 'rtl_ax_station_cmac_connected', 'rtl_ax_station_cmac_client')) + wrapper)
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                    str(path / 'test.c'), '-o', str(path / 'test.so')], check=True)
    native = ctypes.CDLL(str(path / 'test.so'))
    output = ctypes.create_string_buffer(68)
    checks = 0
    for row in roles['role_programs']:
        kind = row.get('operation', 'create')
        native.cam(output, bytes.fromhex('020000961701'),
                   bytes(6) if kind == 'create' else bytes.fromhex('020000961702'),
                   0 if kind == 'create' else 42, kind == 'connect', kind != 'remove')
        actual = next(op['payload'] for op in row['operations']
                      if op.get('class') == 6 and op.get('function') == 0)
        assert output.raw[:60] == bytes.fromhex(actual), (row['chip'], kind)
        checks += 1
    for row in tables['legacy_updates'] + tables['HT_aggregation_updates']:
        native.cmac(output, row['MACID'], row['chip'] == '8852c', row.get('maximum_aggregation', 0))
        actual = next(op['payload'] for op in row['operations']
                      if op.get('class') == 5 and op.get('function') == 2)
        assert output.raw == bytes.fromhex(actual), row
        checks += 1
print(f'STA factory oracle: {checks} C/XB create/connect/disconnect/remove and CCTL aggregation payloads match byte-for-byte')
