#!/usr/bin/env python3
"""Verify original WAN notifications through native CLI and isolated veth."""
import argparse, errno, json, select, socket, subprocess, tempfile, time
from pathlib import Path
from trace_stock_fttr_wan import verify
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
for name in ('controller','output'):p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();original=verify(a.controller)
assert {v['ifname'] for v in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
source=bytes.fromhex('020304050607');target=bytes.fromhex('0a0b0c0d0e0f');cases=[]
with tempfile.TemporaryDirectory() as directory:
 d=Path(directory);binary=d/'fttr-sync';src=root/'port/packages/tyfza1-fttr/src'
 subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*[str(src/n) for n in
  ('fttr-sync.c','sync-client.c','sync-controller.c','sync-store.c','sync-wire.c','sync-peer.c','sync-wifi.c','sync-json.c','sync-register.c','omci-socket.c','omci-wire.c')],'-ljson-c','-o',str(binary)],check=True)
 subprocess.run(['ip','link','add','host','type','veth','peer','name','peer'],check=True)
 for dev,mac in (('host',source),('peer',target)):
  subprocess.run(['ip','link','set',dev,'address',mac.hex(':'),'up'],check=True)
 peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa));peer.bind(('peer',0))
 variants=[('address',v['address'],v) for v in original['cases']]
 variants += [(v,'192.0.2.1',original['cases'][0]) for v in ('auto','method-ack','fragment','nack','timeout','reregister','wrong-source','wrong-method','bad-result','bad-json','no-result')]
 variants += [('invalid',v,None) for v in ('','hostname','192.0.2.256','1.2.3','01.2.3.4','2001:db8::1/64','fe80::1%eth0','192.0.2.1"','x'*256)]
 def send(value,mac=target,fragment=False):
  text=value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
  data=len(text).to_bytes(4,'big')+text;prefix=source+mac+b'\x88\xaa\xf7'
  if fragment:
   n=len(data)//2;peer.send(prefix+b'\x25'+data[n:]);peer.send(prefix+b'\x21'+data[:n])
  else:peer.send(prefix+b'\0'+data)
 for name,address,expected in variants:
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  proc=subprocess.Popen([str(binary),'host',target.hex(':'),'auto' if name=='auto' else '5','0x88aa','wan-ip',address],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  writes=0;deadline=time.monotonic()+6
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,(name,address)
    if not select.select([peer],[],[],.02)[0]:continue
    frame,meta=peer.recvfrom(16384)
    if meta[2]==socket.PACKET_OUTGOING:continue
    if frame[14]&15==1:
     assert name=='auto';peer.send(source+target+b'\x88\xaa\x01\0');continue
    writes+=1;assert writes==1 and expected
    assert frame[:12]==target+source
    assert json.loads(frame[20:20+int.from_bytes(frame[16:20],'big')])==expected['request']
    if name!='auto':assert frame==bytes.fromhex(expected['frame'])
    if name in ('timeout','no-result'):
     if name=='no-result':send({'RPCMethod':'WanIPChange'})
     continue
    if name=='reregister':send({'RPCMethod':'Register','SN':'replacement'});continue
    if name=='bad-result':send({'Result':'1'});continue
    if name=='bad-json':send(b'{bad');continue
    if name=='wrong-source':send({'Result':0},mac=bytes.fromhex('020000000099'))
    if name=='wrong-method':send({'RPCMethod':'SetONURadioInfo','Result':0})
    reply={'Result':0 if name=='nack' else 1}
    if name=='method-ack':reply['RPCMethod']='WanIPChange'
    send(reply,fragment=name=='fragment')
   out,err=proc.communicate(timeout=1)
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
  error={'invalid':-errno.EINVAL,'nack':-errno.EREMOTEIO,'timeout':-errno.ETIMEDOUT,'no-result':-errno.ETIMEDOUT,'reregister':-errno.ESTALE,'bad-result':-errno.EBADMSG,'bad-json':-errno.EBADMSG}.get(name,0)
  assert proc.returncode==bool(error),(name,out,err)
  if name=='invalid':assert not writes and not out
  else:
   result=json.loads(out);assert result['error']==error and result['ack_seen']==(error==0) and not result['configuration_verified']
   assert writes==1
  cases.append(dict(name=name,address=address,result=error))
 peer.close()
a.output.write_text(json.dumps(dict(cases=cases,scope='Actual native CLI/veth, source/method isolation and original envelopes. Routing effects not modeled or verified.'),indent=2)+'\n')
print(f'WAN notification: {len(cases)} original-envelope, address, lifetime and error cases passed')
