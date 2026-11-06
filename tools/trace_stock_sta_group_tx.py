#!/usr/bin/env python3
"""Execute target STA group address production and the wireless header builder."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--wifi',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
class PeerLookup(Exception):pass
packet=b''
offset=0
stage='attributes'
lookup=[]
def boundary(name,args):
    global offset
    if name=='_rtw_memset':
        oracle.uc.mem_write(args[0],bytes([args[1]&255])*args[2]);return args[0]
    if name=='_rtw_memcpy':
        oracle.uc.mem_write(args[0],bytes(oracle.uc.mem_read(args[1],args[2])));return args[0]
    if name=='_rtw_memcmp':
        return int(bytes(oracle.uc.mem_read(args[0],args[2]))==bytes(oracle.uc.mem_read(args[1],args[2])))
    if name=='_rtw_open_pktfile':
        offset=0;return 0
    if name=='_rtw_pktfile_read':
        data=packet[offset:offset+args[2]];assert len(data)==args[2]
        if args[1]:oracle.uc.mem_write(args[1],data)
        offset+=args[2];return len(data)
    if name=='rtw_get_stainfo':
        lookup.append(bytes(oracle.uc.mem_read(args[1],6)).hex())
        if stage=='attributes':raise PeerLookup
        return station
    if name=='rtw_get_sw_seq':return 0x123
    raise AssertionError(('unmodeled wireless boundary',name,args))
oracle=ModuleOracle(a.wifi,['update_attrib','rtw_make_wlanhdr'],boundary)
adapter,station=0x6000000,0x6100000
oracle.uc.mem_map(adapter,0x30000);oracle.uc.mem_map(station,0x10000)
skb,attrib,header=oracle.scratch,oracle.scratch+0x1000,oracle.scratch+0x2000
local=bytes.fromhex('020000001111');ap=bytes.fromhex('020000002222')
oracle.uc.mem_write(adapter+15,local);oracle.uc.mem_write(adapter+0x174,ap)
oracle.uc.mem_write(adapter+204,struct.pack('<I',8))
oracle.uc.mem_write(station+1024,struct.pack('<I',1))
cases=[]
for da in (bytes.fromhex('01005e010203'),bytes.fromhex('333300010203'),bytes.fromhex('020000003333')):
    for sa in (local,bytes.fromhex('020000004444')):
        packet=da+sa+b'\x08\x00'+bytes(64)
        oracle.uc.mem_write(attrib,bytes(512));oracle.uc.mem_write(skb,bytes(512))
        stage='attributes';lookup.clear()
        try:oracle.call('update_attrib',adapter,skb,attrib)
        except PeerLookup:pass
        else:raise AssertionError('STA did not select a unicast receiver')
        assert lookup==[ap.hex()]
        original=bytes(oracle.uc.mem_read(attrib+40,24))
        assert original[:6]==sa and original[6:12]==da and original[12:18]==local and original[18:24]==ap
        for qos in (False,True):
            stage='header'
            oracle.uc.mem_write(attrib+76,struct.pack('<H',26 if qos else 24))
            oracle.uc.mem_write(attrib+79,bytes([0x88 if qos else 8,int(qos)]))
            oracle.uc.mem_write(adapter+1576,struct.pack('<I',int(qos)))
            oracle.uc.mem_write(attrib+89,bytes([5 if qos else 0]))
            oracle.uc.mem_write(attrib+91,b'\x04')
            oracle.word(attrib+224,station)
            assert oracle.call('rtw_make_wlanhdr',adapter,header,attrib)==1
            raw=bytes(oracle.uc.mem_read(header,26 if qos else 24))
            assert raw[4:10]==ap and raw[10:16]==local and raw[16:22]==da
            assert struct.unpack_from('<H',raw)[0]==(0x4188 if qos else 0x4108)
            assert raw[22:24]==b'\x30\x12'
            if qos:assert raw[24:]==b'\x05\0'
            cases.append(dict(ethernet_da=da.hex(),ethernet_sa=sa.hex(),qos=qos,receiver=ap.hex(),header=raw.hex()))
a.output.write_text(json.dumps(dict(cases=cases,sha256=hashlib.sha256(a.wifi.read_bytes()).hexdigest(),
    findings=['Original STA producer selects the associated AP for multicast and unicast Ethernet destinations.',
              'Original three-address TX writes the local adapter TA; Linux instead preserves Ethernet SA in addr2. Native PE follows the current Linux packet decision, retaining this documented difference.'],
    scope='Actual target update_attrib prefix until station lookup, then full rtw_make_wlanhdr. Packetfile/memory helpers, live association fields and sequence allocation are modeled; no RF, crypto or post-lookup attribute execution.'),indent=2)+'\n')
print(f'Original STA group TX: {len(cases)} header cases, associated-AP RA and local-TA difference confirmed')
