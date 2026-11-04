#!/usr/bin/env python3
"""Feed actual ARM wake/sleep publication into linked PE grant selection."""
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_READ, UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_V0)

driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'tx.c').read_text()
start = source.index('void rtl_ax_tx_peer_sleep(')
notify = source[start:source.index('\n}\n', start) + 3]
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "rx-geometry.h"
typedef uint32_t u32;
#define WRITE_ONCE(v,x) ((v)=(x))
#define dma_wmb() atomic_thread_fence(memory_order_seq_cst)
#define spin_lock_irqsave(p,f) do{(f)=0;assert(!pthread_mutex_lock(p));}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);assert(!pthread_mutex_unlock(p));}while(0)
struct rtl_ax_tx {bool pe_active,peer_asleep[128];pthread_mutex_t lock;
 unsigned peer_sleep_events,peer_wake_events;struct {void *memory;}pe;
 struct {u32 route_tim[128];}ni;};
static void ni_route_sleep(struct rtl_ax_tx *tx,unsigned macid){(void)tx;(void)macid;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
'''
fixture += notify + r'''
int main(void){
 unsigned char memory[0x20000]={};
 struct rtl_ax_tx tx={.pe_active=true,.lock=PTHREAD_MUTEX_INITIALIZER,.pe={memory}};
 for(unsigned wrap=0;wrap<2;wrap++){
  put_unaligned_le32(1,memory+0xe810);
  put_unaligned_le32(17,memory+0xe814);
  put_unaligned_le32(wrap?0xfffffffc:0,memory+0xe818);
  put_unaligned_le32(1<<16|1,memory+0xe81c);
  put_unaligned_le32(wrap?0xfffffffe:2,memory+RTL_AX_NI_PS_VERSION+4);
  rtl_ax_tx_peer_sleep(&tx,1,false);
  rtl_ax_tx_peer_sleep(&tx,1,true);
  assert(get_unaligned_le32(memory+0xe814)==17);
  assert(fwrite(memory,1,sizeof(memory),stdout)==sizeof(memory));
 }
}
'''
with tempfile.TemporaryDirectory(prefix='pe-ps-wake-') as directory:
    c, exe = Path(directory) / 'notify.c', Path(directory) / 'notify'
    c.write_text(fixture)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-pthread', '-I' + str(driver),
                    str(c), '-o', str(exe)], check=True)
    snapshots = subprocess.check_output([str(exe)])

pe = Path(sys.argv[1])
symbols = {fields[2]: int(fields[0], 16)
           for line in subprocess.check_output(['llvm-nm-18', str(pe / 'pe-wd.elf')], text=True).splitlines()
           if len(fields := line.split()) == 3}
BASE, MB, NI, BAR, EPP, PAYLOAD, REQ = (
    0x04000000, 0x04002000, 0x10000000, 0x12000000,
    0x05000000, 0x06000000, 0x04007000)
for wrap, raced in ((0, False), (1, False), (0, True), (1, True)):
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for address, size in [(0x1fc00000, 0x10000), (BASE, 0x20000), (NI, 0x10000),
                          (BAR, 0x10000), (EPP, 0x1000), (PAYLOAD, 0x1000)]:
        u.mem_map(address, size)
    u.mem_write(0x1fc00000, (pe / 'pe-tx.bin').read_bytes())
    u.mem_write(BASE, snapshots[wrap * 0x20000:(wrap + 1) * 0x20000])

    def put(address, value):
        u.mem_write(address, struct.pack('<I', value & 0xffffffff))

    def get(address):
        return struct.unpack('<I', u.mem_read(address, 4))[0]

    def call(name):
        for reg, value in [(UC_MIPS_REG_A0, MB), (UC_MIPS_REG_A1, BASE),
                           (UC_MIPS_REG_A2, REQ), (UC_MIPS_REG_SP, BASE + 0x1fff0)]:
            u.reg_write(reg, value | 0xa0000000)
        u.reg_write(UC_MIPS_REG_RA, 0x9fc01ff0)
        u.emu_start(symbols[name], 0x9fc01ff0, count=100000)
        return u.reg_read(UC_MIPS_REG_V0)

    for index, value in [(2, BAR | 0xa0000000), (25, EPP), (114, 0x7060),
                         (115, 0x7160), (167, 15)]:
        put(MB + index * 4, value)
    put(NI + 0x659c, 1 << 24)
    mac = bytes.fromhex('020000000001')
    u.mem_write(BASE + 0xe408, mac)
    put(BASE + 0x11a04, 1)
    put(BASE + 0x19804, 10)
    put(BASE + 0xe204, 3839 << 18 | 1 << 17)
    put(BASE + 0x12020, 64)
    u.mem_write(BASE + 0xd020, struct.pack('<8I', 0, 0, 26 << 16, 0x20100,
                1 << 24 | 1 << 18 | 1 << 16, 3 << 16 | 64, 0, 0))
    for index in range(2):
        frame = mac + bytes.fromhex('0200000000090800') + bytes(1486)
        address = PAYLOAD + index * 2048
        u.mem_write(address + 64, bytes(16) + frame)
        u.mem_write(EPP + index * 8, struct.pack('<II', address | 0x50, 1 << 14 | 1516))
    put(NI + 0x7060, 16)
    call('ni_ingest')
    assert get(BASE + 0x11804) == 2
    if raced:
        revoked = bytes(u.mem_read(BASE + 0xe810, 16))
        version = get(BASE + 0x19e04)
        put(BASE + 0xe81c, 1 << 16 | 1)
        put(BASE + 0x19e04, 0xfffffffe if wrap else 2)

        def revoke(uc, access, address, size, value, data):
            # ni_ps_grant has read the old command/flags. Its caller now
            # reads asleep; inject actual ARM wake/sleep before selection.
            u.mem_write(BASE + 0xe810, revoked)
            put(BASE + 0x19e04, version)
            u.hook_del(hook)

        hook = u.hook_add(UC_HOOK_MEM_READ, revoke,
                          begin=BASE + 0xe810, end=BASE + 0xe813)
    assert call('ni_select') == 0, 'old grant released a frame after wake/sleep'
    assert get(BASE + 0x11804) == 2 and get(MB + 30 * 4) == 0
    call('ni_ps_scan')
    assert get(BASE + 0x11804) == 2
    # A new service period grants exactly one queued MPDU; wrap is valid too.
    put(BASE + 0xe814, 18)
    put(BASE + 0xe81c, 1 << 16 | 1)
    put(BASE + 0x19e04, get(BASE + 0x19e04) + 2)
    assert call('ni_select') == 1
    assert get(REQ + 4 * 4) & (1 << 20)
    assert call('ni_select') == 0 and get(BASE + 0x11804) == 1
    assert get(BASE + 0x1b800) == 18
print('Linked PE: wake/sleep revokes queued and snapshotted old grants; fresh grant releases once; version wrap passed.')

# An unchanged command is only polled; no acquire or grant-content read is needed.
B=BASE
counts={'sync':0,'generation':0}
def observe_code(uc,address,size,data):
    if bytes(uc.mem_read(address & 0x1fffffff,4)) == b'\x0f\0\0\0':
        counts['sync'] += 1
def observe_generation(uc,access,address,size,value,data):
    counts['generation'] += 1
code_hook=u.hook_add(UC_HOOK_CODE,observe_code)
data_hook=u.hook_add(UC_HOOK_MEM_READ,observe_generation,begin=B+0xe814,end=B+0xe817)
u.mem_write(B,bytes(0x20000));put(B+0x19e04,8);put(B+0xe818,8)
call('ni_ps_scan')
assert counts=={'sync':0,'generation':0},counts
u.hook_del(code_hook);u.hook_del(data_hook)
# Publication just after the equality read is serviced on a later scan.
u.mem_write(B,bytes(0x20000));put(B+0x19e04,2);put(B+0xe818,2)
def publish(uc,access,at,size,value,data):
    put(B+0x19e04,4);put(B+0xe814,20);put(B+0xe81c,0);u.hook_del(h)
h=u.hook_add(UC_HOOK_MEM_READ,publish,begin=B+0xe818,end=B+0xe81b)
call('ni_ps_scan');put(MB+196*4,0);call('ni_ps_scan');assert get(B+0xe818)==4 and get(B+0x19a08)==20 and get(B+0x19a0c)==1
# The scan still wraps MACID127 to MACID1, including after early return.
u.mem_write(B,bytes(0x20000));put(MB+196*4,126)
call('ni_ps_scan');assert get(MB+196*4)==127
call('ni_ps_scan');assert get(MB+196*4)==1
# A full version rollover is processed when the ACK still holds fffffffe.
u.mem_write(B,bytes(0x20000));put(B+0xe818,0xfffffffe);put(B+0xe814,23)
call('ni_ps_scan');assert get(B+0xe818)==0 and get(B+0x19a08)==23 and get(B+0x19a0c)==1
print('PS scan: unchanged versions skip both syncs; racing and wrapped new grants still cancel once; MACID127 wraps to1')
