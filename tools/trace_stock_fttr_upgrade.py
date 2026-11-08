#!/usr/bin/env python3
"""Execute target Software Image producers, windows and wire encoders offline."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from arm64_elf_oracle import ElfOracle


class Upgrade:
    def __init__(self,binary):
        self.oracle=ElfOracle(binary,self.boundary,('plr_gponrm_onu_hw_id_get_by_lg_id',
            'plr_gponomci_async_api','plr_gponomci_metrans_id_prep',
            'plr_gponomci_create_swimg_dld_trans','plr_gponomci_delete_swimg_dld_trans',
            'plr_gponomci_me_swimg_dld_tx','plr_gponrm_cfg_image_get',
            'plr_gponomci_module_log','plr_log','plr_gponrm_image_get',
            'plr_gponrm_image_info_by_pos_id','plr_gponrm_swimg_dld_start_if',
            'plr_gponrm_swimg_dld_activate_if','plr_gponrm_swimg_dld_commit_if',
            'plr_gponrm_image_mgmt_get','plr_gponrm_pr_image_get'))
        self.base=self.oracle.scratch+0x2000
        self.onu=7;self.tci=0x8123;self.fail_at=0;self.events=[];self.frames=[];self.transactions=[]
        self.oracle.call('plr_gponrm_sw_crc32_table_init')

    def boundary(self,name,a):
        u=self.oracle.uc
        if name in ('memset','memmove'):
            u.mem_write(a[0],bytes([a[1]&255])*a[2] if name=='memset' else bytes(u.mem_read(a[1],a[2])));return a[0]
        if name in ('plr_log','plr_gponomci_module_log','printf'):return 0
        if name=='usleep':self.sleeps.append(a[0]);return 0
        if name=='plr_gponrm_onu_hw_id_get_by_lg_id':u.mem_write(a[1],struct.pack('<I',self.onu));return 0
        if name=='plr_gponomci_async_api':
            assert a[0]==7
            self.events.append(bytes(u.mem_read(a[1],a[2])))
            return 1 if len(self.events)==self.fail_at else 0
        if name=='plr_gponomci_metrans_id_prep':self.tci+=1;return self.tci
        if name=='plr_gponomci_create_swimg_dld_trans':
            self.transactions.append((a[1],a[3],a[4]));return 0x80000000
        if name=='plr_gponomci_delete_swimg_dld_trans':return 0x80000000
        if name=='plr_gponomci_me_swimg_dld_tx':
            assert a[2]==48;self.frames.append(bytes(u.mem_read(a[1],a[2])));return 0x80000000
        if name=='plr_gponrm_image_mgmt_get':return self.base+0xc00
        if name=='plr_gponrm_pr_image_get':
            instance=struct.unpack('<H',u.mem_read(a[2],2))[0]
            assert instance in (0,1) and a[1]==0xf000
            self.read_instances.append(instance)
            row=bytearray(64);row[6:23]=self.image_attributes[instance]
            u.mem_write(a[2],bytes(row));return 0
        if name=='plr_gponrm_image_get':return 0
        if name=='plr_gponrm_image_info_by_pos_id':return self.base+0xa00+64*a[1]
        if name in ('plr_gponrm_swimg_dld_start_if','plr_gponrm_swimg_dld_activate_if','plr_gponrm_swimg_dld_commit_if'):
            self.selection=a[1];return 0
        if name=='plr_gponrm_cfg_image_get':return self.base+0x400
        raise AssertionError(('unmodeled upgrade boundary',name,a))

    def encode(self):
        names={8:'start_dld',9:'dld_sec',10:'end_dld',11:'activate',12:'commit'}
        self.frames=[];self.transactions=[]
        for recipe in self.events:
            action=struct.unpack_from('<I',recipe,4)[0]
            self.oracle.uc.mem_write(self.base+0x800,recipe)
            assert self.oracle.call('plr_gponomci_swimg_'+names[action],self.base+0x800,len(recipe))==0x80000000
        return list(self.frames)

    def request(self,action,image=1,window=31,size=1234,crc=0x12345678,section=0,data=b'',ack=True):
        self.events=[];self.fail_at=0;o=self.oracle;p=self.base
        o.uc.mem_write(p,struct.pack('<HH',0,8));o.uc.mem_write(p+0x100,data.ljust(31,b'\0'))
        args={'start':(image,window,size),'sec':(image,int(ack),section,len(data),p+0x100),
            'end':(image,crc,size),'activate':(image,),'commit':(image,)}[action]
        assert o.call('plr_gponrm_image_dld_'+action,p,*args)==0
        return self.encode()[0]

    def window(self,data,offset=0,window=31,fail_at=0,retry=False,retries=0):
        o=self.oracle;p=self.base;self.events=[];self.fail_at=fail_at;self.sleeps=[]
        row=bytearray(300);struct.pack_into('<Q',row,258,p+0x1000)
        row[266]=window;row[284]=retries;struct.pack_into('<II',row,267,len(data),offset)
        o.uc.mem_write(p+0x400,bytes(row));o.uc.mem_write(p+0x1000,data)
        name='plr_gponrm_cfg_image_dld_retransfer_win' if retry else 'plr_gponrm_cfg_image_dld_next_win'
        result=o.call(name,p,1)
        frames=self.encode()
        return result,frames,bytes(o.uc.mem_read(p+0x400,300))

    def end_retry(self,count,data=b'123456789'):
        p=self.base;o=self.oracle;self.events=[];self.sleeps=[]
        row=bytearray(300);struct.pack_into('<Q',row,258,p+0x1000);struct.pack_into('<I',row,267,len(data));row[284]=count
        o.uc.mem_write(p+0x400,bytes(row));o.uc.mem_write(p+0x1000,data)
        ret=o.call('plr_gponrm_cfg_image_dld_retry_end',p,1)
        self.encode();return ret,list(self.sleeps),list(self.frames)

    def select(self,action,rows):
        self.selection=None;p=self.base;o=self.oracle
        for i,(committed,active,valid) in enumerate(rows):
            row=bytearray(64);row[17:20]=bytes((committed,active,valid))
            o.uc.mem_write(p+0xa00+i*64,bytes(row))
        o.uc.mem_write(p+0x100,b'\0\0test.bin\0'+bytes(256))
        ret=o.call('plr_gponrm_cfg_swimg_dld_'+action,p,p+0x100)
        return ret,self.selection

    def inventory(self,attributes):
        self.image_attributes=attributes;self.read_instances=[]
        o=self.oracle;p=self.base
        # This one call executes the real image_get body despite its override
        # used by selection tests; internal read transport remains modeled.
        address=o.symbols['plr_gponrm_image_get']
        name=o.hooks.pop(address)
        try:
            o.uc.mem_write(p+0xc00,bytes(124))
            assert o.call('plr_gponrm_image_get',p)==0
        finally:o.hooks[address]=name
        rows=bytes(o.uc.mem_read(p+0xc00,124))
        assert self.read_instances==[0,1]
        for i,attrs in enumerate(attributes):
            row=rows[i*62:(i+1)*62]
            assert row[2:16]==attrs[:14] and row[17:20]==attrs[14:17]
        return rows.hex()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--relay',type=Path,required=True)
    p.add_argument('--fpga-relay',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();reports=[]
    for binary in (args.relay,args.fpga_relay):
        stock=Upgrade(binary);cases=[]
        for image in (0,1):
            for action,opcode in (('start',0x53),('sec',0x54),('end',0x55),('activate',0x56),('commit',0x57)):
                wire=stock.request(action,image=image,data=bytes(range(31)))
                assert wire[2:8]==struct.pack('>BBHH',opcode,10,7,image),(action,wire.hex())
                assert wire[40:]==bytes.fromhex('0000002800000000')
                cases.append(dict(action=action,image=image,wire=wire.hex(),transactions=stock.transactions))
        for size in (1,30,31,32,62,63,960,961,962,1923):
            data=bytes(i%251 for i in range(size));ret,frames,row=stock.window(data)
            assert ret==0
            count=min(31,(size+30)//31)
            assert len(frames)==count
            for i,frame in enumerate(frames):
                assert frame[2]==(0x54 if i==count-1 else 0x14)
                assert frame[8]==i and frame[9:40]==data[i*31:(i+1)*31].ljust(31,b'\0')
            assert struct.unpack_from('<I',row,271)[0]==0
            assert struct.unpack_from('<I',row,275)[0]==min(size,961)
            cases.append(dict(size=size,sections=count,last=frames[-1].hex()))
        for count in (0,1,2):
            ret,frames,row=stock.window(bytes(range(63)),retry=True,retries=count)
            assert ret==(count>=2) and len(frames)==(0 if count>=2 else 3)
            assert row[284]==count+1
        for failed in (1,2,3):
            ret,frames,row=stock.window(bytes(range(63)),fail_at=failed)
            assert ret==1 and len(frames)==failed
            assert struct.unpack_from('<I',row,271)[0]==0
        for count in (0,1,119,120):
            ret,sleeps,frames=stock.end_retry(count)
            assert ret==(count>=120) and sleeps==([] if count>=120 else [4000000])
            assert len(frames)==(count<120)
        inventories=[]
        for state in range(8):
            attributes=[b'VERSION0000001'+bytes((state&1,(state>>1)&1,(state>>2)&1)),
                        b'VERSION0000002'+bytes((0,1,1))]
            inventories.append(stock.inventory(attributes))
        selection_cases=[]
        for action,error in (('start',72),('active',74),('commit',75)):
            for left in range(8):
                for right in range(8):
                    rows=[tuple((v>>bit)&1 for bit in (0,1,2)) for v in (left,right)]
                    candidates=[i for i,(c,active,valid) in enumerate(rows) if
                        (not c and not active if action=='start' else valid and not active if action=='active' else valid and not c)]
                    ret,index=stock.select(action,rows)
                    assert (ret,index)==((0,candidates[0]) if candidates else (error,None)),(action,rows,ret,index)
                    selection_cases.append([action,rows,ret,index])
        p0=stock.base+0x1000;stock.oracle.uc.mem_write(p0,b'123456789')
        crc=stock.oracle.call('plr_gponrm_ver_crc32_get',p0,9)
        assert crc==0xfc891918,hex(crc)
        reports.append(dict(file=binary.name,sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),cases=cases,crc=crc,selection_cases=selection_cases,inventories=inventories))
    args.output.write_text(json.dumps(dict(reports=reports,scope='Executed factory image producers/window/encoder/retry code. Memory, async dispatch and transport are modeled; no flash write or physical activation.'),indent=2)+'\n')
    print('Factory upgrade: both backends, 40 wire/window cases, 384 image selections, 16 version/flag inventories, 12 window-failure/retry cases, 8 end-retry boundaries and image CRC passed')


if __name__=='__main__':main()
