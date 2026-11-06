#!/usr/bin/env python3
"""Compare actual EPON PVT admission with the retained AAL queue decoder."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ni',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
# Reuse only the established MMIO boundary and production function extraction.
setup=(root/'scripts/test_epon_mcast_lifecycle.py').read_text().split("code+=r'''\nstatic void set",1)[0]
scope={'__file__':str(root/'scripts/test_epon_mcast_lifecycle.py')}
exec(compile(setup,'epon-lifecycle-fixture','exec'),scope)
code=scope['code']+r'''
int snapshot(unsigned port,unsigned queues,const unsigned *words,unsigned valid,unsigned *mask){
 unsigned memory[0xa000/4]={};struct rtl9617c_pon p={.lock=1,.mac=memory};
 struct rtl_epon_paths paths={.active=BIT(port),.count=port+1,.queues=queues};
 unsigned first=port*queues,reg=0x81bc-first/32*4;
 memcpy(pvt_table[port],words,20);memory[reg/4]=valid<<(first%32);
 pvt_failure=0;int ret=rtl_pon_epon_queue_snapshot(&p,&paths);*mask=paths.queue_mask[port];return ret;
}
'''
base=0x6000000
table=[0]*5
def boundary(name,values):
    if name=='aal_pon_mac_mode_get':return 0
    if name in ('ca_spin_lock','ca_spin_unlock'):return 0
    raise AssertionError((name,values))
ni=ModuleOracle(args.ni,['aal_puc_port_voq_map_get','aal_puc_port_voq_map_get.part.0',
                         'aal_puc_voq_map_cfg_set','aal_puc_voq_map_cfg_get'],boundary)
ni.uc.mem_map(base,0x10000)
ni.word(ni.symbols['ca_venus_pon_reg_iobase'][0],base)
def write(uc,access,address,size,value,context):
    if address==base+0x8000:
        assert value&0xc0000000==0x80000000
        for i,word in enumerate(table):uc.mem_write(base+0x8014-i*4,struct.pack('<I',word))
def read(uc,access,address,size,value,context):
    if address==base+0x8000:
        command=struct.unpack('<I',uc.mem_read(address,4))[0]
        uc.mem_write(address,struct.pack('<I',command&0x7fffffff))
ni.uc.hook_add(UC_HOOK_MEM_WRITE,write,begin=base,end=base+65535)
ni.uc.hook_add(UC_HOOK_MEM_READ,read,begin=base,end=base+65535)
def fields(words,cos,value):
    bits=int.from_bytes(struct.pack('<5I',*words),'little')
    bits=(bits&~(511<<(cos*9)))|(value<<(cos*9))
    return list(struct.unpack('<5I',bits.to_bytes(20,'little')))
def decoded(port,cos):
    ni.uc.mem_write(ni.scratch,bytes(16))
    assert ni.call('aal_puc_port_voq_map_get',0,port,cos,ni.scratch)==0
    data=bytes(ni.uc.mem_read(ni.scratch,16))
    return struct.unpack_from('<I',data)[0],data[4]

rng=random.Random(0x8277c);cases=decodes=0
with tempfile.TemporaryDirectory(prefix='epon-pvt-oracle-') as name:
    tmp=Path(name);(tmp/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-shared','-fPIC',
                    '-I'+str(scope['drivers']),str(tmp/'test.c'),'-o',str(tmp/'test.so')],check=True)
    native=ctypes.CDLL(str(tmp/'test.so'))
    for queues in (4,8):
        mode=int(queues==4)
        assert ni.call('aal_puc_voq_map_cfg_set',0,mode)==0
        assert struct.unpack('<I',ni.uc.mem_read(base+0x804c,4))[0]&3==mode
        assert ni.call('aal_puc_voq_map_cfg_get',0,ni.scratch)==0
        assert ni.unpack(ni.scratch,'I')[0]==mode
        # The original API checks the software queue count. Set8 to decode
        # all PVT fields, including unexpected upper entries in4Q mode.
        ni.uc.mem_write(ni.symbols['queue_num_per_llid'][0],bytes([8]))
        for port in range(128//queues):
            normal=[0,0,1<<12,0,0]
            for cos in range(queues):normal=fields(normal,cos,256|port*queues+cos)
            for fault in range(20):
                table=list(normal);valid=(1<<queues)-1;cos=fault%queues
                if fault<8:table=fields(table,cos,port*queues+cos) # PVT disabled
                elif fault<16:table=fields(table,cos,256|((port*queues+cos)^1))
                elif fault==16:table[2]&=~(1<<12)
                elif fault==17:table[2]|=1<<8
                elif fault==18:table[4]|=1<<23
                else:valid=rng.randrange(1<<queues)
                rows=[decoded(port,c) for c in range(8)];decodes+=8
                expected=0;status=0
                if table[2]&(1<<8) or table[4]&(1<<23):status=-95
                elif table[2]&(1<<12):
                    for c,(qid,enabled) in enumerate(rows):
                        if not enabled:continue
                        if c>=queues or qid!=port*queues+c:status=-117;break
                        if valid&(1<<c):expected|=1<<c
                words=(ctypes.c_uint*5)(*table);mask=ctypes.c_uint()
                ret=native.snapshot(port,queues,words,valid,ctypes.byref(mask))
                assert ret==status,(port,queues,fault,ret,status)
                if not ret:assert mask.value==expected,(port,queues,fault,mask.value,expected)
                cases+=1
args.output.write_text(json.dumps(dict(cases=cases,original_queue_decodes=decodes,
    original_sha256=hashlib.sha256(args.ni.read_bytes()).hexdigest(),
    scope='Actual AAL PVT decoder and mode setter/getter compared with actual native queue snapshot. Indirect completion and table contents are modeled. No physical queue delivery or NI/QM COS folding claim.'),indent=2)+'\n')
print(f'EPON PVT: {cases} native admission cases, {decodes} original queue decodes and both original mode controls passed')
