#!/usr/bin/env python3
"""Execute linked PE multicast-cookie admission, queueing and stale publication."""
from pathlib import Path
import struct
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'analysis/python-deps'))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN,UC_HOOK_MEM_READ
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_V0
from test_pe_downlink_gather import case

directory=Path(sys.argv[1]);binary=(directory/'pe-tx.bin').read_bytes()
symbols={s[2]:int(s[0],16) for line in subprocess.check_output(['llvm-nm-18',str(directory/'pe-wd.elf')],text=True).splitlines() if len(s:=line.split())==3}
base,mb,ni,bar,epp,payload,table,requests=0x04000000,0x04002000,0x10000000,0x12000000,0x06000000,0x07000000,0x08000000,0x09000000
peer=bytes.fromhex('020000000002');group=bytes.fromhex('01005e010203')
class Machine:
    def __init__(self,tid=0,mode=0,ipv6=False,full=True,client=False,dscp=46):
        self.u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
        for address,size in ((0x1fc00000,0x10000),(base,0x20000),(ni,0x10000),(bar,0x10000),(epp,0x10000),(payload,0x4000),(table,0x6000),(requests,0x2000)):
            self.u.mem_map(address,size)
        self.u.mem_write(0x1fc00000,binary);self.header=16 if full else 8
        self.frame=bytearray(group+bytes.fromhex('020000000001')+(b'\x86\xdd' if ipv6 else b'\x08\x00')+bytes(70))
        self.frame[14]=0x60 if ipv6 else 0x45
        if ipv6:self.frame[14]|=dscp>>2;self.frame[15]=(dscp&3)<<6
        else:self.frame[15]=dscp<<2
        self.u.mem_write(payload+64+self.header,bytes(self.frame))
        self.u.mem_write(payload+64,struct.pack('>II',(1<<31 if full else 0)|(265<<18),len(self.frame)+(self.header-8)<<15|19<<3|tid))
        self.u.mem_write(epp+tid*4096,struct.pack('<II',payload|0x50,0))
        self.put(ni+0x659c,1<<(24+tid));self.put(ni+0x7060+tid*4,8)
        self.put(mb+114*4,0x7060);self.put(mb+115*4,0x7160);self.put(mb+167*4,12);self.put(mb+25*4,epp)
        self.put(mb+2*4,bar|0xa0000000);self.put(mb+210*4,table);self.put(table,7)
        self.u.mem_write(base+0xe408,peer);self.put(base+0x11a04,1)
        self.put(base+0xd020+16,1<<24|1<<18|1<<16|(1<<23 if client else 0));self.put(base+0xd020+8,26<<16)
        self.u.mem_write(table+16+9*80,struct.pack('<II',7,1|3<<8|mode<<11)+peer+bytes(2)+bytes([i%8 for i in range(64)]))
        self.tid=tid
    def put(self,address,value):self.u.mem_write(address,struct.pack('<I',value))
    def get(self,address):return struct.unpack('<I',self.u.mem_read(address,4))[0]
    def call(self,name,*args):
        for i,value in enumerate(args):self.u.reg_write(UC_MIPS_REG_A0+i,value|0xa0000000 if value>=0x04000000 else value)
        self.u.reg_write(UC_MIPS_REG_SP,(base+0x1fff0)|0xa0000000);self.u.reg_write(UC_MIPS_REG_RA,0x9fc0fff0)
        self.u.emu_start(symbols[name],0x9fc0fff0,count=100000)
        return self.u.reg_read(UC_MIPS_REG_V0)
    def ingest(self):
        self.call('ni_ingest',mb,base)
        # The production callers ignore the return; optimized code may not
        # preserve it. Published EPP credit and queue ownership are observable.
        assert self.get(ni+0x7160+self.tid*4)==8 and self.get(mb+38*4)==1

vectors=0
for mode in range(3):
    for ipv6 in (False,True):
        for full in (False,True):
            for tid in range(8):
                m=Machine(tid,mode,ipv6,full);m.ingest();chosen=3 if mode==0 else tid if mode==1 else 6
                assert m.get(base+0x15000+(8+chosen)*4)==payload
                assert bytes(m.u.mem_read(payload+64+m.header,6))==peer
                assert m.get(payload+16)==265 and m.get(ni+0x7160+tid*4)==8
                vectors+=1
# Every DSCP entry selects its compiled Linux policy, independently of ECN.
for ipv6 in (False,True):
    for dscp in range(64):
        m=Machine(mode=2,ipv6=ipv6,dscp=dscp)
        offset=15
        m.u.mem_write(payload+64+m.header+offset,bytes([m.frame[offset]|(3<<4 if ipv6 else 3)]))
        m.ingest()
        assert m.get(base+0x15000+(8+dscp%8)*4)==payload
        vectors+=1
