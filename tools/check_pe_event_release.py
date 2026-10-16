#!/usr/bin/env python3
"""Interleave linked PE instructions with the native ARM event-release function."""
from pathlib import Path
import ctypes,hashlib,json,struct,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'analysis/python-deps'))
from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_LITTLE_ENDIAN,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE,UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_A0,UC_MIPS_REG_SP
binary=(Path(sys.argv[1])/'pe-tx.bin').read_bytes();old='--before' in sys.argv
header=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/pe-event.h'
s=header.read_text();a=s.index('static void pe_event_release(');b=s.index('static int pe_event_prepare(',a)
common=r'''#include <stdint.h>
#include <string.h>
typedef uint32_t u32;
#define PE_PROBE_MAILBOX 0x2000
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
struct rtl_ax_tx {struct {unsigned slot;void *memory;} pe;};
static void *pe_event_timer,*pe_engine;
static unsigned pe_event_irq;
static u32 pe_event_saved[4]={7,0,0,0};
static struct rtl_ax_tx *pe_event_radios[2];
static unsigned freed,unmapped;
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void dma_wmb(void){}
static void synchronize_irq(unsigned irq){(void)irq;}
static void writel(u32 v,void *p){memcpy(p,&v,4);}
static void free_irq(unsigned irq,void *dev){(void)irq;(void)dev;freed++;}
static void iounmap(void *p){(void)p;unmapped++;}
'''
wrapper=r'''void release_native(void *memory,u32 *timer) {
 struct rtl_ax_tx tx={.pe={.slot=0,.memory=memory}};
 pe_event_timer=timer;pe_event_radios[0]=&tx;pe_event_radios[1]=0;
 pe_event_release(&tx);
}
unsigned released(void){return freed==1 && unmapped==1 && !pe_event_timer;}
'''
with tempfile.TemporaryDirectory(prefix='pe-event-release-',dir=ROOT/'artifacts') as tmp:
 tmp=Path(tmp);(tmp/'native.c').write_text(common+s[a:b]+wrapper)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-shared','-fPIC',str(tmp/'native.c'),'-o',str(tmp/'native.so')],check=True)
 lib=ctypes.CDLL(str(tmp/'native.so'));lib.release_native.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
 BASE,CONTROL,RADIO,TIMER=0x4000000,0x4002000,0x4102000,0x100290b0
 u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_LITTLE_ENDIAN)
 for at,size in ((0x1fc00000,0x4000),(BASE,0x20000),(RADIO-0x2000,0x20000),(0x10020000,0x10000)):u.mem_map(at,size)
 u.mem_write(0x1fc00000,binary)
 def put(at,v):u.mem_write(at,struct.pack('<I',v))
 def get(at):return struct.unpack('<I',u.mem_read(at,4))[0]
 put(CONTROL+140*4,RADIO|0xa0000000);put(RADIO,0x50455744);put(TIMER,7)
 for i,v in ((3,2),(64,TIMER|0xa0000000),(67,1)):put(RADIO+i*4,v)
 pause_ack=False;armed=False;reads=0;released=False;io_after=[]
 def do_release():
  global released
  memory=ctypes.create_string_buffer(bytes(u.mem_read(RADIO-0x2000,0x20000)))
  timer=ctypes.create_string_buffer(bytes(u.mem_read(TIMER,36)))
  lib.release_native(memory,timer);assert lib.released()
  u.mem_write(RADIO-0x2000,memory.raw[:0x20000]);u.mem_write(TIMER,timer.raw[:36])
  assert get(RADIO+64*4)==0
  assert [get(TIMER+i) for i in (0,4,12,16)]==[7,0,0,0]
  released=True
 def ack(uc,access,at,size,v,ctx):
  global pause_ack,armed
  if v==2 and not pause_ack:
   pause_ack=True
   if not old:armed=True
 def read(uc,access,at,size,v,ctx):
  global reads,armed
  if pause_ack:
   reads+=1
   # First read tests availability; the second copies the timer pointer.
   # Release on the next instruction, once that pointer is in a register.
   if old and reads==2:armed=True
 def step(uc,at,size,ctx):
  global armed
  if armed and not released:armed=False;do_release()
 def io(uc,access,at,size,v,ctx):
  if released:io_after.append({'offset':at-TIMER,'value':v})
 u.hook_add(UC_HOOK_MEM_WRITE,ack,begin=RADIO+16,end=RADIO+19)
 u.hook_add(UC_HOOK_MEM_READ,read,begin=RADIO+256,end=RADIO+259)
 u.hook_add(UC_HOOK_MEM_WRITE,io,begin=TIMER,end=TIMER+35)
 u.hook_add(UC_HOOK_CODE,step)
 u.reg_write(UC_MIPS_REG_A0,CONTROL|0xa0000000);u.reg_write(UC_MIPS_REG_SP,(BASE+0x1fff0)|0xa0000000)
 u.emu_start(0xbfc00400,0xbfc03ffc,count=15000)
 assert pause_ack and released
 if old:
  assert io_after==[{'offset':4,'value':0},{'offset':0,'value':1},{'offset':32,'value':16},{'offset':12,'value':1},{'offset':4,'value':128}]
  assert [get(TIMER+i) for i in (0,4,12)]==[1,128,1]
 else:
  assert reads==0 and not io_after
  assert [get(TIMER+i) for i in (0,4,12,16)]==[7,0,0,0]
 print(json.dumps({'PE_sha256':hashlib.sha256(binary).hexdigest(),'ARM_source_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),'actual_native_event_release_executed':True,'saved_timer_state_satisfies_prepare_idle_check':True,'timer_pointer_reads_after_pause_ack':reads,'PE_writes_after_ARM_freed_IRQ_restored_and_unmapped_timer':io_after,'restored_timer_values':{str(i):get(TIMER+i) for i in (0,4,12,16)},'scope':'Actual linked MIPS PE and extracted native ARM release C compiled for the host. The interleaving, IRQ synchronization and physical MMIO storage are modeled; no actual IRQ, memory-ordering latency or device test.'},indent=2))
