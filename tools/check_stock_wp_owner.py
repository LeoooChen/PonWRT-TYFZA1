#!/usr/bin/env python3
"""Execute the board's aggregate DMA/SKB release and single-WP completion."""
from pathlib import Path
import hashlib,json,struct,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from arm64_module_oracle import ModuleOracle
module=ROOT/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
assert hashlib.sha256(module.read_bytes()).hexdigest()=='745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
PHL,HAL,HCI,RINGS,REQ,COMMON,STATS=0x6000000,0x6040000,0x6050000,0x6060000,0x6070000,0x6080000,0x6090000
ADAPTER,DVOBJ,BATCH,PKTLIST,IDLE=0x6100000,0x6140000,0x6180000,0x6190000,0x61a0000
unmapped,completed,returned=[],[],[]
def callback(name,args):
 if name=='rtw_hal_get_halcom':return HAL
 if name in ('_phl_wp_rpt_statistics','_phl_wp_rpt_chk_txsts','rtw_phl_get_stainfo_by_macid','__local_bh_enable_ip','rtw_delay_measure_done_tx'):return 0
 if name=='pci_unmap_bus_addr':unmapped.append({'dma':hex(o.unpack(args[1],'Q')[0]),'bytes':args[2],'direction':args[3]});return 0
 if name=='rtw_os_pkt_complete':completed.append(hex(args[1]));return 0
 if name=='rtw_list_insert_tail':returned.append(hex(args[0]));return 0
 raise AssertionError((name,args))
o=ModuleOracle(module,['phl_recycle_payload','txsc_free_txreq','txsc_recycle_txreq_phyaddr'],callback,{'vabits_actual':0x500a000})
o.uc.mem_map(PHL,0x200000);o.word(0x500a000,48)
def put(at,v,fmt='I'):o.uc.mem_write(at,struct.pack('<'+fmt,v))
o.word(PHL+24032,HAL);o.word(HAL+1848,STATS);o.word(PHL+23840,HCI);o.word(HCI+24,RINGS);o.word(PHL+4648,COMMON)
# Original phl_recycle_payload dispatches one tx_request through its core
# callback. Bind the actual stock aggregate release implementation directly;
# station/HAL/OS DMA/free primitives remain explicit callbacks.
o.word(COMMON+12744,o.functions['txsc_free_txreq']);o.word(COMMON+32480,ADAPTER)
o.word(ADAPTER+192,DVOBJ);o.word(ADAPTER+0x20000+19432,IDLE)
results=[]
for count in (1,5,8):
 unmapped.clear();completed.clear();returned.clear();slot=RINGS+168+17*8
 o.word(slot,REQ);o.word(REQ+16,BATCH);put(REQ+24,2);put(REQ+48,8);put(REQ+46,2,'H');put(REQ+121,count,'B');o.word(REQ+112,PKTLIST)
 put(BATCH+120,count,'B');o.word(BATCH+144,0)
 for n in range(count):
  o.word(BATCH+48+n*8,0x61b0000+n*256)
  o.word(PKTLIST+24+n*24+8,0x8000000+n*4096);put(PKTLIST+24+n*24+16,1500,'H')
 put(STATS,9);put(PHL+0x8000+3508,0);put(RINGS+0x58,0)
 o.call('phl_recycle_payload',PHL,0,17,0)
 row={'MSDUs':count,'DMA_unmaps':list(unmapped),'completed_SKBS':list(completed),'batch_return':list(returned),'WP_slot_cleared':o.unpack(slot,'Q')[0]==0,'outstanding_WP_delta':o.unpack(STATS,'I')[0]-9,'successful_MSDU_delta':o.unpack(PHL+0x8000+3508,'I')[0]}
 assert len(unmapped)==count and len(completed)==count and returned==[hex(BATCH)]
 assert row['WP_slot_cleared'] and row['outstanding_WP_delta']==-1 and row['successful_MSDU_delta']==count
 results.append(row)
print(json.dumps({'factory_module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),'actual_functions':['phl_recycle_payload@0x196314','txsc_free_txreq@0xec818','txsc_recycle_txreq_phyaddr@0xec478'],'scope':'Actual aggregate DMA-unmap/SKB-release loops and WP lookup/release/counters. The core callback is bound directly to stock txsc_free_txreq; HAL/station lookup, DMA/OS free and idle-list insertion are modeled. No RF timing or throughput claim.','results':results},indent=2))
