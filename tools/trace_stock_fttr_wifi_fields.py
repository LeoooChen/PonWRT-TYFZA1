#!/usr/bin/env python3
"""Execute fttrManage Wi-Fi field producer and remote configuration builder."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_elf_oracle import ElfOracle
from trace_stock_fttr_sync import SyncOriginal
from unicorn.arm64_const import UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_SP


class WifiFieldsOriginal(SyncOriginal):
    def __init__(self, manager, mib):
        assert hashlib.sha256(Path(manager).read_bytes()).hexdigest() == \
            '9c8b1c80b3efe72f0a6f03b27bdf93b3cd6027021c00350b307807f522df195b'
        assert hashlib.sha256(Path(mib).read_bytes()).hexdigest() == \
            '5e14985931f51977c0b0e190bfa1c16122b8614b596f58f7ad121a83beb37ec2'
        self.oracle = ElfOracle(manager, self.handler, ('DBG_LOG',), copy_symbols={
            name: bytes(size) for name, size in [('FTTRMANAGE_PID',24),('BRIF',4),
            ('SYNCSERV_USOCK_SERVER',24),('SYSTEMD_USOCK_SERVER',23),('ARG_0x4',8),
            ('WLANIF',16),('uloop_cancelled',1),('wlan_idx',4),('ELANVIF',160),('stderr',8)]})
        self.mib = ElfOracle(mib, self.mib_handler)
        self.objects = {}
        self.next_object = self.oracle.scratch + 0x20000
        self.text = self.oracle.scratch + 0x50000
        self.next_text = self.oracle.scratch + 0x80000
        self.peer = bytes.fromhex('0a0b0c0d0e0f')
        self.row = bytearray(self.entry(4, b'synthetic-name', b'synthetic-password'))
        self.row[338] = 11
        self.sent = []
        self.selected = []

    def text_pointer(self, value):
        text = value.encode()
        pointer = self.next_text
        self.next_text += (len(text)+16) & ~7
        assert self.next_text < self.oracle.scratch + 0x180000
        self.oracle.uc.mem_write(pointer, text+b'\0')
        return pointer

    def mib_handler(self, name, a):
        if name == 'strcmp':
            def string(p):
                result=bytearray()
                while self.mib.uc.mem_read(p+len(result),1)[0]:
                    result.extend(self.mib.uc.mem_read(p+len(result),1))
                return bytes(result)
            x,y=string(a[0]),string(a[1])
            return (x>y)-(x<y)
        raise AssertionError(('unexpected MIB IO', name))

    def handler(self, name, a):
        u = self.oracle.uc
        if name == 'json_object_get_string':
            if not a[0]: return 0
            value=self.objects[a[0]]
            return self.text_pointer(value if isinstance(value,str) else json.dumps(value,separators=(',',':')))
        if name == 'json_object_to_json_string':
            return self.text_pointer(json.dumps(self.objects[a[0]],separators=(',',':'),ensure_ascii=False))
        if name == 'json_object_object_get_ex':
            value=self.objects[a[0]].get(self.string(a[1]))
            u.mem_write(a[2],struct.pack('<Q',self.object(value) if value is not None else 0))
            return int(value is not None)
        if name == 'json_object_array_length':return len(self.objects[a[0]])
        if name == 'json_object_array_get_idx':return self.object(self.objects[a[0]][a[1]])
        if name == 'atoi':return int(self.string(a[0]))
        if name == 'snprintf':
            fmt=self.string(a[2])
            values=a[3:]+[u.reg_read(UC_ARM64_REG_X6),u.reg_read(UC_ARM64_REG_X7),
                           struct.unpack('<I',u.mem_read(u.reg_read(UC_ARM64_REG_SP),4))[0]]
            if fmt=='%s':text=self.string(values[0])
            else:
                assert fmt.count('%') in (0,1,6),fmt
                text=fmt % tuple(values[:fmt.count('%')])
            text=text.encode();u.mem_write(a[0],text[:a[1]-1]+b'\0');return len(text)
        if name in ('strcmp','strncasecmp'):
            x,y=self.string(a[0]),self.string(a[1])
            if name=='strncasecmp':x,y=x[:a[2]].lower(),y[:a[2]].lower()
            return (x>y)-(x<y)
        if name == 'getWlanEntryBySSIDIdx':
            self.selected.append(a[0])
            assert 1<=a[0]<=16
            u.mem_write(a[1],struct.pack('<I',int(a[0]>8)))
            u.mem_write(a[2],bytes(self.row))
            return (a[0]-1)%8+1
        if name == 'mib_chain_get':
            if a[0]==3210:
                assert a[1]<16
                row=bytearray(1200);row[1:7]=self.peer;row[1038]=1
                u.mem_write(a[2],bytes(row))
            else:
                assert a[0]==3501 and a[1]<8
                u.mem_write(a[2],bytes(self.row))
            return 1
        if name == 'mib_local_mapping_get':
            assert a[0] in (1265,1469,1470)
            u.mem_write(a[2],bytes([1 if a[0]==1265 else 0]))
            return 1
        if name == 'map_encrypt_to_mib':return self.mib.call(name,a[0])
        if name == 'map_wlan_standard_to_mib':
            self.mib.uc.mem_write(self.mib.scratch+0x80000,self.string(a[1]).encode()+b'\0')
            return self.mib.call(name,a[0],self.mib.scratch+0x80000)
        if name == 'sync_syncserv_setting':
            self.sent.append({'flag':a[0],'content':json.loads(self.string(a[1])),'mac':self.string(a[2])})
            return 0
        if name in ('DBG_LOG','printf','free'):return 0
        return super().handler(name,a)

    def write(self, value):
        self.sent.clear();self.selected.clear()
        self.oracle.call('setWiFiInfo',0,self.text_pointer(json.dumps(value)),self.object({}))
        assert len(self.sent)==1
        return self.sent[0]

    def read_local(self):
        result=self.oracle.call('getWifiSsidInfo',0,self.text_pointer('{}'),self.object({}))
        return self.objects[result]


def verify(manager, mib):
    stock=WifiFieldsOriginal(manager,mib)
    read=stock.read_local()
    assert len(read['List'])==16
    assert [int(r['SSIDIndex']) for r in read['List']]==list(range(1,17))
    assert all('PWD' not in r and r['ENCRYPT']=='4' and r['SSID']=='synthetic-name' for r in read['List'])
    cases=[]
    for code,raw in ((1,0),(2,1),(3,2),(4,4),(5,6),(9,16),(10,20)):
        for index in (17,24,25,32,33,48,257,272):
            value={'SSIDIndex':str(index),'Enable':'1','SSID':'synthetic-new',
                   'ENCRYPT':str(code),'PWD':'abcde' if code==2 else 'synthetic-new-password',
                   'SSIDAdvertisementEnabled':'true','Standard':'bgn'}
            actual=stock.write(value)
            expected={'act':'config','SSIDIndex':str((index-1)%16+1),
                      'Enable':1,'SSID':'synthetic-new','ENCRYPT':raw,'PWD':value['PWD'],
                      'SSIDAdvertisementEnabled':'true','Standard':'bgn'}
            assert actual=={'flag':1,'mac':stock.peer.hex().upper(),'content':expected},actual
            cases.append({'ui_encryption':code,'wire_encryption':raw,'ui_index':index,
                          'wire_index':int(expected['SSIDIndex'])})
    # The old manager truncates explicit SSID input to31 bytes but a preexisting
    # 32-byte SSID survives an omitted SSID. Native must never silently truncate.
    actual=stock.write(dict(value,SSID='X'*32))
    assert actual['content']['SSID']=='X'*31
    actual=stock.write({'SSIDIndex':'17'})
    assert actual['content']['PWD']==actual['content']['Standard']==actual['content']['SSIDAdvertisementEnabled']==''
    assert actual['content']['SSID']=='synthetic-name' and actual['content']['ENCRYPT']==4
    stock.row[219:252]=b'S'*32+b'\0'
    assert stock.write({'SSIDIndex':'17'})['content']['SSID']=='S'*32
    assert stock.read_local()['List'][0]['SSID']=='S'*32
    return {'manager_sha256':hashlib.sha256(Path(manager).read_bytes()).hexdigest(),
            'mib_sha256':hashlib.sha256(Path(mib).read_bytes()).hexdigest(),
            'cases':cases,'read_fields':list(read['List'][0]),
            'read_entry_example':read['List'][0],
            'findings':['remote write content.act=config uses device-local SSIDIndex string1..16',
                        'write ENCRYPT uses raw MIB code, while local read ENCRYPT uses UI code string',
                        'local read returns16 entries and omits PWD entirely',
                        'explicit SSID input truncated to31 bytes; existing32-byte SSID survives read and omitted-field write',
                        'omitted PWD/Standard/advertisement become empty strings in original; do not copy'],
            'scope':'Actual fttrManage local Wi-Fi producer and remote write builder plus libmib enum conversion. Synthetic MIB rows, modeled JSON-C and captured sync_syncserv_setting; no slave apply or slave read producer.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manager',type=Path,required=True)
    p.add_argument('--mib',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=verify(args.manager,args.mib)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
