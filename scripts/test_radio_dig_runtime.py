#!/usr/bin/env python3
"""Replay origin DIG/FAHM against the TYFZA1 factory ARM64 module."""
from pathlib import Path
import struct,sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
bb,hal,phl=0x6000000,0x6020000,0x6040000
registers={0x472c:6<<24,0x473c:1<<9,0x46a8:31<<10}
writes=[]
reads=[]
def callback(name,args):
 if name=='printk':return 0
 if name in ('_rtw_memset','_rtw_memcpy'):
  dst,src,n=args[:3]
  o.uc.mem_write(dst,bytes([src])*n if name=='_rtw_memset' else bytes(o.uc.mem_read(src,n)))
  return dst
 if name in ('halbb_get_reg','halbb_get_reg_cmn'):
  _,addr,mask,_=args
  reads.append((addr,mask))
  return (registers.get(addr,0)&mask)//(mask&-mask) if mask else 0
 if name in ('halbb_set_reg','halbb_set_reg_cmn','halbb_set_reg_phy0_1'):
  _,addr,mask,val=args
  writes.append((addr,mask,val))
  if mask:registers[addr]=(registers.get(addr,0)&~mask)|((val*(mask&-mask))&mask)
  return 0
 if name in ('halbb_get_sys_up_time','halbb_get_sys_time'):return 100
 if name=='halbb_cnvrt_2_sign': return args[1] and ((args[0]&((1<<args[1])-1))-(1<<args[1]) if args[0]&(1<<(args[1]-1)) else args[0])
 if name=='halbb_ccx_ext_loss_update':return 0
 if name in ('halbb_traffic_load_decision','halbb_get_plurality_rx_rate_su','halbb_get_plurality_rx_rate_mu'):return 0
 if name in ('halbb_dig_timer_init',):return 0
 raise AssertionError((name,args))
names=['halbb_cr_cfg_dig_init','halbb_cr_cfg_env_mntr_init','halbb_fahm_init']
names+=['halbb_dig','halbb_dig_init','halbb_dig_mode_update','halbb_dig_abort','halbb_dig_reset','halbb_dig_para_reset','halbb_dig_gain_para_init','halbb_dig_op_unit_para_reset_h','halbb_dig_op_unit_para_reset_l','halbb_dig_para_update','halbb_dig_fahm_para_init','halbb_dig_fahm_trig','halbb_dig_fahm_latch','halbb_dig_cfg_bbcr','halbb_dig_igi_by_ofst','halbb_dig_recorder','halbb_dig_recorder_reset','halbb_dig_damping_chk_init','halbb_dig_damping_chk','halbb_dig_ofst_by_fa','halbb_dyn_pd_th_ofdm','halbb_dyn_pd_th_cck','halbb_sdagc_follow_pagc_config','halbb_get_lna_idx','halbb_get_tia_idx','halbb_get_rxb_idx']
names+=['halbb_ccx_ms_2_period_unit','halbb_fahm_trigger','halbb_fahm_set','halbb_fahm_th_update_chk','halbb_fahm_set_th_reg','halbb_fahm_racing_ctrl','halbb_fahm_racing_release','halbb_fahm_hw_trigger','halbb_fahm_result','halbb_fahm_get_result','halbb_fahm_get_utility','halbb_fahm_cal_wgt','halbb_fahm_cal_wgt_avg','halbb_fahm_get_ratio']
names+=['halbb_set_pd_lower_bound','halbb_set_pd_lower_bound_cck','halbb_set_pd_lower_bound_8852c','halbb_set_pd_lower_bound_cck_8852c','halbb_set_pd_lower_bound_8192xb','halbb_set_pd_lower_bound_cck_8192xb']
names+=['halbb_rx_pkt_su_store_in_sta_info','halbb_cmn_info_self_update']
o=ModuleOracle(root/'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko',names,callback)
o.uc.mem_map(bb,0x80000)
def put(off,v,fmt='I'):o.uc.mem_write(bb+off,struct.pack('<'+fmt,v))
def get(off,fmt='B'):return o.unpack(bb+off,fmt)[0]
o.word(bb,phl);o.word(bb+8,hal)

import ctypes
import random
import subprocess
import tempfile

driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source=r'''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef int8_t s8;typedef uint16_t u16;
typedef uint32_t u32;typedef uint64_t u64;
#include "dig-runtime.h"
static u32 registers[0x20000/4];
static struct rtl_ax_dig dig;
static struct rtl_ax_dig_rssi rssi;
static u32 read32(void *ctx,unsigned int reg){return registers[reg/4];}
static void write32(void *ctx,unsigned int reg,u32 value){registers[reg/4]=value;}
static struct rtl_ax_io io={.read32=read32,.write32=write32};
void native_clear(void){memset(registers,0,sizeof(registers));memset(&dig,0,sizeof(dig));memset(&rssi,0,sizeof(rssi));}
void native_set(unsigned reg,u32 value){registers[(reg|0x10000)/4]=value;}
u32 native_get(unsigned reg){return registers[(reg|0x10000)/4];}
void native_init(unsigned width){rtl_ax_dig_initialize(&io,&dig);rtl_ax_dig_bandwidth(&io,&dig,width);}
void native_update(unsigned chip,bool linked,u8 rssi,s8 loss,u32 now){rtl_ax_dig_update(&io,&dig,chip,linked,rssi,loss,now);}
void native_rssi_sample(u8 value){rtl_ax_dig_rssi_sample(&rssi,value);}
unsigned native_rssi_tick(void){return rtl_ax_dig_rssi_update(&rssi);}
unsigned native_rssi_average(void){return rssi.average;}
unsigned native_state(unsigned field){
 switch(field){
 case 0:return dig.unit[0].igi;case 1:return dig.igi_rssi;
 case 2:return dig.unit[0].follow_pagc;case 3:return dig.damping_active;
 case 4:return dig.false_alarms;case 5:return dig.damping_igi;
 default:return dig.unit[0].fa_threshold[field-6];
 }
}
'''
checks=[0x481c,0x4b64,0x4b74,0x4c24,0x4c28,0x4ce8,0x4cec,
        0xc18,0xc1c,0xc20,0xc24,0xc28]
