#!/usr/bin/env python3
"""Execute target relay EEPROM diagnostics and distinguish no-op thresholds."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from arm64_elf_oracle import ElfOracle
from unicorn.arm64_const import UC_ARM64_REG_D0


class ModuleOptics:
    def __init__(self, binary):
        self.reads=[];self.fail=0;self.words={}
        self.o=ElfOracle(binary,self.hook,('drv_gpon_fpga_exti2c_read_word',))
        self.output=self.o.scratch+0x8000

    def hook(self,name,a):
        u=self.o.uc
        if name=='drv_gpon_fpga_exti2c_read_word':
            self.reads.append(list(a[:3]))
            assert a[0]==0 and a[1]==0x51 and a[2] in self.words,(name,a)
            if self.fail==len(self.reads):return -1
            # The callback supplies the read_word API's host integer. The
            # target's apparent endian helper is a no-op in these builds.
            u.mem_write(a[3],struct.pack('<H',self.words[a[2]]));return 0
        if name=='log10':
            value=struct.unpack('<d',struct.pack('<Q',u.reg_read(UC_ARM64_REG_D0)))[0]
            assert value>0,value
            u.reg_write(UC_ARM64_REG_D0,struct.unpack('<Q',struct.pack('<d',math.log10(value)))[0]);return 0
        if name=='memset':u.mem_write(a[0],bytes([a[1]&255])*a[2]);return a[0]
        if name in ('plr_log','plr_pondrv_log_module_id_get'):return 0
        raise AssertionError((name,a))

    def call(self,name,*args):
        self.reads=[];self.o.uc.mem_write(self.output,b'\xa5'*64)
        result=self.o.call(name,*args)
        return result,bytes(self.o.uc.mem_read(self.output,64))


def verify(binary):
    stock=ModuleOptics(binary);output=stock.output;noop=[];cases=[]
    for name in ('threshold_alarm','rxpwr_threshold','txpwr_threshold','vcc_threshold','bias_threshold','temp_threshold'):
        symbol='drv_gpon_get_optic_'+name
        for port in (0,1,255):
            ret,data=stock.call(symbol,port,output)
            assert ret==0 and data==b'\xa5'*64 and not stock.reads
        noop.append(symbol)
    for i in (0,1):
        ret,data=stock.call('drv_gpon_polling_pon_module',output,output+32)
        assert ret==0 and data==b'\xa5'*64 and not stock.reads
    # External calibration: Q8 slope, signed Q8 offset, raw ADC.
    # Keep temperature examples positive: the original converter truncates
    # through signed8-bit degrees, which is not a generic sensor contract.
    for slope,offset,raw in ((256,0,6400),(128,256,1000),(512,0,10000)):
        stock.words={80:slope,82:offset,102:raw,84:slope,86:offset,96:raw,
                     76:slope,78:offset,100:raw,88:slope,90:offset,98:raw}
        value=(slope*raw+offset)/256
        expected={'txpwr':int(1000*math.log10(value)-4000),'temp':((int(value/256)+128)%256-128)*100,
                  'bias':int(value/5),'vcc':int(value/100)}
        for field,registers in (('txpwr',[80,82,102]),('temp',[84,86,96]),('bias',[76,78,100]),('vcc',[88,90,98])):
            stock.fail=0;ret,data=stock.call('drv_gpon_get_optic_'+field,0,output)
            assert ret==0 and struct.unpack('<i',data[:4])[0]==expected[field],(field,data[:4],expected[field])
            assert stock.reads==[[0,81,x] for x in registers]
            assert data[4:]==b'\xa5'*60
            for fail in (1,2,3):
                stock.fail=fail;ret,data=stock.call('drv_gpon_get_optic_'+field,0,output)
                assert ret&0xffffffff==0xffffffff and data==b'\xa5'*64
                assert len(stock.reads)==3 # original continues collecting before reporting failure
            cases.append(dict(field=field,slope=slope,offset=offset,raw=raw,result=expected[field],registers=registers))
        stock.fail=0;ret,data=stock.call('plr_api_pon_optical_module_info_get',output)
        assert ret==0
        values=list(struct.unpack('<5h',data[:10]))
        assert values==[expected['vcc'],0,expected['txpwr'],expected['bias'],expected['temp']],values
        assert data[10:]==b'\xa5'*54 and len(stock.reads)==12
        for fail in range(1,13):
            stock.fail=fail;ret,data=stock.call('plr_api_pon_optical_module_info_get',output)
            assert ret&0xffffffff==0xffffffff and data==b'\xa5'*64
    return dict(file=Path(binary).name,sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                no_op_thresholds=noop,polling_is_no_op=True,cases=cases,
                module_info_rx_is_constant_zero=True,
                scope='Actual relay API/getter/conversion instructions and no-op endian helper. read_word host integers, log10 and logging are explicit boundaries; EEPROM wire byte order is not modeled. No BOSA/SFP detection, physical units validation, actual sensor or board-selection evidence.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--relay',type=Path,required=True);p.add_argument('--fpga-relay',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=[verify(a.relay),verify(a.fpga_relay)]
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print('Both original module APIs:24 calibrated getter cases,144 read failures, no-op thresholds/polling and constant-zero aggregate RX verified')
