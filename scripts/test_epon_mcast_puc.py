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
from trace_stock_fttr_mcast_hw import replica_recipes
from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ni',type=Path,required=True)
parser.add_argument('--fc',type=Path,required=True)
parser.add_argument('--reference',type=Path,required=True,help='Retained aal-77c/include/aal_puc.h')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
recipes=[replica_recipes(args.fc,args.ni,(7,),pon=(0,cos,0))['cases'][0]
         for cos in range(8)]
preserved_recipe=replica_recipes(args.fc,args.ni,(1,))['cases'][0]['recipe']
reference=args.reference.read_text()
for mode,ldpid,cos in ((8,3,2),(4,4,1)):
    assert f'AAL_PUC_VOQ_MODE_{mode}Q = {int(mode==4)}' in reference
    assert f'VoQID[6:0] = {{HdrA.LDPID[{ldpid}:0], HdrA.CoS[{cos}:0]' in reference
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
# Execute the existing CPU TX and NI encoder. Only the final PUC bit slicing
# is modeled from the cited hardware contract, not inferred from these helpers.
tx_scope={'__file__':str(root/'scripts/test_pon_data_path.py')}
exec(compile((root/'scripts/test_pon_data_path.py').read_text().split("test = r'''",1)[0],
             'pon-data-fixture','exec'),tx_scope)
tx_code=tx_scope['model']+r'''
static int rtl_pon_itu_data_transmit(struct rtl9617c_pon *p,struct sk_buff *skb,struct net_device *d){
 (void)p;(void)skb;(void)d;assert(!"EPON test entered ITU TX");return NETDEV_TX_BUSY;
}
'''+tx_scope['function']('rtl_data_transmit')+r'''
#include "rtl9617c-fe-mcast.h"
unsigned cpu_header(unsigned port,unsigned queues,unsigned cos){
 struct rtl9617c_pon p={.llid_count=128/queues,.mode=RTL_PON_EPON_D10G,
  .registered=~0U,.traffic_enabled=true};
 struct net_device d={.port={.pon=&p,.llid=port},.running=true,.carrier=true};
 unsigned char bytes[64]={};struct sk_buff *skb=packet(bytes,sizeof(bytes));skb->priority=cos;
 assert(rtl_data_transmit(skb,&d)==NETDEV_TX_OK);return last_frame.header_low;
}
int replica(unsigned port,unsigned cos,unsigned preserve,unsigned *words){
 struct rtl_dq_io io={0};struct rtl_fe_mc_replica r={.destination=32+port,
  .epon=1,.queue=preserve?0:cos,.preserve_queue=preserve};
 return rtl_fe_mc_replica_encode(&io,&r,words);
}
'''
base=0x6000000
table=[0]*5
encoded=[]
def boundary(name,values):
    if name=='aal_pon_mac_mode_get':return 0
    if name in ('ca_spin_lock','ca_spin_unlock'):return 0
    if name=='aal_l2_vlan_default_cfg_get':
        ni.uc.mem_write(values[1],bytes(24));return 0
    if name=='generic_entry_add':
        assert values[0]==ni.symbols['l2fe_arb_fib_table'][0]
        encoded.append(list(ni.unpack(values[1],'3I')))
        ni.uc.mem_write(values[2],struct.pack('<I',1));return 0
    raise AssertionError((name,values))
ni=ModuleOracle(args.ni,['aal_puc_port_voq_map_get','aal_puc_port_voq_map_get.part.0',
                         'aal_puc_voq_map_cfg_set','aal_puc_voq_map_cfg_get',
                         '_aal_arb_mc_fib_action_add'],boundary)
ni.word(ni.symbols['l2fe_arb_fib_table'][0]+136,1)
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

rng=random.Random(0x8277c);cases=decodes=cos_cases=0
with tempfile.TemporaryDirectory(prefix='epon-pvt-oracle-') as name:
    tmp=Path(name);(tmp/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-shared','-fPIC',
                    '-I'+str(scope['drivers']),str(tmp/'test.c'),'-o',str(tmp/'test.so')],check=True)
    native=ctypes.CDLL(str(tmp/'test.so'))
    (tmp/'tx.c').write_text(tx_code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-shared','-fPIC',
                    '-I'+str(scope['drivers']),str(tmp/'tx.c'),'-o',str(tmp/'tx.so')],check=True)
    tx=ctypes.CDLL(str(tmp/'tx.so'));tx.cpu_header.restype=ctypes.c_uint
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
            table=list(normal)
            for cos in range(8):
                words=(ctypes.c_uint*3)()
                header=tx.cpu_header(port,queues,cos)
                assert (header>>3&63)==32+port and header&7==cos%queues
                assert tx.replica(port,cos,1,words)==0
                # Use complete FC-produced inputs for fixed PON COS and
                # ordinary COS retention; only adapt the destination byte.
                for preserve in (False,True):
                    recipe=bytearray.fromhex(preserved_recipe if preserve else recipes[cos]['recipe'])
                    recipe[0]=32+port
                    ni.uc.mem_write(ni.scratch+64,bytes(recipe));encoded.clear()
                    assert ni.call('_aal_arb_mc_fib_action_add',ni.scratch+128,ni.scratch+64)==0
                    assert len(encoded)==1
                    assert tx.replica(port,cos,preserve,words)==0
                    assert list(words)==encoded[0],(port,cos,preserve,list(words),encoded)
                    outgoing=cos if words[1]&(1<<30) else words[2]>>5&7
                    destination=words[1]>>24&63
                    # PUC maps Header A fields, independently of whether
                    # NI replaced COS or preserved it for this packet.
                    voq=(destination&(128//queues-1))*queues+(outgoing&(queues-1))
                    cpu_voq=(header>>3&(128//queues-1))*queues+(header&(queues-1))
                    assert voq==cpu_voq==port*queues+cos%queues
                    assert decoded(port,cos%queues)==(voq,1);cos_cases+=1
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
    cos_cases=cos_cases,reference_sha256=hashlib.sha256(args.reference.read_bytes()).hexdigest(),
    fc_sha256=hashlib.sha256(args.fc.read_bytes()).hexdigest(),
    original_sha256=hashlib.sha256(args.ni.read_bytes()).hexdigest(),
    scope='Actual AAL PVT decoder/mode controls/NI replica encoder compared with native snapshot, CPU TX and replica encoder. PUC Header A bit slicing is modeled from the cited reference; indirect completion and table contents are modeled. No physical NI/QM/PUC queue delivery or retirement claim.'),indent=2)+'\n')
print(f'EPON PVT: {cases} admission cases, {decodes} fault decodes, {cos_cases} original/native COS recipes, CPU headers and documented PUC mappings passed')
