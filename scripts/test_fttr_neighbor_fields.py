#!/usr/bin/env python3
"""Compare native neighbor fields with actual target manager output and reject malformed rows."""
import argparse
import ctypes as C
import errno
import json
from pathlib import Path
import subprocess
import tempfile
from trace_stock_fttr_neighbor_fields import verify

root=Path(__file__).resolve().parents[1]; src=root/'port/packages/tyfza1-fttr/src'
p=argparse.ArgumentParser(description=__doc__)
for name in ('manager','mib','output'):p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();original=verify(a.manager,a.mib)
with tempfile.TemporaryDirectory() as t:
    d=Path(t)
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',
                    *[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],
                    '-ljson-c','-o',str(d/'client.so')],check=True)
    lib=C.CDLL(str(d/'client.so'))
    lib.json_tokener_parse.argtypes=[C.c_char_p];lib.json_tokener_parse.restype=C.c_void_p
    lib.json_object_put.argtypes=[C.c_void_p]
    lib.json_object_to_json_string_ext.argtypes=[C.c_void_p,C.c_int];lib.json_object_to_json_string_ext.restype=C.c_char_p
    lib.fttr_sync_neighbor_fields.argtypes=[C.c_void_p,C.c_void_p]
    peer=(C.c_ubyte*6).from_buffer_copy(bytes.fromhex('0a0b0c0d0e0f'))
    cases=[]
    def run(text,expected,error=0):
        obj=lib.json_tokener_parse(json.dumps(dict(scanResult=text,scan_complete=False)).encode())
        ret=lib.fttr_sync_neighbor_fields(obj,peer)
        result=json.loads(lib.json_object_to_json_string_ext(obj,0));lib.json_object_put(obj)
        assert ret==error,(text,ret,error)
        assert result['scanResult']==text and not result['scan_complete']
        if error:assert 'neighbors' not in result
        else:assert result['neighbors']==expected,(text,result,expected)
        cases.append(dict(bytes=len(text.encode()),error=ret))
    for row in original['cases']:
        for ending in ('\n','\r\n',''):
            run(row['line'].rstrip('\n')+ending,[row['fields']])
    run('',[])
    run(''.join(row['line'] for row in original['cases']),[row['fields'] for row in original['cases']])
    base=original['cases'][0];fields=base['line'].rstrip('\n').split(':',1)[1].split(',')
    for mac in ('02:00:00:00:00:01','02000000000a','02:00:00:00:00:0a'):
        row=fields.copy();row[3]=mac
        run('row:'+','.join(row)+'\n',[dict(base['fields'],BSSID=mac)])
    # UTF8 SSIDs are byte-bounded. Spaces are preserved, never split by a
    # whitespace scanner. Empty/embedded-comma SSIDs have no verified encoding.
    for ssid in ('x'*32,'合成网络','quote"back\\slash'):
        row=fields.copy();row[2]=ssid
        run('row:'+','.join(row)+'\n',[dict(base['fields'],SSIDName=ssid)])
    bad=['\n','row:partial\n',':'+','.join(fields),'row:'+','.join(fields)+',extra',base['line']+'partial',
         'x'*256+':'+','.join(fields),base['line'].replace('Synthetic AP','a\0b')]
    for index in range(8):
        row=fields.copy();row[index]='';bad.append('row:'+','.join(row))
        row=fields.copy();row[index]='a\tb';bad.append('row:'+','.join(row))
    for index,value in ((0,'a'),(5,'36MHz'),(6,'--55'),(6,'5-5'),(6,'+55'),(6,'55.5'),(6,'12345'),
                        (3,'030000000001'),(3,'000000000000'),(3,'zz0000000001'),(3,'02:00:00:00:00'),
                        (1,'2.4GHz'),(2,'密'*11),(2,'x'*33),(4,'x'*16),(7,'x'*6)):
        row=fields.copy();row[index]=value;bad.append('row:'+','.join(row))
    for text in bad:run(text,None,-errno.EBADMSG)
a.output.write_text(json.dumps(dict(cases=cases,scope='Native field parser compared with executed target JSON conversion; malformed/ambiguous rows refused, scan completeness not claimed.'),indent=2)+'\n')
print(f'Neighbor fields: {len(cases)} target/native, newline, UTF8, bound and malformed-row comparisons PASS')
