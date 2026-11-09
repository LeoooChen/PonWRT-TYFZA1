#!/usr/bin/env python3
"""Run the native cursor rebase followed by linked PE tail-buffer recycling."""
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
begin=source.index('static void uplink_counter_start(')
end=source.index('\nstatic int ni_uplink_start(',begin)
code=r'''
#include <stdint.h>
#include <string.h>
typedef uint32_t u32;typedef unsigned char u8;
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
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',str(d/'test.c'),'-o',str(d/'test.so')],check=True)
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
print(f'Native cursor restart -> linked PE: {count} old/new epoch and one/two retained-tail cases;514-buffer inventory and exact delayed return PASS')
