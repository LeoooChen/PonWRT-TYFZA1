#!/usr/bin/env python3
"""Execute linked MIPS final-publisher PN contention, closure and credit stalls."""
from pathlib import Path
import struct

root = Path(__file__).resolve().parents[1]
fixture = root / 'scripts/test_pe_tx_publication.py'
scope = {'__file__': str(fixture), '__name__': 'pn_publication_fixture'}
exec(compile(fixture.read_text().split('for initial_sequence in (0, 15):',1)[0], str(fixture),'exec'),scope)
Machine, TX, MB, BAR, PAGES, PAYLOAD, REGISTERS = (scope[n] for n in
    ('Machine','TX','TXMB','BAR','PAGES','PAYLOAD','REGISTERS'))


def shared(m, cam, generation, pn):
    address = TX + 0x1e900 + cam * 40
    m.u.mem_write(address, bytes(40))
    m.put(address, generation);m.put(address+20,pn&0xffffffff);m.put(address+28,pn>>32)
    return address


def request(m, slot, ident, cam, generation):
    words = [0]*32
    words[:6] = [ident, 5, 80|1<<24, 4, 1<<13|1<<14|1<<15, cam<<16]
    words[8:10] = [PAYLOAD,80];words[30] = generation
    m.u.mem_write(TX+0x4000+slot*128,struct.pack('<32I',*words))


def pn(m, address):
    bank=m.get(address+16)&1
    return m.get(address+20+bank*4)|(m.get(address+28+bank*4)<<32)


cases=0
for cam in (0,1,63,127):
    for start in (0,65535,0xffffffff,0x123456abcdef,0xffffffffffff):
        m=Machine(paired=False);s=shared(m,cam,123,start)
        m.put(MB+8*4,1);m.put(MB+9*4,0)
        m.put(MB+37*4,254);m.put(BAR+REGISTERS[5],255<<16|254)
        request(m,0,7,cam,123)
        m.run('PE1',18000)
        assert m.get(MB+9*4)==1 and not m.get(MB+194*4) and pn(m,s)==start
        # ARM is inside its software-CCMP allocation; PE must yield.
        m.put(s+4,1);m.put(s+12,0)
        m.put(BAR+REGISTERS[5],1<<16|254)
        events=m.run('PE1',18000)
        assert not [e for e in events if e[0]=='doorbell'] and not m.get(s+8)
        assert not m.get(MB+194*4) and pn(m,s)==start
        m.put(s+4,0);events=m.run('PE1',18000)
        next_pn=(start+1)&0xffffffffffff
        assert pn(m,s)==next_pn and m.get(MB+194*4)==1
        wd=PAGES+5*264*128
        assert m.get(wd+16)>>16==next_pn&65535 and m.get(wd+20)==next_pn>>16
        assert len([e for e in events if e[0]=='doorbell'])==1
        # A request waiting with no TXBD room can be revoked without PN use.
        request(m,1,8,cam,123);m.put(MB+8*4,2)
        m.put(BAR+REGISTERS[5],0<<16|255);m.run('PE1',18000)
        assert m.get(MB+194*4)==1
        m.put(s,0);m.put(MB+3*4,2)
        events=m.run('PE1',18000)
        assert m.get(MB+4*4)==2 and m.get(TX+0x1e400+8*4)==1
        assert pn(m,s)==next_pn and not [e for e in events if e[0]=='doorbell']
        # Same CAM, new key: captured requests cannot acquire the new PN.
        shared(m,cam,124,31);request(m,2,9,cam,123)
        m.put(MB+8*4,3);m.put(MB+3*4,3);m.run('PE1',18000)
        assert m.get(TX+0x1e400+9*4)==1 and pn(m,s)==31
        cases+=1

# Revocation after the publisher's first generation check but before taking
# the peer lock must still reject it and return software WP ownership.
from unicorn import UC_HOOK_MEM_READ
m=Machine(paired=False);s=shared(m,63,123,17)
m.put(MB+8*4,1);m.put(MB+9*4,0);request(m,0,7,63,123)
reads=0
def close(u,*args):
    global reads
    reads+=1
    if reads==2:m.put(s,0)
m.u.hook_add(UC_HOOK_MEM_READ,close,begin=s,end=s+3)
m.run('PE1',18000)
assert m.get(TX+0x1e400+7*4)==1 and pn(m,s)==17 and not m.get(MB+10*4)
print(f'Linked PE GTK PN: {cases+1} credit stalls, ARM contention, PN carry/wrap, close races, CAM generations and software WP releases passed')
