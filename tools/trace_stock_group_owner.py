#!/usr/bin/env python3
"""Execute original per-self-MACID GTK calls and port/MBID HIQ commands."""
import argparse,json,struct,sys,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
setup=(root/'tools/trace_stock_extended_keys.py').read_text().split('rows=[]',1)[0]
n={'__file__':str(root/'tools/trace_stock_extended_keys.py')};saved=sys.argv;sys.argv=['test','--module',str(a.module),'--output',str(a.output)];exec(compile(setup,n['__file__'],'exec'),n);sys.argv=saved
o=n['o'];rows=[]
for macid in (0,7,41,127):
 o.uc.mem_write(n['sta']+28,struct.pack('<H',macid))
 for cipher in (6,7,8,9):
  for keyid in range(4):
   n['events']=[];assert not o.call('rtw_hal_set_key',n['adapter'],n['sta'],cipher,int(cipher in (7,9)),0,keyid,1,n['data'])
   events=n['events'];assert len(events)==(2 if cipher in (7,9) else 1),(cipher,events)
   assert all(e['macid']==macid and e['keyid']==keyid and e['kind']==1 for e in events)
   rows.append(dict(macid=macid,cipher=cipher,keyid=keyid,hal_calls=events))
setup=(root/'tools/trace_stock_ap_port.py').read_text().split('programs = []',1)[0]
n={'__file__':str(root/'tools/trace_stock_ap_port.py')};saved=sys.argv;sys.argv=['test','--module',str(a.module)];exec(compile(setup,n['__file__'],'exec'),n);sys.argv=saved
o=n['oracle'];s=n['state'];drops=[]
for chip in ('8852c','8192xb'):
 o.word(n['adapter']+88,o.symbols['mac'+chip+'_hw_info'][0])
 for port in range(5):
  for mbid in range(8 if not port else 1):
   s.clear();s.update(registers={},operations=[],time_us=0,phase='group_drop')
   data=bytearray(8);data[0]=7 if port else 8;data[6]=port;data[7]=mbid;o.uc.mem_write(n['params'],bytes(data))
   assert not o.call('h2c_pkt_drop',n['adapter'],n['params'],0,0)
   event=next(e for e in s['operations'] if e['op']=='H2C');wire=bytes.fromhex(event['payload'])
   assert wire==struct.pack('<6I',(7 if port else 8)|port<<24,mbid,0,0,0,0)
   assert not event['receive_ACK'] and not event['done_ACK']
   drops.append(dict(chip=chip,port=port,mbid=mbid,wire=wire.hex()))
a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(a.module.read_bytes()).hexdigest(),keys=rows,drops=drops,
 scope='Actual HAL key ownership and HIQ payload encoders, captured firmware calls. No physical key installation or queue retirement.'),indent=2)+'\n')
print(f'Original group ownership: {len(rows)} GTK owner calls and {len(drops)} HIQ port/MBID encodings passed')
