#!/usr/bin/env python3
"""Execute host header construction with a DMA address in a PE ATU IO window."""
from pathlib import Path
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_INVALID
from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_A0, UC_MIPS_REG_SP

code = Path(sys.argv[1]).read_bytes()
words = list(struct.unpack('<%dI' % (len(code) // 4), code))
for i, word in enumerate(words):
    if word in (0x4c880000, 0x4c880800, 0x4c882000, 0x4c882800, 0x4cc80000, 0x4cc81000): words[i] = 0
    if word == 0x4c084000: words[i] = 0x24080000
base, mailbox, ring, pages, headers = 0x04000000, 0x04002000, 0x05000000, 0x05100000, 0x05200000
words[0] |= (mailbox | 0xa0000000) >> 16
words[1] |= mailbox & 65535
words[2] |= (base | 0x80000000) >> 16
words[0x380 // 4] |= (mailbox | 0xa0000000) >> 16
words[0x384 // 4] |= mailbox & 65535

destination = bytes.fromhex('b46e108327cb')
source = bytes.fromhex('020000000001')
ap = bytes.fromhex('020000000012')
ethernet = destination + source + b'\x08\x00'
host_dma = 0x120380be  # Observed bad region; PE ATU redirects this window to IO.

for encap, payload_bytes in ((encap, size) for encap in (True, False)
                            for size in (0, 1, 1500, 2048)):
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for at, size in ((0x1fc00000,0x10000),(base,0x20000),(ring,0x10000),
                     (pages,0x10000),(headers,0x10000),(0x11000000,0x100000),
                     (0x12000000,0x100000),(0x10020000,0x1000)):
        u.mem_map(at,size)
    u.mem_write(0x1fc00000,struct.pack('<%dI'%len(words),*words))
    def invalid(uc, access, address, size, value, unused):
        raise AssertionError(('unmodeled IO',hex(address),hex(uc.reg_read(UC_MIPS_REG_PC))))
    u.hook_add(UC_HOOK_MEM_INVALID,invalid)
    # The PCIe DMA consumer sees host RAM, but the PE sees its IO mapping.
    # A host DMA cookie must therefore remain opaque to the PE CPU.
    u.mem_write(host_dma,b'\xa5'*64)
    def put(at,value):u.mem_write(at,struct.pack('<I',value))
    def get(at):return struct.unpack('<I',u.mem_read(at,4))[0]
    put(mailbox+8,0xb1000000)
    put(mailbox+44,ring);put(mailbox+48,pages);put(mailbox+56,headers)
    u.mem_write(mailbox+64,ap+b'\0\0')
    put(mailbox+140*4,mailbox|0xa0000000)
    put(mailbox+142*4,1)
    put(mailbox+12,1)
    # The boot/CP3 path has its own test; start the actual C entry here.
    u.reg_write(UC_MIPS_REG_A0, mailbox | 0xa0000000)
    u.reg_write(UC_MIPS_REG_SP, (base + 0x1fff0) | 0xa0000000)
    pc=0xbfc00400
    def run():
        global pc
        u.emu_start(pc,0xbfc10000,count=12000)
        pc=u.reg_read(UC_MIPS_REG_PC)
    run()
    assert get(mailbox)==0x50455744
    raw=bytes.fromhex('88020000')+destination+ap+source+b'\x30\x12\0\0'
    expected=raw[:22]+b'\0\0'+raw[24:]
    if encap:expected+=b'\xaa\xaa\x03\0\0\0\x08\0'
    u.mem_write(headers+7*128,ethernet if encap else raw)
    request=[0]*32
    request[0]=7;request[1]=1<<16
    request[2]=(len(expected)+payload_bytes)|26<<16|1<<24
    request[3]=0x00330180
    request[4]=(2<<24)|(1<<18 if encap else 1<<22)
    request[5]=64
    request[8]=host_dma;request[9]=(14 if encap else 26)+payload_bytes
    u.mem_write(base+0x4000,struct.pack('<32I',*request))
    put(mailbox+32,1)
    run()
    assert get(mailbox+36)==1
    actual=bytes(u.mem_read(headers+7*128,len(expected)))
    assert actual==expected,('Ethernet' if encap else 'raw QoS',actual.hex(),expected.hex())
    wp=bytes(u.mem_read(pages+7*128,128))
    count=struct.unpack_from('<I',wp,4)[0]>>26
    segments=[struct.unpack_from('<HI',wp,64+i*6) for i in range(count)]
    lengths=[length & 2047 for length, address in segments]
    assert lengths==[len(expected)]+([min(payload_bytes,2047)] if payload_bytes else [])+([1] if payload_bytes==2048 else [])
    assert [length>>15 for length,address in segments]==[0]*(count-1)+[1], (encap,payload_bytes,segments)
    assert segments[0][1]==headers+7*128
    offset=14 if encap else 26
    for length,address in segments[1:]:
        assert address==host_dma+offset
        offset+=length & 2047
    assert struct.unpack_from('<I',wp,8)[0]&0x3fff==sum(lengths)
    assert get(ring)&0xffff==64+count*6
print('PE host headers: staged headers, opaque payload DMA, and exactly one final LS for empty, one-byte, normal and split payloads')
