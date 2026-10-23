#!/usr/bin/env python3
"""Execute TYFZA1 RXBD batch notification and RDU handling on both radios."""
from pathlib import Path
import sys,struct,json,hashlib
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
M=root/'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko'
PHL,HAL,COMMON,OPS,POOL,BD,DESC,BUF= [0x6000000+i*0x10000 for i in range(8)]
results=[]
for chip in ('8852ce','8192xbe'):
 events=[];idle=[]
 def boundary(name,args):
  if name=='query_idle_rx_buf':return idle.pop(0) if idle else 0
  if name=='enqueue_busy_rx_buf':events.append(['busy',args[2]]);return 0
  if name=='hal_get_rxbd_num_'+chip:return 256
  if name=='_hal_get_bd_idx_reg_'+chip:return 0x1218 if args[0]==16 else 0x121c
  if name in ('_hal_write16','_hal_write32'):
   events.append([name,args[1],args[2]])
   return 0
  raise AssertionError((name,args))
 names=['_phl_refill_rxbd','rtw_hal_update_rxbd','rtw_hal_notify_rxdone','hal_update_rxbd_'+chip,'hal_notify_rxdone_'+chip,'hal_int_hdler_'+chip,'hal_clear_int_'+chip]
 o=ModuleOracle(M,names,boundary)
 o.uc.mem_map(PHL,0x80000)
 o.word(PHL+0x5de0,HAL);o.word(HAL+8,COMMON);o.word(HAL,PHL)
 o.word(HAL+24,OPS);o.word(OPS+0xe0,o.functions['hal_update_rxbd_'+chip]);o.word(OPS+0xe8,o.functions['hal_notify_rxdone_'+chip])
 o.uc.mem_write(COMMON+0x2cf,b'\x08');o.word(BD,DESC)
 for channel in (0,1):
  for count in (0,1,16,255):
   events.clear();o.uc.mem_write(BD+0x22,struct.pack('<H',254))
   idle[:]=[BUF+i*64 for i in range(count)]
   for i,a in enumerate(idle):
    o.uc.mem_write(a+0x20,struct.pack('<III',0x10000000+i*16384,0,12288 if not channel else 1024))
   o.call('_phl_refill_rxbd',PHL,POOL,BD,channel,count,instruction_budget=100000)
   writes=[e for e in events if e[0]=='_hal_write16']
   assert writes==([['_hal_write16',0x1218+channel*4,(254+count)%256]] if count else []),writes
   assert len(events)==count+bool(count)
   if count:assert events[-1]==writes[0]
   for i in range(count):
    word,address=o.unpack(DESC+((254+i)%256)*8,'II')
    assert word&0x3fff==(12288 if not channel else 1024) and address==0x10000000+i*16384
   results.append({'chip':chip,'channel':channel,'descriptors':count,'notifications':writes})
 # Hardware RDU status is W1C; the handler schedules RX, not a DMA reset.
 for status in (1<<19,1<<20,(1<<19)|(1<<20)):
  events.clear();o.uc.mem_write(COMMON+0x6a8,b'\x01')
  o.uc.mem_write(COMMON+0x6b4,struct.pack('<I',status));o.uc.mem_write(COMMON+0x6b0,struct.pack('<I',0x180005))
  before=o.unpack(PHL+0x7f6c,'I')[0]
  o.call('hal_clear_int_'+chip,HAL)
  assert events==[['_hal_write32',0x10b4,status]]
  flags=o.call('hal_int_hdler_'+chip,HAL)
  assert flags&2 and o.unpack(PHL+0x7f6c,'I')[0]==before+bool(status&(1<<19))
  assert events[-1]==['_hal_write32',0x10b0,0]
print(json.dumps({'module_sha256':hashlib.sha256(M.read_bytes()).hexdigest(),'refill_batches':results,'RDU_ack_schedules_RX_without_reset':True,'scope':'Actual stock refill, HAL update and notification functions; idle/busy queues and MMIO callbacks modeled.'},indent=2))
