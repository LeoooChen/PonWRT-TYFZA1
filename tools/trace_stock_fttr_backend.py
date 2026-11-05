#!/usr/bin/env python3
"""Execute original controller dispatch and FPGA2 startup with synthetic platform IO."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from elftools.elf.elffile import ELFFile
from arm64_elf_oracle import ElfOracle
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_PC

class Dispatched(Exception): pass

def dispatch(binary, backend):
    result = {}
    def hook(name, a):
        if name == 'drv_get_miniolt_chiptype': return backend
        if name == 'memset': oracle.uc.mem_write(a[0], bytes([a[1]&255])*a[2]); return a[0]
        if name == 'execvp':
            result['executable'] = bytes(oracle.uc.mem_read(a[0],128)).split(b'\0')[0].decode()
            raise Dispatched()
        if name in ('daemon','signal','fork','puts'): return 0
        raise AssertionError(name)
    oracle=ElfOracle(binary,hook)
    oracle.symbols['entry']=0x400adc
    try: oracle.call('entry')
    except Dispatched: pass
    assert result['executable'] == ('/bin/onuinfo_fpga' if backend==1 else '/bin/onuinfo'),result
    return dict(backend=backend,**result)


def startup(binary, backend, mode):
    copies={}
    with binary.open('rb') as stream:
        elf=ELFFile(stream);symbols=elf.get_section_by_name('.dynsym')
        for section in elf.iter_sections():
            if section['sh_type']=='SHT_RELA':
                for reloc in section.iter_relocations():
                    if reloc['r_info_type']==1024:
                        sym=symbols.get_symbol(reloc['r_info_sym']);copies[sym.name]=bytes(sym['st_size'])
    events=[]
    def hook(name,a):
        if name=='drv_get_miniolt_chiptype': return backend
        if name=='mib_get_s':
            assert a[0]==2706 and a[2]==1
            oracle.uc.mem_write(a[1],bytes([mode]));return 1
        if name=='memset': oracle.uc.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
        if name=='memcpy': oracle.uc.mem_write(a[0],bytes(oracle.uc.mem_read(a[1],a[2])));return a[0]
        if name=='getInAddr':
            assert a[1]==4;oracle.uc.mem_write(a[2],b'\0\0'+bytes.fromhex('020304050607')+bytes(16));return 1
        if name=='fopen64': return 0
        if name=='sched_get_priority_max': return 1
        if name in ('create_thread','pthread_create','printf','puts','fflush','version_banner'): return 0
        if name=='SetOnuTcontDba': events.append([name,*a[:5]]);return 0
        if name=='drv_get_fpga_version': oracle.uc.mem_write(a[0],struct.pack('<I',0x23040700));return 0
        if name in ('drv_reset_phy0','drv_reset_phy1','drv_set_optical_delay','drv_reset_soft',
                'drv_set_reg_otp','drv_set_pause_en','drv_mask_interrupt','usleep',
                'drv_set_olt_dba_bw_max','drv_set_olt_dba_bw_min_user_fec','drv_set_olt_dba_bw_min_omci_fec',
                'drv_set_olt_dba_bw_min_user','drv_set_olt_dba_bw_min_omci'):
            events.append([name,a[0]]);return 0
        if name in ('drv_set_pcbd_dst_mac','drv_set_omci_dst_mac'):
            events.append([name,bytes(oracle.uc.mem_read(a[0],6)).hex()]);return 0
        if name=='drv_set_reg_ploam_print': events.append([name,*a[:2]]);return 0
        if name.startswith('plr_api_register_onu_') or name=='plr_api_main_init':
            events.append([name]);return 0
        raise AssertionError((name,a))
    oracle=ElfOracle(binary,hook,copy_symbols=copies)
    oracle.hooks[0x4173a0]='version_banner'
    oracle.hooks[0x418ce0]='create_thread'
    oracle.symbols['startup']=0x418e30
    assert oracle.call('startup')==0
    assert events[:6]==[['drv_reset_phy0',0],['drv_reset_phy1',0],['drv_set_optical_delay',6],
            ['drv_reset_soft',0],['usleep',100],['drv_reset_soft',1]]
    assert ['drv_set_reg_otp',6 if mode==5 else 7] in events
    assert (['drv_set_pause_en',1] in events)==(mode!=5)
    dba=[e for e in events if e[0].startswith('drv_set_olt_dba_')]
    assert [e[1] for e in dba]==[19440,38,38,10,10],dba
    startup_events=list(events);callbacks=[]
    # Actual registered-status callback's default DBA loop. It uses the
    # OltSetting table initialized by the executed startup, not a new fixture.
    for id in (1,6,64):
        events.clear();stack=oracle.scratch+0x1ff000
        oracle.uc.reg_write(UC_ARM64_REG_SP,stack)
        oracle.uc.mem_write(stack+60,struct.pack('<I',id))
        oracle.uc.emu_start(0x415ec4,0x415fc0,count=20000)
        assert oracle.uc.reg_read(UC_ARM64_REG_PC)==0x415fc0
        assert events==[['SetOnuTcontDba',id,0,0,3840,3840],
                        ['SetOnuTcontDba',id,1,0,0,19440*64]],events
        callbacks.extend(events)
    return dict(backend=backend,host_mode=mode,events=startup_events,registration_dba=callbacks)


def fpga2_registers(binary, events):
    values={0x40022:0xa5f0,0x30005:8}; trace=[]
    def boundary(name,a):
        if name=='drv_get_miniolt_chiptype':return 2
        if name=='FPGA_reg_read':
            value=values.get(a[0],0);trace.append(['read',a[0],value]);return value
        if name=='FPGA_reg_write':
            values[a[0]]=a[1];trace.append(['write',*a[:2]]);return 0
        if name in ('open','flock','close'):return 3 if name=='open' else 0
        raise AssertionError(name)
    oracle=ElfOracle(binary,boundary,('FPGA_reg_read','FPGA_reg_write','drv_get_miniolt_chiptype'))
    for name,value in events:
        if name=='usleep':trace.append(['delay',value]);continue
        if name in ('drv_reset_phy0','drv_reset_phy1','drv_set_optical_delay','drv_reset_soft','drv_set_reg_otp','drv_set_pause_en'):
            oracle.call(name,value)
    return trace


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('watchdog','caller','asic','asic-relay','fpga-relay','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    dispatches=[dispatch(args.watchdog,b) for b in (1,2,3)]
    starts=[startup(args.caller,2,m) for m in (5,9)]
    for start in starts:
        selected=[e for e in start['events'] if e[0] in ('usleep','drv_reset_phy0','drv_reset_phy1','drv_set_optical_delay','drv_reset_soft','drv_set_reg_otp','drv_set_pause_en')]
        start['registers']=fpga2_registers(args.asic,selected)
    pools=[]
    for binary,backends,expected in ((args.asic_relay,[2,3],(257,767)),(args.fpga_relay,[1],(256,319))):
        def unused(name,a):
            assert name=='unused';return 0
        oracle=ElfOracle(binary,unused)
        oracle.hooks[0x914d4 if 2 in backends else 0x8e414]='unused'
        assert oracle.call('plr_gpon_const_val_init')==0
        actual=(oracle.call('plr_gpon_min_allocId_get'),oracle.call('plr_gpon_max_allocId_get'))
        assert actual==expected
        cases=[]
        for id in range(64):
            for slot in range(1,8):
                ret=oracle.call('plr_ponsrv_alloc_id_get',1,id+1,slot,oracle.scratch+0x2000)
                if 2 not in backends and slot!=1: assert ret!=0;continue
                assert ret==0
                alloc=struct.unpack('<H',oracle.uc.mem_read(oracle.scratch+0x2000,2))[0]
                assert alloc==(256+id*8+slot if 2 in backends else 256+id)
                cases.append([id,slot,alloc])
        pools.append(dict(backends=backends,range=actual,allocations=cases,sha256=hashlib.sha256(binary.read_bytes()).hexdigest()))
    with args.caller.open('rb') as stream:
        elf=ELFFile(stream)
        needed=[tag.needed for tag in elf.get_section_by_name('.dynamic').iter_tags() if tag.entry.d_tag=='DT_NEEDED']
        assert 'libponrelay.so' in needed and 'libponrelay1p25g.so' not in needed
    report=dict(dispatch=dispatches,fpga2_startup=starts,pools=pools,
        watchdog_sha256=hashlib.sha256(args.watchdog.read_bytes()).hexdigest(),
        caller_sha256=hashlib.sha256(args.caller.read_bytes()).hexdigest(),
        scope='Executed original ARM64 dispatch, complete FPGA2 controller startup with modeled platform calls, actual constants/allocators with free occupancy. No physical board IO.')
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: original dispatch selects ASIC relay for FPGA2, full FPGA2 startup and 448 FPGA2/ASIC allocator cases')
if __name__=='__main__':main()