rng=random.Random(9617)
cases=damped=0
with tempfile.TemporaryDirectory(prefix='dig-runtime-') as tmp:
    p=Path(tmp)
    (p/'test.c').write_text(source)
    subprocess.run(['cc','-O2','-Wall','-Werror','-shared','-fPIC','-I'+str(driver),
                    str(p/'test.c'),'-o',str(p/'test.so')],check=True)
    native=ctypes.CDLL(str(p/'test.so'))
    native.native_get.restype=ctypes.c_uint32
    for chip,width in [(8,w) for w in range(4)]+[(32,w) for w in range(2)]:
        o.uc.mem_write(bb,bytes(0x20000));o.word(bb,phl);o.word(bb+8,hal)
        put(0xc1c,2);put(0xc14,chip);o.word(bb+0xd00,0x820)
        o.uc.mem_write(hal+0x380,struct.pack('<I',width))
        registers.clear();registers.update({0x472c:6<<24,0x473c:1<<9,0x46a8:31<<10})
        o.call('halbb_cr_cfg_dig_init',bb);o.call('halbb_cr_cfg_env_mntr_init',bb)
        o.call('halbb_fahm_init',bb)
        native.native_clear()
        for addr,value in registers.items():native.native_set(addr,value)
        o.call('halbb_dig_init',bb)
        # The channel callback reencodes the initial PD for this width.
        o.call('halbb_set_pd_lower_bound',bb,98,width,0)
        o.call('halbb_set_pd_lower_bound_cck',bb,98,width,0)
        native.native_init(width)
        for addr in checks:
            assert native.native_get(addr)==registers.get(addr,0), ('init',chip,width,hex(addr))
        for step in range(700):
            linked=step%100<80
            rssi=rng.choice([0,20,40,56,80,100,120,140,160,180])
            ready=step%17!=0
            # Full histogram, including 16-bit sum overflow and rounded1000.
            raw=[rng.randrange(6000) for _ in range(12)] if step%3 else [0]*12
            if step%13==0:raw=[58906,0,0,0,0,0,0,0,0,0,0,0]
            for i in range(6):
                value=raw[i*2]|raw[i*2+1]<<16
                registers[0x1aa8+i*4]=value;native.native_set(0x1aa8+i*4,value)
            registers[0x1ac0]=int(ready)<<16;native.native_set(0x1ac0,int(ready)<<16)
            put(0x424e,int(linked and not get(0x424c)),'B')
            put(0x424f,int(not linked and get(0x424c)),'B')
            put(0x424c,int(linked),'B');put(0x4290,rssi,'B');put(0xc2c,step*2)
            # External loss is the calibrated channel input, not an oracle IO guess.
            loss=rng.choice([-4,0,3]) if chip==8 else 0
            put(0x21c3,loss&255,'B')
            o.call('halbb_dig',bb)
            native.native_update(0x52 if chip==8 else 0x53,linked,rssi,loss,step*2)
            for addr in checks:
                actual=native.native_get(addr);expected=registers.get(addr,0)
                assert actual==expected,(chip,width,step,hex(addr),hex(actual),hex(expected))
            expected=[get(0x30c5),get(0x3063),get(0x30cc),get(0x31a0)]
            expected+=list(o.unpack(bb+0x30b6,'4H'))
            fields=[0,1,2,3,6,7,8,9]
            actual=[native.native_state(i) for i in fields]
            assert actual==expected,(chip,width,step,actual,expected)
            damped+=bool(actual[3])
            cases+=1
    # Replay actual station smoothing and the common-state watchdog that
    # consumes packet counts and chooses its next averaging factor.
    o.uc.mem_write(bb,bytes(0x20000));o.word(bb,phl);o.word(bb+8,hal)
    sta,rssi_addr,descriptor=bb+0x10000,bb+0x11000,bb+0x12000
    o.word(bb+24,sta);o.word(sta+0xa80,rssi_addr)
    put(0x411,1,'B');put(0x491,1,'B')
    o.uc.mem_write(sta+0x18,bytes([1]));o.uc.mem_write(sta+0x1c,bytes([1,0]))
    o.uc.mem_write(hal+0x62d,bytes([1]));o.uc.mem_write(phl+0x58fa,bytes([54]))
    o.uc.mem_write(descriptor+4,bytes([1]))
    native.native_clear()
    samples=0
    for count in [1,0,5,6,20,21,100,101,200]*8:
        for _ in range(count):
            value=rng.randrange(1,200)
            put(0x3390,value,'B')
            o.call('halbb_rx_pkt_su_store_in_sta_info',bb,descriptor)
            native.native_rssi_sample(value)
            assert native.native_rssi_average()==o.unpack(rssi_addr+6,'H')[0]
            samples+=1
        o.call('halbb_cmn_info_self_update',bb)
        assert native.native_rssi_tick()==get(0x4290)
print(f'PASS: {cases} factory ARM64 origin DIG/FAHM watchdog steps, C/XB initialization and 20/40/80/160 MHz register/state parity')
print(f'PASS: {samples} factory station RSSI samples and packet-count smoothing transitions; damping active in {damped} watchdog steps')

