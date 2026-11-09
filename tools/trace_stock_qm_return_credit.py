#!/usr/bin/env python3
"""Execute target inactive BID credit, FIFO readiness and PUSH encoding."""
import argparse
import json
from pathlib import Path
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE


def verify(module):
    def boundary(name, args):
        assert name == 'printk', (name, args)
        return 0
    o=ModuleOracle(module, ['aal_l3qm_get_inactive_bid_cntr',
        'aal_l3qm_check_cpu_push_ready','aal_l3qm_set_cpu_push_paddr'], boundary)
    base=0x6000000;o.uc.mem_map(base,65536)
    o.word(o.symbols['g_ne_reg_iobase'][0],base)
    o.uc.mem_write(o.symbols['ca_aal_debug'][0],bytes(4))
    reads=[];writes=[]
    o.uc.hook_add(UC_HOOK_MEM_READ,lambda u,k,a,n,v,d:reads.append(a-base),begin=base,end=base+65535)
    o.uc.hook_add(UC_HOOK_MEM_WRITE,lambda u,k,a,n,v,d:writes.append((a-base,v)),begin=base,end=base+65535)
    credits=[]
    for eq in (4,5,6,7):
        for valid in (0,1):
            for count in (0,1,2,63,256,2492,16383):
                word=valid<<31|0x5fff8000|count
                o.uc.mem_write(base+0x6328+eq*4,word.to_bytes(4,'little'));reads.clear()
                result=o.call('aal_l3qm_get_inactive_bid_cntr',eq)
                assert result==(count if valid else 0) and reads==[0x6328+eq*4]
                credits.append(dict(eq=eq,word=word,credit=result))
    pushes=[]
    for cpu,eq in ((0,4),(0,5),(2,7),(3,6)):
        for ready in (0,1):
            o.uc.mem_write(base+0x6368+cpu*8,(ready<<31).to_bytes(4,'little'));reads.clear()
            result=o.call('aal_l3qm_check_cpu_push_ready',cpu,instruction_budget=10000)
            assert result==ready and set(reads)=={0x6368+cpu*8}
        for address in (0x4000000,0x6020080,0xaffe000):
            writes.clear();o.call('aal_l3qm_set_cpu_push_paddr',cpu,eq,address)
            assert writes==[(0x636c+cpu*8,address|eq)],writes
            pushes.append(dict(cpu=cpu,eq=eq,address=address,writes=list(writes)))
    return dict(credits=credits,pushes=pushes,
        scope='Actual target credit/ready/PUSH instructions, modeled register values. Credit requires bit31 and low14 count; FIFO readiness is independent. Does not prove physical acceptance or upstream retirement.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ni',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(verify(a.ni),indent=2)+'\n')
    print('Original QM credit:56 validity/count cases,8 FIFO states,12 PUSH encodings PASS')
