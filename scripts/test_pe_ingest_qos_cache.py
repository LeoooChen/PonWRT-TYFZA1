#!/usr/bin/env python3
"""Compare linked PE admission across QoS peers, short frames and withdrawal."""
from pathlib import Path
import sys
import struct
import json
root = Path(__file__).resolve().parents[1]
source = (root / 'scripts/test_pe_ethernet_prefix.py').read_text().split('a,b=[Machine')[0]
ns = {'__file__': str(root / 'scripts/test_pe_ethernet_prefix.py')}
exec(compile(source, 'existing-ingest-fixture', 'exec'), ns)
from unicorn import UC_HOOK_MEM_READ
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC
B, MB, NI, BAR, EPP, BUF = [ns[x] for x in ('B','MB','NI','BAR','EPP','BUF')]

def case(path, mode, header, withdraw):
    m = ns['Machine'](path)
    u, put = m.u, m.put
    u.mem_map(BUF + 4096, 0x20000)
    ns['initialize'](u, B)
    for i, v in [(2, BAR | 0xa0000000), (25, EPP), (114, 0x7060), (115, 0x7160), (167, 12)]:
        put(MB+i*4, v)
    put(NI+0x659c, 1<<24)
    put(NI+0x7060, 64*8)
    for peer in (1,2):
        put(B+0x11a00+peer*4, 1)
        u.mem_write(B+0xe400+peer*8, bytes([2,0,0,0,0,peer]))
        put(B+0xd000+peer*32+8, (24 if mode=='nonqos' or peer==2 else 26)<<16)
    for i in range(64):
        peer = 1 + (i % 2 if mode=='mixed' else 0)
        size = 14 + i % 3 if mode=='short' else 1514
        frame = bytes([2,0,0,0,0,peer])+bytes.fromhex('123456789abc0800')
        frame += (bytes([0x45,i<<2])+bytes(size-16)) if size>=16 else bytes(size-14)
        at = BUF+i*2048
        u.mem_write(at+64+header,frame)
        if header==16:put(EPP+i*8+4,1<<14|len(frame)+16)
        else:u.mem_write(at+64,struct.pack('>II',0,len(frame)<<15))
        put(EPP+i*8,at|0x56)
    reads = {'qos':0,'authorization':0}
    def observe(uc, access, address, size, value, user):
        if address in (B+0xd028,B+0xd048):reads['qos']+=1
        if B+0x11a00<=address<B+0x11c00:reads['authorization']+=1
        if withdraw and address==EPP+32*8:put(B+0x11a04,0)
    u.hook_add(UC_HOOK_MEM_READ,observe)
    for reg, v in [(UC_MIPS_REG_A0,MB),(UC_MIPS_REG_A1,B),(UC_MIPS_REG_SP,B+0x1fff0)]:u.reg_write(reg,v|0xa0000000)
    u.reg_write(UC_MIPS_REG_RA,0x9fc0fff0)
    u.emu_start(m.entry,0x9fc0fff0,count=200000)
    assert u.reg_read(UC_MIPS_REG_PC)==0x9fc0fff0
    assert struct.unpack('<I',u.mem_read(NI+0x7160,4))[0]==512
    # Exclude private stack; include all queues, flow counters, BIDs and payloads.
    image = bytes(u.mem_read(B,0x1c000))+bytes(u.mem_read(B+0x20000,0x60000))+bytes(u.mem_read(BUF,64*2048))
    return image, reads

rows=[]
for mode in ('same','mixed','nonqos','short'):
    for header in (8,16):
        for withdraw in (False,True):
            a,b=[case(p,mode,header,withdraw) for p in sys.argv[1:3]]
            assert a[0]==b[0],(mode,header,withdraw)
            if mode=='same' and not withdraw:
                assert a[1]['qos']==64 and b[1]['qos']==1,(a[1],b[1])
                assert a[1]['authorization']==b[1]['authorization']
            rows.append({'mode':mode,'header':header,'withdraw':withdraw,'old':a[1],'new':b[1]})
print(json.dumps({'packets':len(rows)*64,'state_and_payload_equal':True,'cases':rows},indent=2))
