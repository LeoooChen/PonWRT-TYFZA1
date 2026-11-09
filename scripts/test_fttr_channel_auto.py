#!/usr/bin/env python3
"""Verify original channel selection through native CLI and isolated veth."""
import argparse, errno, json, select, socket, subprocess, tempfile, time
from pathlib import Path
from trace_stock_fttr_channel_auto import verify
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
for name in ('controller','output'):p.add_argument('--'+name,type=Path,required=True)
p.add_argument('--stage',type=Path);p.add_argument('--openwrt',type=Path)
a=p.parse_args();assert bool(a.stage)==bool(a.openwrt)
original=verify(a.controller)
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
 command=[str(binary)]
 if a.stage:
  loader=next(a.openwrt.glob('staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/lib/libc.so'))
  command=[str(loader),'--library-path',str(a.openwrt/'staging_dir/target-aarch64_cortex-a55_musl/usr/lib')+':'+str(loader.parent),str(a.stage/'usr/sbin/fttr-sync')]
 variants=('normal','auto','method-ack','fragment','nack','timeout','reregister','wrong-source','wrong-method','bad-result','bad-json','no-result','negative','bool-result','nul-method','request-ack','fragment-missing','lease')
 def send(value,mac=target,fragment=False):
  text=value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
  data=len(text).to_bytes(4,'big')+text;prefix=source+mac+b'\x88\xaa\xf7'
  if fragment:
   n=len(data)//2;peer.send(prefix+b'\x25'+data[n:]);peer.send(prefix+b'\x21'+data[:n])
  else:peer.send(prefix+b'\0'+data)
 for name in variants:
  expected=original
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  proc=subprocess.Popen([*command,'host',target.hex(':'),'auto' if name=='auto' else '5','0x88aa','channel-auto'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  writes=0;deadline=time.monotonic()+6
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,name
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
     if name=='no-result':send({'RPCMethod':'ChannelAutoSelect'})
     continue
    if name=='reregister':send({'RPCMethod':'Register','SN':'replacement'});continue
    if name=='lease':
     other=subprocess.run([*command,'host',target.hex(':'),'5','0x88aa','channel-auto'],capture_output=True,text=True);assert other.returncode and not other.stdout
    if name=='negative':send({'Result':-1});continue
    if name=='bool-result':send({'Result':True});continue
    if name=='nul-method':send({'RPCMethod':'ChannelAutoSelect\0other','Result':1});continue
    if name=='fragment-missing':
     text=b'{"Result":1}';data=len(text).to_bytes(4,'big')+text;peer.send(source+target+b'\x88\xaa\xf7\x25'+data[8:]);continue
    if name=='request-ack':peer.send(source+target+b'\x88\xaa\xf6\0\0\0\0\x0c{"Result":1}');continue
    if name=='bad-result':send({'Result':'1'});continue
    if name=='bad-json':send(b'{bad');continue
    if name=='wrong-source':send({'Result':0},mac=bytes.fromhex('020000000099'))
    if name=='wrong-method':send({'RPCMethod':'SetONURadioInfo','Result':0})
    reply={'Result':0 if name=='nack' else 1}
    if name=='method-ack':reply['RPCMethod']='ChannelAutoSelect'
    send(reply,fragment=name=='fragment')
   out,err=proc.communicate(timeout=1)
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
  error={'nack':-errno.EREMOTEIO,'timeout':-errno.ETIMEDOUT,'no-result':-errno.ETIMEDOUT,'reregister':-errno.ESTALE,'bad-result':-errno.EBADMSG,'bad-json':-errno.EBADMSG,'negative':-errno.EREMOTEIO,'bool-result':-errno.EBADMSG,'nul-method':-errno.EBADMSG,'fragment-missing':-errno.ETIMEDOUT}.get(name,0)
  assert proc.returncode==bool(error),(name,out,err)
  result=json.loads(out);assert result['error']==error and result['ack_seen']==(error==0) and not result['channel_verified']
  assert writes==1
  cases.append(dict(name=name,result=error))
 for variant in ('all-ok','all-nack','all-timeout','all-reregister','all-empty','all-extra-arg','all-16'):
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  targets=[bytes.fromhex(x) for x in ('020000000010','020000000020','020000000030')]
  if variant=='all-16':targets=[bytes([2,0,0,0,0,i+1]) for i in range(16)]
  cmd=[*command,'host','all','auto' if variant!='all-extra-arg' else '5','0x88aa','channel-auto']
  proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  counts={v:0 for v in targets};deadline=time.monotonic()+18
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,variant
    if not select.select([peer],[],[],.02)[0]:continue
    frame,meta=peer.recvfrom(16384)
    if meta[2]==socket.PACKET_OUTGOING:continue
    if frame[14]&15==1:
     if variant!='all-empty':
      for mac in targets:peer.send(source+mac+b'\x88\xaa\x01\0')
     continue
    mac=frame[:6];assert mac in counts and json.loads(frame[20:])==original['request']
    counts[mac]+=1;assert counts[mac]==1
    if mac==targets[1] and variant=='all-timeout':continue
    response={'RPCMethod':'Register'} if mac==targets[1] and variant=='all-reregister' else {'Result':0 if mac==targets[1] and variant=='all-nack' else 1}
    send(response,mac=mac)
   out,err=proc.communicate(timeout=1)
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
  assert bool(proc.returncode)==(variant not in ('all-ok','all-16')),(variant,out,err)
  if variant in ('all-empty','all-extra-arg'):assert not out and not any(counts.values())
  else:
   rows=json.loads(out);assert len(rows)==len(targets) and all(v==1 for v in counts.values())
   for i,row in enumerate(rows):
    error=0 if i!=1 else {'all-nack':-errno.EREMOTEIO,'all-timeout':-errno.ETIMEDOUT,'all-reregister':-errno.ESTALE}.get(variant,0)
    assert row['peer']==targets[i].hex(':') and row['slot']==i and row['error']==error
    assert row['ack_seen']==(error==0) and not row['channel_verified']
  cases.append(dict(name=variant,requests=sum(counts.values()),exit=proc.returncode))
 peer.close()
a.output.write_text(json.dumps(dict(cases=cases,scope='Actual native CLI/veth, source/method isolation and original envelopes. Channel/RF effects not modeled or verified.'),indent=2)+'\n')
print(f'Channel trigger: {len(cases)} original-envelope, address, lifetime and error cases passed')
