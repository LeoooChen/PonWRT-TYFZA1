#!/usr/bin/env python3
"""Execute original age-row updates and cache-invalidate busy/error branches."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ni',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def boundary(name,args):
    if name=='printk':return 0
    raise AssertionError((name,args))
o=ModuleOracle(a.ni,['aal_hash_age_set','aal_hash_cache_invalidate'],boundary)
base=0x6000000;o.uc.mem_map(base,65536);o.word(o.symbols['g_ne_reg_iobase'][0],base)
o.uc.mem_write(o.symbols['ca_aal_debug'][0],bytes(4))
o.uc.mem_write(o.symbols['hash_tbl_table'][0]+16,struct.pack('<I',65536))
# Retain the target binary's32-entry bucket default.
bucket_enum=o.unpack(o.symbols['g_hb_size'][0],'I')[0]
assert o.unpack(o.symbols['hb_width_array'][0]+bucket_enum*8,'Q')[0]==32
regs=[0]* (65536//4);ages=[[0x76543210]*4 for _ in range(2048)]
events=[];pending=None;stuck=0;age_commands=cache_commands=0
def write(u,access,address,size,value,data):
    global pending,age_commands,cache_commands
    reg=address-base;events.append((reg,value));regs[reg//4]=value
    if reg==0x3928:
        age_commands+=1;pending=('age',value)
    if reg==0x38b0:
        cache_commands+=1;pending=('cache',value)
def read(u,access,address,size,value,data):
    global pending
    reg=address-base
    if pending and reg==(0x3928 if pending[0]=='age' else 0x38b0):
        kind,command=pending
        held=stuck==3 if kind=='cache' else stuck==age_commands
        if not held:
            if kind=='age':
                row=command&2047
                for i in range(4):
                    off=0x3938-i*4
                    if command&(1<<30):ages[row][i]=regs[off//4]
                    else:regs[off//4]=ages[row][i];u.mem_write(base+off,struct.pack('<I',regs[off//4]))
            regs[reg//4]=command&~((1<<31) if kind=='age' else 1);pending=None
    u.mem_write(address,struct.pack('<I',regs[reg//4]))
o.uc.hook_add(UC_HOOK_MEM_WRITE,write,begin=base,end=base+65535)
o.uc.hook_add(UC_HOOK_MEM_READ,read,begin=base,end=base+65535)
checks=0
for index in (0,1,7,8,15,16,23,24,31,32,65535):
    for age in (0,1,7):
        stuck=age_commands=0;events.clear();before=list(ages[index>>5])
        assert o.call('aal_hash_age_set',8,index,age)==0
        wanted=list(before);slot=index&31;shift=(slot&7)*4
        wanted[slot>>3]=(wanted[slot>>3]&~(15<<shift))|(age<<shift)
        assert ages[index>>5]==wanted
        assert [reg for reg,value in events]==[0x3928,0x3938-(slot>>3)*4,0x3928]
        checks+=1
for stuck in (1,2):
    pending=None;age_commands=0;events.clear()
    assert o.call('aal_hash_age_set',8,45,0)&0xffffffff==0xfffffff5
    assert len(events)==(1 if stuck==1 else 3)
cache=[]
for busy in (False,True):
    for stuck in (0,3):
        pending=None;cache_commands=0;regs[0x38b0//4]=int(busy);events.clear()
        ret=o.call('aal_hash_cache_invalidate',19,0x1234)&0xffffffff
        assert ret==(0xfffffff5 if busy or stuck else 0)
        assert [x[0] for x in events]==([0x38ac] if busy else [0x38ac,0x38b0])
        assert events[0][1]==19|(0x1234<<5)|(1<<30)
        cache.append(dict(initial_busy=busy,completion_stuck=bool(stuck),result=ret,writes=events.copy()))
a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(a.ni.read_bytes()).hexdigest(),age_cases=checks,age_timeouts=2,cache=cache,
    scope='Actual target instructions; age-row storage and completion timing modeled. Original cache invalidation stages CMD before waiting for idle; native guards before staging. No physical packet retirement proof.'),indent=2)+'\n')
print('Original Hash:33 exact age updates,2 timeouts,4 cache busy/completion paths; original pre-busy CMD write recorded')
