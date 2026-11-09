#!/usr/bin/env python3
"""Verify original QosSync through native CLI and isolated veth."""
import argparse, ast, copy, errno, json, os, select, socket, subprocess, tempfile, time
from pathlib import Path
from trace_stock_fttr_qos import verify
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
for name in ('controller','output'):p.add_argument('--'+name,type=Path,required=True)
p.add_argument('--stage',type=Path);p.add_argument('--openwrt',type=Path)
a=p.parse_args();assert bool(a.stage)==bool(a.openwrt);original=verify(a.controller)
assert {v['ifname'] for v in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
source=bytes.fromhex('020304050607');target=bytes.fromhex('0a0b0c0d0e0f');cases=[]
with tempfile.TemporaryDirectory() as directory:
 d=Path(directory);binary=d/'fttr-sync';src=root/'port/packages/tyfza1-fttr/src'
 subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*[str(src/n) for n in
  ('fttr-sync.c','sync-client.c','sync-controller.c','sync-store.c','sync-wire.c','sync-peer.c','sync-wifi.c','sync-json.c','sync-register.c','omci-socket.c','omci-wire.c')],'-ljson-c','-o',str(binary)],check=True)
 command=[str(binary)]
 if a.stage:
  loader=next(a.openwrt.glob('staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/lib/libc.so'))
  libs=a.openwrt/'staging_dir/target-aarch64_cortex-a55_musl/usr/lib'
  command=[str(loader),'--library-path',str(libs)+':'+str(loader.parent),str(a.stage/'usr/sbin/fttr-sync')]
 subprocess.run(['ip','link','add','host','type','veth','peer','name','peer'],check=True)
 for dev,mac in (('host',source),('peer',target)):
  subprocess.run(['ip','link','set',dev,'address',mac.hex(':'),'mtu','1982','up'],check=True)
 peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa));peer.bind(('peer',0))
 variants=[('input',v['input'],v) for v in original['cases']]
 variants += [(v,original['cases'][1]['input'],original['cases'][1]) for v in ('auto','method-ack','fragment','nack','timeout','reregister','wrong-source','wrong-method','bad-result','bad-json','no-result','private-mode')]
 variants += [('invalid',v,None) for v in ({}, {'ahsapd.qos': None}, {'ahsapd.qos': []}, {'ahsapd.qos': 'x'}, {'ahsapd.qos': {}, 'RPCMethod': 'Other'})]
 # Find the actual maximum unfragmented encoded envelope, not input JSON size.
 boundary={'ahsapd.qos': {'synthetic': ''}}
 overhead=len(json.dumps(dict(RPCMethod='QosSync',MAC=source.hex().upper(),**{'ahsapd.qos':{'synthetic':'','SyncFromMaster':'1'}}),separators=(',',':')).encode())
 boundary['ahsapd.qos']['synthetic']='x'*(1976-overhead)
 variants += [('maximum',boundary,None),('oversize',{'ahsapd.qos': {'synthetic': boundary['ahsapd.qos']['synthetic']+'x'}},None)]
 def send(value,mac=target,fragment=False):
  text=value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
  data=len(text).to_bytes(4,'big')+text;prefix=source+mac+b'\x88\xaa\xf7'
  if fragment:
   n=len(data)//2;peer.send(prefix+b'\x25'+data[n:]);peer.send(prefix+b'\x21'+data[:n])
  else:peer.send(prefix+b'\0'+data)
 for name,configuration,expected in variants:
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  path=d/'qos.json';path.write_text(json.dumps(configuration)+'\n');path.chmod(0o644 if name=='private-mode' else 0o600)
  proc=subprocess.Popen([*command,'host',target.hex(':'),'auto' if name=='auto' else '5','0x88aa','qos-sync',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  writes=0;deadline=time.monotonic()+6
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,(name,configuration)
    if not select.select([peer],[],[],.02)[0]:continue
    frame,meta=peer.recvfrom(16384)
    if meta[2]==socket.PACKET_OUTGOING:continue
    if frame[14]&15==1:
     assert name=='auto';peer.send(source+target+b'\x88\xaa\x01\0');continue
    writes+=1;assert writes==1
    assert frame[:12]==target+source
    want=dict(RPCMethod='QosSync',MAC=source.hex().upper(),**{'ahsapd.qos': dict(configuration['ahsapd.qos'],SyncFromMaster='1')})
    assert json.loads(frame[20:])==want
    if name=='maximum':assert len(frame)==1996
    if expected and name!='auto':assert frame==bytes.fromhex(expected['frame'])
    if name in ('timeout','no-result'):
     if name=='no-result':send({'RPCMethod':'QosSync'})
     continue
    if name=='reregister':send({'RPCMethod':'Register','SN':'replacement'});continue
    if name=='bad-result':send({'Result':'1'});continue
    if name=='bad-json':send(b'{bad');continue
    if name=='wrong-source':send({'Result':0},mac=bytes.fromhex('020000000099'))
    if name=='wrong-method':send({'RPCMethod':'SetONURadioInfo','Result':0})
    reply={'Result':0 if name=='nack' else 1}
    if name=='method-ack':reply['RPCMethod']='QosSync'
    send(reply,fragment=name=='fragment')
   out,err=proc.communicate(timeout=1)
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
  error={'invalid':-errno.EINVAL,'oversize':-errno.EMSGSIZE,'private-mode':-errno.EACCES,'nack':-errno.EREMOTEIO,'timeout':-errno.ETIMEDOUT,'no-result':-errno.ETIMEDOUT,'reregister':-errno.ESTALE,'bad-result':-errno.EBADMSG,'bad-json':-errno.EBADMSG}.get(name,0)
  assert proc.returncode==bool(error),(name,out,err)
  if name in ('invalid','oversize','private-mode'):assert not writes and not out
  else:
   result=json.loads(out);assert result['error']==error and result['ack_seen']==(error==0) and not result['configuration_verified'] and not result['scheduler_verified'] and result['values_redacted']
   assert writes==1
  cases.append(dict(name=name,result=error))
 # Durable resident path, no invented readback RPC.
 fault_ast=ast.parse((root/'scripts/test_fttr_sync_store.py').read_text())
 fault_code=next(ast.literal_eval(n.value) for n in fault_ast.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FAULTS' for t in n.targets))
 (d/'fault.c').write_text(fault_code)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(d/'fault.c'),'-ldl','-o',str(d/'fault.so')],check=True)
 store=d/'state';store.mkdir(mode=0o700)
 configuration=original['cases'][2]['input']
 policy={'Peers':[{'MAC':target.hex(':'),'Revision':'qos-1','Registration':{'OUI':'020304','SN':'synthetic-master','Ready':1,'ExpectedSN':'synthetic-peer'},'QoS':configuration}]}
 policy_path=d/'policy.json'
 def clear():
  for file in store.iterdir():file.unlink()
 def resident(case):
  policy_path.write_text(json.dumps(policy));policy_path.chmod(0o600)
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  env=os.environ.copy()
  if case=='fsync':env.update(LD_PRELOAD=str(d/'fault.so'),FTTR_TEST_SYNC_FAULT='fail-file')
  proc=subprocess.Popen([*command,'host','serve','0x88aa',str(policy_path),'--state',str(store)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
  end=time.monotonic()+9;registered=False;writes=0;events=[];order=[];pending=b''
  try:
   while proc.poll() is None:
    assert time.monotonic()<end,case
    ready,_,_=select.select([peer,proc.stdout],[],[],.02)
    stop_now=False
    if proc.stdout in ready:
     pending+=os.read(proc.stdout.fileno(),65536)
     while b'\n' in pending:
      line,pending=pending.split(b'\n',1)
      event=json.loads(line);events.append(event)
      if case=='ready-zero' and event['event']=='sync_resident_registered':end=min(end,time.monotonic()+.4)
      if event['event']=='sync_resident_qos' or (case=='reregister' and event['event']=='sync_resident_cancel') or (case=='wrong-identity' and event['event']=='sync_resident_registration_rejected'):
       if case!='mixed':stop_now=True
      if case=='mixed' and event['event']=='sync_resident_easymesh':stop_now=True
    if stop_now:proc.terminate();break
    if case=='ready-zero' and time.monotonic()>end-.05:proc.terminate();break
    if peer not in ready:continue
    frame,meta=peer.recvfrom(16384)
    if meta[2]==socket.PACKET_OUTGOING:continue
    if frame[14]&15==1:
     peer.send(source+target+b'\x88\xaa\x01\0')
     if not registered:
      send({'RPCMethod':'Register','SN':'wrong' if case=='wrong-identity' else 'synthetic-peer','GPONSN':'ABCD00000001','LOID':'synthetic-loid'});registered=True
     continue
    query=json.loads(frame[20:])
    if frame[14]&15==7:continue
    method=query['RPCMethod']
    if method=='QosSync':
     writes+=1;assert writes==1
     assert frame[:12]==target+source
     assert query==dict(RPCMethod='QosSync',MAC=source.hex().upper(),**{'ahsapd.qos':dict(configuration['ahsapd.qos'],SyncFromMaster='1')})
     records=list(store.glob('*.json'));assert len(records)==1 and records[0].stat().st_mode&0o777==0o600
     record=json.loads(records[0].read_text());assert record['version']==8 and record['qos']==configuration
     assert record['attempted']==((1<<23)|1|(1<<17)|(1<<19)|(1<<20)|(1<<22) if case=='mixed' else 1<<23)
     if case=='mixed':assert order==['WiFi','Radio','Time','IPTV','Password'];order.append('QoS')
     if case=='crash':proc.kill();break
     if case=='timeout':continue
     if case=='reregister':send({'RPCMethod':'Register','SN':'replacement'});continue
     send({'RPCMethod':'QosSync','Result':0 if case=='nack' else 1})
    elif method=='SetAllSetting':
     assert case=='mixed'
     if query['Flag']==2:
      assert order[-1]=='QoS';order.append('Mesh')
      send({'RPCMethod':'SetAllSetting','Flag':2,'STATUS':0,'content':{}})
     else:
      want=policy['Peers'][0]['WiFi'][0]
      if query['content']['act']=='config':order.append('WiFi');send({'Result':1})
      else:
       row={k:str(v) for k,v in want.items() if k not in ('act','PWD')};row.update(ENCRYPT='1',RadioType='2.4G')
       send({'RPCMethod':'SetAllSetting','Flag':1,'STATUS':0,'content':{'content':{'List':[row]}}})
    elif method=='GetONUInfo':
     assert case=='mixed';send({'Result':1,'MAC':target.hex().upper(),'easymeshCapability':'1' if order[-1]=='Mesh' else '0'})
    elif method=='GetWifiRadio':send({'RPCMethod':method,'RadioInfo':[dict(policy['Peers'][0]['Radios'][0],OnuMAC=target.hex().upper(),AutoChannelEnable='0')]})
    elif method=='SetONURadioInfo':order.append('Radio');send({'Result':1})
    elif method.startswith('Get'):
     field={'GetTimeSetting':'Time','GetIPTVSetting':'IPTV','GetWebPassword':'WebPassword'}[method]
     send(dict(policy['Peers'][0][field],Result=1,MAC=target.hex().upper()))
    else:
     order.append({'SetTimeSetting':'Time','SetIPTVSetting':'IPTV','SetWebPassword':'Password'}[method]);send({'Result':1})
   out,err=proc.communicate(timeout=3)
   events.extend(json.loads(line) for line in out.splitlines())
   q=[v for v in events if v['event']=='sync_resident_qos']
   if case in ('initial','mixed','restart','nack','timeout'):
    assert len(q)==1,(case,events,err)
    assert not q[0]['scheduler_verified'] and not q[0]['configuration_verified'] and not q[0]['write_replayed']
    assert q[0]['result']=={'restart':-errno.EINPROGRESS,'nack':-errno.EREMOTEIO,'timeout':-errno.ETIMEDOUT}.get(case,0),(case,q)
    assert q[0]['prior_write_outcome_unknown']==(case=='restart')
    assert q[0]['ack_seen']==(case in ('initial','mixed'))
   assert writes==(case in ('initial','mixed','crash','nack','timeout','reregister')),(case,writes,events,err)
   if case in ('drift','fsync'):assert proc.returncode!=0
   if case=='crash':assert proc.returncode==-9
   if case=='mixed':assert order==['WiFi','Radio','Time','IPTV','Password','QoS','Mesh'],(order,events)
   assert 'ahsapd.qos' not in out+err
   cases.append(dict(name='resident-'+case,writes=writes))
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
 resident('initial');resident('restart')
 policy['Peers'][0]['QoS']={'ahsapd.qos':{'changed':True}};resident('drift');policy['Peers'][0]['QoS']=configuration
 for case in ('crash','nack','timeout'):
  clear();resident(case);resident('restart')
 for case in (('wrong-identity','reregister') if a.stage else ('fsync','wrong-identity','reregister')):
  clear();resident(case)
 clear();policy['Peers'][0]['Registration']['Ready']=0;resident('ready-zero');policy['Peers'][0]['Registration']['Ready']=1
 clear();policy['Peers'][0].update(WiFi=[{'act':'config','SSIDIndex':'1','SSID':'qos-test','PWD':'','ENCRYPT':0,'Enable':1,'Standard':'11bgn','SSIDAdvertisementEnabled':'true'}],Radios=[{'RadioType':'2.4G','Enable':'1','TransmitPower':'80','Standard':'11bgn','Channel':'6','FrequencyWidth':'20M'}],Time={'TimeEnable':1,'TimeZone':24,'NTPServer1':'192.0.2.123','NTPServer2':''},IPTV={'IPTVEnable':0},WebPassword={'WebAdminPassword':'synthetic-only'},EasyMesh=True)
 resident('mixed')
 peer.close()
a.output.write_text(json.dumps(dict(cases=cases,scope='Actual native CLI/resident/veth and durable intents compared with original envelopes. Opaque policy preserved; remote QoS installation and hardware scheduling not verified.'),indent=2)+'\n')
print(f'QosSync: {len(cases)} original-envelope, limit, lifetime, durable restart and failure cases PASS')
