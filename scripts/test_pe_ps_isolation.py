#!/usr/bin/env python3
"""Run linked PE: 500 sleeping frames cannot prevent another peer from transmitting."""
from pathlib import Path
import sys,struct,subprocess,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"analysis/python-deps"))
from unicorn import UC_HOOK_MEM_WRITE, Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_A1,UC_MIPS_REG_A2,UC_MIPS_REG_SP,UC_MIPS_REG_RA,UC_MIPS_REG_V0
p=Path(sys.argv[1])
symbols={x[2]:int(x[0],16) for l in subprocess.check_output(["llvm-nm-18",str(p/"pe-wd.elf")],text=True).splitlines() if len(x:=l.split())==3}
u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
BASE,MB,NI,PAYLOAD,EPP,REQ=0x04000000,0x04002000,0x10000000,0x06000000,0x05000000,0x04007000
for a,n in [(0x1fc00000,0x4000),(BASE,0x20000),(NI,0x10000),(PAYLOAD,0x200000),(0x12000000,0x10000),(EPP,0x40000),(0x10020000,0x1000)]:u.mem_map(a,n)
u.mem_write(0x1fc00000,(p/"pe-tx.bin").read_bytes())
def put(a,v):u.mem_write(a,struct.pack("<I",v))
def get(a):return struct.unpack("<I",u.mem_read(a,4))[0]
for i,v in [(25,EPP),(114,0x7060),(115,0x7160),(167,15)]:put(MB+i*4,v)
put(NI+0x659c,1<<24)
# RX handoff and downlink scheduling use the same per-radio PE memory.
rx_map = struct.pack('<256I', *range(256))
u.mem_write(BASE+0x17400, rx_map)
put(BASE+0x17800,257);put(BASE+0x17804,258)
rx_state = bytes(u.mem_read(BASE+0x17400,0x420))
mac=[bytes.fromhex("020000000001"),bytes.fromhex("020000000002")]
for peer in [1,2]:
 put(BASE+0x11a00+peer*4,1);put(BASE+0xe200+peer*4,3839<<18|1<<17)
 u.mem_write(BASE+0xe400+peer*8,mac[peer-1])
 u.mem_write(BASE+0xd000+peer*32,struct.pack("<8I",0,0,26<<16,0x20100,peer<<24|1<<18|1<<16,3<<16|64,0,0))
 put(BASE+0x12000+peer*8*4,64)
put(BASE+0xe800+16,1)
for n in range(501):
 frame=mac[0 if n<500 else 1]+bytes.fromhex("0200000000030800")+bytes(50)
 at=PAYLOAD+n*2048
 u.mem_write(at+32,struct.pack(">II",0x80000000,(len(frame)+8)<<15)+bytes(8)+frame)
 u.mem_write(EPP+n*8,struct.pack("<II",at|0x50,len(frame)+16))
put(NI+0x7060,501*8)
def call(name):
 u.reg_write(UC_MIPS_REG_A0,MB|0xa0000000);u.reg_write(UC_MIPS_REG_A1,BASE|0xa0000000);u.reg_write(UC_MIPS_REG_A2,REQ|0xa0000000)
 u.reg_write(UC_MIPS_REG_SP,(BASE+0x1fff0)|0xa0000000);u.reg_write(UC_MIPS_REG_RA,0x9fc01ff0)
 u.emu_start(symbols[name],0x9fc01ff0,count=100000)
 return u.reg_read(UC_MIPS_REG_V0)
for n in range(501):call("ni_ingest")
assert bytes(u.mem_read(BASE+0x17400,0x420)) == rx_state, 'downlink ingestion overwrote RX buffer ownership'
# Return an RXBD whose old payload is still in the NI tail: install a spare.
RX, RXBD = 0x08000000, 0x09000000
u.mem_map(RX,258*12288);u.mem_map(RXBD,4096)
put(RX+12288,0x1234c040)
for i,v in [(2,0xb2000000),(80,RX),(81,2),(83,1),(91,2),(134,RXBD)]:put(MB+i*4,v)
put(BASE+0xf004,(1<<31)|2);put(0x12001218,2<<16|1)
call('pe_rxq')
assert get(BASE+0x17404)==256 and get(RXBD+12)==RX+256*12288
assert get(BASE+0x17810)==2 and get(RX+12288)==0x1234c040
rx_state = bytes(u.mem_read(BASE+0x17400,0x420))
selected=call("ni_select")
r={"scope":"Linked PE instructions, synthetic two-peer traffic; no device measurement","sleeping_peer_packets":500,"awake_peer_next_packets":1,"EPP_consumed_packets":get(NI+0x7160)//8,"awake_peer_selected_frames":selected,"owner_slots_held":get(MB+30*4),"sleeping_peer_queued":get(BASE+0x11804),"awake_peer_queued":get(BASE+0x11808),"counterexample":selected==0 and get(NI+0x7160)==124*8}
assert selected == 1 and get(NI+0x7160)==501*8 and get(MB+30*4)==1, r
assert get(BASE+0x18000+8*4)==500
assert get(BASE+0x18000+16*4)==1
# One PS grant releases one frame; it does not wake the remaining queue.
put(BASE+0xe800+16+4,1);put(BASE+0xe800+16+12,1<<16)
assert call("ni_select")==1
assert get(BASE+0xe800+16+8)==1
assert get(REQ+4*4)&(1<<19) and not get(REQ+4*4)&(1<<20)
assert call("ni_select")==0 and get(BASE+0x11804)==499
# Retire the sleeping peer; only its queued BIDs may return to EQ.
put(BASE+0x11a04,0)
for i,v in [(0,0x50455744),(2,0xb2000000),(3,1),(24,1),(116,0x6380),(117,6),(140,MB|0xa0000000)]:put(MB+i*4,v)
put(NI+0x6340,0x8000007c);put(NI+0x6380,0x80000000)
returned=[]
u.hook_add(UC_HOOK_MEM_WRITE,lambda uc,access,at,size,value,data:returned.append(value),begin=NI+0x6384,end=NI+0x6387)
u.reg_write(UC_MIPS_REG_A0,MB|0xa0000000)
u.reg_write(UC_MIPS_REG_SP,(BASE+0x1fff0)|0xa0000000)
u.emu_start(0xbfc00400,0xbfc03ffc,count=2000000)
assert len(returned)==499 and len(set(returned))==499
assert set(returned)=={PAYLOAD+n*2048|6 for n in range(1,500)}
assert get(MB+30*4)==2 and get(BASE+0x18000+8*4)==1
assert get(BASE+0x18000+16*4)==1 and get(BASE+0x11804)==0
assert bytes(u.mem_read(BASE+0x17400,0x420)) == rx_state, 'downlink retirement overwrote RX buffer ownership'
put(MB+91*4,4);call('pe_rxq')
assert get(BASE+0x17810)==0 and get(BASE+0x17800)==2 and get(RX+12288)==0
r.update(retired_waiting_BIDs_returned_exactly_once=True,inflight_BIDs_retained=True,pending_TID_counter_above255=True,PS_grant_releases_one_frame=True)
r.update(RX_map_preserved=True,RX_spare_handoff_preserves_waiting_queues=True)
r.pop("counterexample")
print(json.dumps(r,indent=2))
