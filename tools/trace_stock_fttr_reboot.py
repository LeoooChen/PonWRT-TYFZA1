#!/usr/bin/env python3
"""Execute factory ONU-G reboot producer/encoder with explicit transport boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from arm64_elf_oracle import ElfOracle


class Reboot:
    def __init__(self, binary):
        self.events=[]
        self.oracle=ElfOracle(binary,self.boundary,('plr_gponrm_pon_protect_is_standby_port',
            'plr_gponrm_onu_hw_id_get_by_lg_id','plr_gponomci_sync_api',
            'plr_gponomci_metrans_id_prep','plr_gponomci_create_trans',
            'plr_gponomci_delete_trans','plr_gponomci_me_swimg_dld_tx',
            'plr_gponomci_module_log','plr_log','plr_gponomci_get_trans',
            'plr_gponomci_trans_mutex_take_with_timeout','plr_gponomci_trans_mutex_give'))
        self.base=self.oracle.scratch+0x2000

    def boundary(self,name,a):
        u=self.oracle.uc
        if name in ('memset','memmove'):
            u.mem_write(a[0],bytes([a[1]&255])*a[2] if name=='memset' else bytes(u.mem_read(a[1],a[2])));return a[0]
        if name in ('plr_log','plr_gponomci_module_log'):return 0
        if name=='plr_gponrm_pon_protect_is_standby_port':return 0
        if name=='plr_gponrm_onu_hw_id_get_by_lg_id':
            u.mem_write(a[1],struct.pack('<I',self.onu));return 0
        if name=='plr_gponomci_sync_api':
            assert (a[0],a[2],a[4])==(256,87,14)
            self.recipe=bytes(u.mem_read(a[1],a[2]));u.mem_write(a[3],bytes(a[4]));return 0
        if name=='plr_gponomci_get_trans':
            u.mem_write(a[2],struct.pack('<Q',self.base+0x200));return 0x80000000
        if name in ('plr_gponomci_trans_mutex_take_with_timeout','plr_gponomci_trans_mutex_give'):return 0
        if name=='plr_gponomci_metrans_id_prep':return self.tci
        if name=='plr_gponomci_create_trans':self.events.append('create');return self.create_result
        if name=='plr_gponomci_delete_trans':self.events.append('delete');return 0x80000000
        if name=='plr_gponomci_me_swimg_dld_tx':
            assert a[2]==48
            self.frame=bytes(u.mem_read(a[1],a[2]));self.events.append('send');return self.send_result
        raise AssertionError(('unmodeled reboot call',name,a))

    def run(self,onu=7,tci=0x8123,create_result=0x80000000,send_result=0x80000000):
        self.onu,self.tci,self.create_result,self.send_result=onu,tci,create_result,send_result
        self.events=[];self.frame=None
        p=self.base;o=self.oracle
        o.uc.mem_write(p,struct.pack('<HH',0,onu+1))
        assert o.call('plr_gponrm_onu_reboot',p)==0
        assert self.recipe==struct.pack('<II',onu,15)+bytes(79)
        o.uc.mem_write(p,self.recipe)
        result=o.call('plr_gponomci_onug_reboot',256,p,len(self.recipe),p+0x100,14)
        expected=create_result if create_result!=0x80000000 else send_result
        assert result==expected
        assert self.events==(['create'] if create_result!=0x80000000 else ['create','send','delete'])
        if self.frame:
            assert self.frame==struct.pack('>HBBHH',tci,0x59,10,256,0)+bytes(35)+b'\x28'+bytes(4)
        return dict(onu=onu,tci=tci,result=result,events=self.events,wire=self.frame.hex() if self.frame else None)

    def ack(self,status):
        p=self.base;o=self.oracle
        trans=bytearray(112)
        struct.pack_into('<II',trans,24,7,15)
        struct.pack_into('<I',trans,44,48)
        trans[48:96]=struct.pack('>HBBHH',0x8123,0x39,10,256,0)+bytes([status])+bytes(34)+b'\x28'+bytes(4)
        struct.pack_into('<Q',trans,104,p+0x400)
        o.uc.mem_write(p+0x200,bytes(trans));o.uc.mem_write(p+0x400,bytes(14))
        assert o.call('plr_gponomci_onug_reboot_ack',0x8123,p)==0x80000000
        observed=struct.unpack('<I',o.uc.mem_read(p+0x400+10,4))[0]
        # Factory result parser only handles opcode0x24..0x32; reboot0x39
        # falls through without copying byte8, so the zero local value wins.
        assert observed==0,(observed,status)
        return observed


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--relay',type=Path,required=True)
    p.add_argument('--fpga-relay',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();reports=[]
    for binary in (args.relay,args.fpga_relay):
        stock=Reboot(binary)
        cases=[stock.run(onu,tci) for onu in (0,7,63) for tci in (0x8001,0x8123,0xffff)]
        cases += [stock.run(create_result=0x80000010),stock.run(send_result=0x8000000b)]
        acks=[stock.ack(status) for status in range(7)]
        reports.append(dict(file=binary.name,sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),cases=cases,ack_results=acks))
    args.output.write_text(json.dumps(dict(reports=reports,scope='Actual factory API, ONU-G dispatcher, action/class mapping and wire encoding; transaction allocation and transport are explicit models, no remote restart or retry-engine claim.'),indent=2)+'\n')
    print('Factory ASIC/FPGA1 reboot: 22 producer/encoder/failed-transport + 14 actual ACK result cases passed')


if __name__=='__main__':main()
