#!/usr/bin/env python3
"""Execute PE exception ordering and RXBD ownership across tag/ring wrap."""
from pathlib import Path
import sys,struct,json
root=Path(__file__).resolve().parents[1]
if len(sys.argv)==1:
 sys.argv.append(str(root/'artifacts/live-radio-rxhandoff59/pe-tx.bin'))
fixture=(root/'scripts/test_pe_ni_radio_firmware.py').read_text().split('for batch in range(64):')[0]
exec(compile(fixture,str(root/'scripts/test_pe_ni_radio_firmware.py'),'exec'))
put(mailbox+140*4,mailbox|0xa0000000)
put(mailbox+24*4,0)
rxq=0x06800000;u.mem_map(rxq,256*12288)
put(mailbox+80*4,rxq);put(mailbox+81*4,255);put(mailbox+82*4,8190);put(mailbox+83*4,255)
packets=[]
for slot,tag in [(255,8191),(0,1)]:
 raw=struct.pack('<II',100|0xc000|tag<<16,64|1<<31)+bytes(28)+bytes([slot])*64
 u.mem_write(rxq+slot*12288,raw);packets.append((slot,raw))
put(bar+0x1218,1<<16|255)
run()
assert get(mailbox+94*4)==1 and get(mailbox+81*4)==0
assert get(rxq)==struct.unpack_from('<I',packets[1][1])[0]
run()
assert get(mailbox+94*4)==1 and get(mailbox+81*4)==0
put(base+0xf000+255*4,1<<30);put(mailbox+95*4,1)
run()
assert get(mailbox+94*4)==2 and get(mailbox+81*4)==1
assert get(rxq+255*12288)==0
assert get(bar+0x1218)&1023==0
assert bytes(u.mem_read(rxq,len(packets[1][1])))==packets[1][1]
print(json.dumps({'linked_MIPS':True,'waits_for_ARM_state_publication':True,'next_RXBD_held_until_ARM_ack':True,'RXBD_and_tag_wrap':True,'payload_untouched_while_waiting':True,'hardware_test':False}))