for fault in range(15):
    m=Machine(client=True)
    if fault==0:m.put(table,0)
    if fault==1:m.put(table+16+9*80,6)
    if fault==2:m.put(base+0x11a04,0)
    if fault==3:m.put(base+0xd020+16,1<<23)
    if fault==4:m.put(table+16+9*80+4,1|7<<16)
    if fault==5:m.put(table+16+9*80+4,1|3<<11)
    if fault==6:m.u.mem_write(table+16+9*80+8,b'\x01')
    if fault==7:m.put(mb+210*4,0)
    if fault==8:m.u.mem_write(payload+64,struct.pack('>I',256<<18))
    if fault==9:
        address=table+16+9*80+4
        m.u.hook_add(UC_HOOK_MEM_READ,lambda u,*a:u.mem_write(table,bytes(4)),begin=address,end=address+3)
    if fault==10:m.put(table+16+9*80+4,1|1<<7)
    if fault==11:m.u.mem_write(table+16+9*80+8,bytes(6))
    if fault==12:m.u.mem_write(table+16+9*80+14,b'\x01')
    if fault==13:
        m.put(table+16+9*80+4,1|2<<11)
        m.u.mem_write(table+16+9*80+16,bytes([8])*64)
    if fault==14:
        m.u.mem_write(payload+68,struct.pack('>I',(len(m.frame)+8)<<15|18<<3))
    m.ingest();assert m.get(base+0x15000)==payload and not m.get(base+0x11804)
    assert bytes(m.u.mem_read(payload+64+m.header,6))==group
    vectors+=1
for mode in (1,2):
    m=Machine(tid=5,mode=mode)
    m.u.mem_write(table+16+9*80+16,bytes([2])*64)
    m.ingest();assert m.get(base+0x15000+10*4)==payload;vectors+=1
# Never overwrite a retained discard WP when an old generation is closed.
m=Machine();m.ingest();m.put(table,0);m.put(mb+47*4,8)
assert not m.call('ni_select',mb,base,requests)
assert m.get(mb+47*4)==8 and m.get(mb+38*4)==1 and not m.get(base+0xc400)
m.put(mb+47*4,0);m.call('ni_select',mb,base,requests)
assert m.get(mb+47*4)==1 and not m.get(mb+38*4);vectors+=1
# A closed generation must discard queued sleepers and already selected WPs.
for pending in (False,True,2):
    m=Machine();m.ingest();m.put(bar+0xc438,4000)
    if pending:
        assert m.call('ni_select',mb,base,requests)==1
        m.put(mb+193*4,1);m.put(mb+194*4,0)
    if pending==2:m.put(base+0x11a04,0)
    else:m.put(table,0)
    if pending:
        m.call('pe_tx_publish',mb,8,requests)
        assert m.get(mb+194*4)==1 and not m.get(mb+10*4)
    else:
        m.put(base+0xe810,1) # No PS grant; stale copies still retire.
        m.call('ni_select',mb,base,requests)
        assert not m.get(mb+38*4)
    assert (m.get(mb+45*4) if pending else m.get(mb+47*4)) and m.get(base+0xc404)&1<<31
    # Return exactly this dropped BID after the hardware push reg gains credit.
    m.put(mb+116*4,0x6380);m.put(mb+117*4,6)
    m.call('ni_recycle',mb,base);assert m.get(base+0xc400)==payload
    m.put(ni+0x6340,1);m.put(ni+0x6380,1<<31);m.call('ni_recycle',mb,base)
    assert not m.get(mb+47*4) and not m.get(base+0xc400) and m.get(ni+0x6384)==payload|6
    vectors+=1
# Same peer with different owners must not enter one A-MSDU. Otherwise a
# selected aggregate could retain a revoked second owner's packet.
m=Machine(mode=1);m.put(base+0xe204,7935<<18)
second=payload+2048;m.u.mem_write(second+64+m.header,bytes(m.frame))
m.u.mem_write(second+64,struct.pack('>II',1<<31|266<<18,(len(m.frame)+8)<<15|19<<3))
m.u.mem_write(table+16+10*80,bytes(m.u.mem_read(table+16+9*80,80)))
m.u.mem_write(epp+8,struct.pack('<II',second|0x50,0));m.put(ni+0x7060,16)
m.call('ni_ingest',mb,base);m.put(bar+0xc438,4000)
assert m.call('ni_select',mb,base,requests)==1 and m.get(base+0x15020)==second
assert m.get(requests+8)>>24==1;vectors+=1
# Two already selected owners must drain during pause without EQ credit.
m=Machine(mode=1);second=payload+2048
m.u.mem_write(second+64+m.header,bytes(m.frame));m.u.mem_write(second+64,bytes(m.u.mem_read(payload+64,16)))
m.u.mem_write(epp+8,struct.pack('<II',second|0x50,0));m.put(ni+0x7060,16)
m.call('ni_ingest',mb,base);m.put(bar+0xc438,4000)
assert m.call('ni_select',mb,base,requests)==1
assert m.call('ni_select',mb,base,requests+128)==1
m.put(mb+193*4,2);m.put(table,0);m.call('pe_tx_publish',mb,16,requests)
assert m.get(mb+194*4)==2 and m.get(mb+45*4)==2 and m.get(mb+30*4)==2
assert not m.get(mb+47*4) and not m.get(mb+10*4);vectors+=1
# Real main loop builds encrypted descriptors and holds every BID until RPQ.
for client in (False,True):
    for lengths in ([60],[1514]*3):
        case(lengths,7935,from_epp=True,client=client,multicast=True)
print(f'PE multicast: {vectors} admission/revocation cases and4 full encrypted TX/RPQ cases passed')
