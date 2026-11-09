#!/usr/bin/env python3
"""Run native cursor rebase followed by linked PE work/tail recycling."""
import ctypes as C
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
fixture=root/'scripts/test_pe_rxq_backpressure.py'
ns={'__file__':str(fixture)}
exec(compile(fixture.read_text().split('m=Machine();retained=[]',1)[0],str(fixture),'exec'),ns)
Machine,B,MB,TAIL,IDLE=(ns[n] for n in ('Machine','B','MB','TAIL','IDLE'))
driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=(driver/'ni-test.h').read_text()
if len(sys.argv)>2:
    source=subprocess.check_output(['git','show',sys.argv[2]+':'+str((driver/'ni-test.h').relative_to(root))],cwd=root,text=True)
begin=source.index('static void uplink_counter_start(')
end=source.index('\nstatic int ni_uplink_start(',begin)
code=r'''
#include <stdint.h>
#include <string.h>
#include "rx-geometry.h"
typedef uint32_t u32;typedef unsigned char u8;
#define BIT(n) (1U<<(n))
#define PE_PROBE_MAILBOX 0x2000
struct rtl_ax_tx {void *rx;};
static void *rtl_ax_rx_pe_memory(void *r){return r;}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
'''+source[begin:end]+r'''
void rebase(void *memory,u32 start){struct rtl_ax_tx tx={.rx=memory};uplink_counter_start(&tx,start);}
'''
with tempfile.TemporaryDirectory(prefix='ni-tail-restart-') as directory:
    d=Path(directory);(d/'test.c').write_text(code)
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC','-I',str(driver),str(d/'test.c'),'-o',str(d/'test.so')],check=True)
    lib=C.CDLL(str(d/'test.so'));lib.rebase.argtypes=[C.c_void_p,C.c_uint32]
    count=0
    for old in (0,1,1023,0x3fffffff,0x7fffffff):
        for start in (0,1,511,1023):
            for tails in (1,2):
                m=Machine();held=[]
                # These two completed buffers are held outside RXQ and the
                # work list, exactly as the real stop/handoff leaves them.
                idle=m.get(MB+177*4)
                m.put(MB+177*4,idle-tails)
                for i in range(tails):
                    identity=m.get(B+IDLE+(idle-tails+i)*4)
                    distance=i if tails==2 else 0
                    m.put(B+TAIL+i*8,identity+1)
                    m.put(B+TAIL+i*8+4,(old-distance)&0x7fffffff)
                    m.put(m.payload_dma(identity),0x12345678+i)
                    held.append(identity)
                m.put(MB+364,old);m.put(MB+360,old);m.inventory()
                memory=C.create_string_buffer(bytes(m.u.mem_read(B,0x20000)),0x20000)
                lib.rebase(memory,start)
                m.u.mem_write(B,memory.raw)
                assert m.get(MB+360)==m.get(MB+364)==m.get(MB+472)==start
                before=bytes(m.u.mem_read(B+TAIL,16))
                m.execute('rx_completion_poll');m.inventory()
                assert bytes(m.u.mem_read(B+TAIL,16))==before
                for i,identity in enumerate(held):assert m.get(m.payload_dma(identity))==0x12345678+i
                m.put(MB+364,(start+1)&0x7fffffff);m.execute('rx_completion_poll');m.inventory()
                assert m.get(B+TAIL)==held[0]+1
                if tails==2:assert not m.get(B+TAIL+8) and not m.get(m.payload_dma(held[1]))
                m.put(MB+364,(start+2)&0x7fffffff);m.execute('rx_completion_poll');m.inventory()
                assert not m.get(B+TAIL) and not m.get(m.payload_dma(held[0]))
                assert m.get(MB+177*4)==idle
                m.execute('rx_completion_poll');m.inventory();assert m.get(MB+177*4)==idle
                count+=1
    work_cases=0
    IDS,STATUS=(ns[n] for n in ('IDS','STATUS'))
    for old in (0,1,1023,0x3fffffff,0x7fffffff,0x80000000,0xffffffff):
        for start in (0,1,511,1023):
            for read in (0,253,255):
                m=Machine();idle=m.get(MB+177*4)
                slots=[(read+i)&255 for i in range(5)]
                identities=[m.get(B+IDLE+(idle-5+i)*4) for i in range(5)]
                owners=[0x80000000|((old-2)&0x7fffffff),0,
                        0x80000000|((old-1)&0x7fffffff),
                        0x80000000|(old&0x7fffffff),0x40000000]
                m.put(MB+177*4,idle-5)
                for i,(slot,identity,owner) in enumerate(zip(slots,identities,owners)):
                    m.put(B+IDS+slot*4,identity|(0x80000000 if i==2 else 0))
                    m.put(B+STATUS+slot*4,owner)
                    m.put(m.payload_dma(identity),0x12345678+i)
                m.put(MB+176*4,read);m.put(MB+175*4,slots[2]);m.put(MB+174*4,(read+5)&255)
                # ARM owns slot1; lookahead has already submitted slots2/3.
                m.put(B+0xf400,slots[1]);m.put(MB+94*4,1);m.put(MB+180*4,1)
                m.put(MB+364,old);m.put(MB+360,old);m.inventory()
                # Non-NI hints and stale storage outside READ..WRITE are not epochs.
                outside=(read+6)&255;m.put(B+STATUS+outside*4,0x80001234)
                before=bytes(m.u.mem_read(B,0x20000));expected=bytearray(before)
                for i,distance in ((0,2),(2,1),(3,0)):
                    struct.pack_into('<I',expected,STATUS+slots[i]*4,0x80000000|((start-distance)&0x7fffffff))
                for offset in (360,364,472):struct.pack_into('<I',expected,MB-B+offset,start)
                memory=C.create_string_buffer(before,0x20000);lib.rebase(memory,start)
                rebased=memory.raw
                m.u.mem_write(B,memory.raw)
                m.execute('rx_completion_poll');m.process();m.inventory()
                assert m.get(MB+176*4)==slots[1] and m.get(MB+175*4)==slots[2]
                assert not m.get(m.payload_dma(identities[0]))
                for i in range(1,5):assert m.get(m.payload_dma(identities[i]))==0x12345678+i
                m.arm_ack()
                for _ in range(3):m.process()
                m.execute('rx_completion_poll');m.inventory()
                assert m.get(MB+176*4)==m.get(MB+174*4)
                assert m.get(MB+177*4)==idle-2,('early work-buffer release',hex(old),start,read)
                assert m.get(B+TAIL)==identities[2]+1 and m.get(B+TAIL+8)==identities[3]+1
                m.put(MB+364,(start+1)&0x7fffffff);m.execute('rx_completion_poll');m.inventory()
                assert not m.get(m.payload_dma(identities[2])) and m.get(m.payload_dma(identities[3]))==0x1234567b
                m.put(MB+364,(start+2)&0x7fffffff);m.execute('rx_completion_poll');m.inventory()
                assert m.get(MB+177*4)==idle and not m.get(m.payload_dma(identities[3]))
                m.execute('rx_completion_poll');m.inventory();assert m.get(MB+177*4)==idle
                assert rebased==bytes(expected),('work owner rebase',hex(old),start,read)
                work_cases+=1
print(f'Native cursor restart -> linked PE: {count} retained-tail and {work_cases} work-ring/ARM barrier/lookahead/epoch-wrap cases;514-buffer inventory and exact delayed return PASS')
