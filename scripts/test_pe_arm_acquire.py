#!/usr/bin/env python3
"""Run linked PE against a late producer visibility model (not real bus ordering)."""
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
fixture = (root / 'scripts/test_pe_tx_publication.py').read_text()
# Share the real-PE setup but not the publication test's top-level scenarios.
exec(compile(fixture[:fixture.index('for initial_sequence in (0, 15):')],
             str(root / 'scripts/test_pe_tx_publication.py'), 'exec'))

class LateProducer(Machine):
    def __init__(self,initial):
        self.initial=initial
        super().__init__(paired=False)
        self.injected=False
        self.pending=None
        self.read_stale=False
        self.u.hook_add(UC_HOOK_MEM_READ,self.observe_request,begin=TX+0x4000,end=TX+0xc000-1)
        self.put(TXMB+9*4,initial)
        self.put(TXMB+8*4,(initial+1)&255)
        for n,wp in [(0,0),(1,99)]:
            req=[0]*32;req[0]=wp;req[1]=(n<<8)|((n+1)<<16)
            req[2]=(1<<24)|80;req[3]=4;req[8]=PAYLOAD+n*256;req[9]=80
            self.u.mem_write(TX+0x4000+((initial+n)&255)*128,struct.pack('<32I',*req))
        req[0]=1
        self.actual=struct.pack('<32I',*req)
    def write(self,uc,access,address,size,value,unused):
        super().write(uc,access,address,size,value,unused)
        if address==TXMB+9*4 and value==((self.initial+1)&255) and not self.injected:
            # ARM publishes another entry while PE is in this eight-request turn.
            # A newly visible producer does not itself acquire the request words.
            self.injected=True
            self.put(TXMB+8*4,(self.initial+2)&255)
            self.pending=self.actual
    def step(self,uc,address,size,unused):
        if self.get(address&0x1fffffff)==0xf and self.pending:
            self.u.mem_write(TX+0x4000+((self.initial+1)&255)*128,self.pending)
            self.pending=None
        super().step(uc,address,size,unused)
    def observe_request(self,uc,access,address,size,value,unused):
        start=TX+0x4000+((self.initial+1)&255)*128
        if start <= (address&0x1fffffff) < start+128 and self.pending:
            self.read_stale=True
for initial in (0,1,254,255):
    m=LateProducer(initial)
    m.run('PE1',50000)
    ids=[]
    for n in range(2):
        wd=m.get(RING+n*8+4)
        ids.append(m.get(wd+56)&4095)
    print('Late producer at',initial,': emitted WP IDs',ids,'unacquired read',m.read_stale)
    assert ids==[0,1] and not m.read_stale,'PE reads a late ARM request without acquire after the new producer'
