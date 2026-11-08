#!/usr/bin/env python3
"""Exercise native EasyMesh CLI against original frames in an isolated netns."""
import argparse, ast, copy, errno, json, os, select, socket, struct, subprocess, tempfile, time
from pathlib import Path
from trace_stock_fttr_easymesh import verify
from trace_stock_fttr_wifi_fields import WifiFieldsOriginal
from trace_stock_fttr_info import verify as verify_info

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
for name in ('manager','mib','controller','output'):p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args()
assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
original=verify(a.manager,a.mib,a.controller)
original_info=verify_info(a.controller,a.manager,a.mib)
wifi_rows=WifiFieldsOriginal(a.manager,a.mib).read_local()['List']
src=root/'port/packages/tyfza1-fttr/src'
host=bytes.fromhex('020304050607');target=bytes.fromhex('0a0b0c0d0e0f')
group=bytes.fromhex('0110ec000002');master=bytes.fromhex('0110ec000001')
cases=[]
with tempfile.TemporaryDirectory() as directory:
 d=Path(directory);binary=d/'fttr-sync'
 subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*[str(src/n) for n in
  ('fttr-sync.c','sync-client.c','sync-controller.c','sync-store.c','sync-wire.c','sync-peer.c','sync-wifi.c','sync-json.c','sync-register.c','omci-socket.c','omci-wire.c')],'-ljson-c','-o',str(binary)],check=True)
 subprocess.run(['ip','link','add','fttr-test','type','veth','peer','name','fttr-peer'],check=True)
 for dev,mac in (('fttr-test',host),('fttr-peer',target)):
  subprocess.run(['ip','link','set',dev,'address',':'.join(f'{b:02x}' for b in mac),'up'],check=True)
 peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa));peer.bind(('fttr-peer',0))
 for case in ('enable','disable','auto','report-first','fragment','wrong-flag','wrong-method','wrong-source',
              'progress','nack','ack-only','register','bad-status','bad-json','bad-value',
              'preflight-missing','preflight-invalid','readback-mismatch','readback-timeout','readback-register'):
  enabled=case!='disable';writes=0;info_reads=0
  proc=subprocess.Popen([str(binary),'fttr-test',':'.join(f'{b:02x}' for b in target),
     'auto' if case=='auto' else '5','0x88aa','easymesh','bad' if case=='bad-value' else str(int(enabled))],
     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  deadline=time.monotonic()+16
  def send(value,source=target,fragment=False):
   text=json.dumps(value,separators=(',',':')).encode() if not isinstance(value,bytes) else value
   body=struct.pack('>I',len(text))+text
   if fragment:
    cut=len(body)//2
    peer.send(master+source+b'\x88\xaa\xf6'+bytes([0x25])+body[cut:])
    peer.send(master+source+b'\x88\xaa\xf6'+bytes([0x21])+body[:cut])
   else:peer.send(master+source+b'\x88\xaa\xf7\0'+body)
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,case
    ready,_,_=select.select([peer],[],[],0.02)
    if not ready:continue
    frame,addr=peer.recvfrom(16384)
    if addr[2]==socket.PACKET_OUTGOING:continue
    if frame[14]&15==1:
     assert case=='auto';peer.send(master+target+b'\x88\xaa\x01\0');continue
    assert frame[:12]==group+host
    length=int.from_bytes(frame[16:20],'big');request=json.loads(frame[20:20+length])
    if request['RPCMethod']=='GetONUInfo':
     info_reads+=1
     if writes and case=='readback-timeout':continue
     if writes and case=='readback-register':send(dict(RPCMethod='Register',SN='replacement'));continue
     info=dict(RPCMethod='GetONUInfo',Result=1,MAC=target.hex().upper(),easymeshCapability=str(int(enabled if writes else not enabled)))
     if case=='preflight-missing':del info['easymeshCapability'];info['deviceStatus']='online'
     if case=='preflight-invalid':info['easymeshCapability']='2'
     if writes and case=='readback-mismatch':info['easymeshCapability']=str(int(not enabled))
     send(info)
     continue
    writes+=1;assert writes==1
    assert request==original['cases'][int(enabled)]['request'],(case,request)
    # Original encoder fixture takes an explicit destination; native uses
    # the established slave group and pins the target in DMAC.
    if case!='auto':assert frame[6:]==bytes.fromhex(original['cases'][int(enabled)]['frame'])[6:]
    report=dict(RPCMethod='SetAllSetting',Flag=2,STATUS=0)
    if case=='nack':send({'Result':0});continue
    if case=='register':send({'RPCMethod':'Register','SN':'replacement'});continue
    if case=='bad-status':send(dict(report,STATUS='0'));continue
    if case=='bad-json':send(b'{broken');continue
    if case!='report-first':send({'Result':1})
    if case=='ack-only':continue
    if case=='wrong-flag':send(dict(report,Flag=1))
    if case=='wrong-method':send(dict(report,RPCMethod='GetWifi'))
    if case=='wrong-source':send(report,source=bytes.fromhex('020000000099'))
    if case=='progress':send(dict(report,STATUS=1))
    if case in ('wrong-flag','wrong-method','wrong-source','progress'):
     time.sleep(.08);assert proc.poll() is None,case
    send(report,fragment=case=='fragment')
   out,err=proc.communicate(timeout=1)
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
  if case=='bad-value':assert proc.returncode and not writes and not out;continue
  if case.startswith('preflight-'):
   assert proc.returncode==1 and not writes and not out and info_reads==1
   assert str(-errno.ENODATA if case=='preflight-missing' else -errno.EPROTO) in err
   cases.append(case);continue
  result=json.loads(out)
  expected={'nack':-errno.EREMOTEIO,'ack-only':-errno.ETIMEDOUT,'register':-errno.ESTALE,
            'bad-status':-errno.EBADMSG,'bad-json':-errno.EBADMSG,'readback-mismatch':-errno.EUCLEAN,
            'readback-timeout':-errno.ETIMEDOUT,'readback-register':-errno.ESTALE}.get(case,0)
  assert writes==1 and result['error']==expected,(case,out,err)
  assert result['completion_reported']==(not expected or case.startswith('readback-')) and not result['configuration_verified']
  assert info_reads==(1 if case=='register' else 2)
  assert result['reported_state_matches']==(case!='register' and not case.startswith('readback-'))
  assert proc.returncode==bool(expected)
  assert result['requested_enabled']==enabled
  if case=='report-first':assert not result['ack_seen']
  if case=='ack-only':assert result['ack_seen']
  cases.append(case)
 info_cases=[]
 for case in ('normal','partial','fragment','no-method','wrong-method','wrong-mac','wrong-source','ack-only','register','bad-type','nul','oversized'):
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  proc=subprocess.Popen([str(binary),'fttr-test',target.hex(':'),'5','0x88aa','onu-info'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  deadline=time.monotonic()+5;requests=0
  fields={name:'1' for name in original_info['parsed_fields'] if name!='MAC'}
  fields.update(hdVersion='synthetic-hw',swVersion='synthetic-fw',sysUpTime='1234567890123456789',cpuTemp='-12')
  if case=='partial':fields={'deviceStatus':'online'}
  response=dict(fields,RPCMethod='GetONUInfo',Result=1,MAC=target.hex().upper(),UnknownExtension='not-retained')
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,case
    ready,_,_=select.select([peer],[],[],.02)
    if not ready:continue
    frame,addr=peer.recvfrom(16384)
    if addr[2]==socket.PACKET_OUTGOING:continue
    request=json.loads(frame[20:20+int.from_bytes(frame[16:20],'big')])
    assert request==original_info['request'];requests+=1;assert requests==1
    send({'Result':1})
    if case=='ack-only':continue
    if case=='register':send(dict(RPCMethod='Register',SN='changed'));continue
    if case=='wrong-method':send(dict(response,RPCMethod='GetWifi'))
    if case=='wrong-source':send(response,source=bytes.fromhex('020000000099'))
    if case in ('wrong-method','wrong-source'):time.sleep(.05);assert proc.poll() is None
    if case=='wrong-mac':response['MAC']='020000000099'
    if case=='no-method':del response['RPCMethod']
    if case=='bad-type':response['cpuUsage']={}
    if case=='nul':response['swVersion']='bad\0suffix'
    if case=='oversized':response={'RPCMethod':'GetONUInfo','MAC':target.hex().upper(),'swVersion':'x'*1025}
    send(response,fragment=case=='fragment')
   out,err=proc.communicate(timeout=1)
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
  expected={'wrong-mac':-errno.ESTALE,'register':-errno.ESTALE,'ack-only':-errno.ETIMEDOUT,
            'bad-type':-errno.EBADMSG,'nul':-errno.EBADMSG,'oversized':-errno.EBADMSG}.get(case,0)
  assert requests==1 and proc.returncode==bool(expected),(case,out,err)
  if expected:assert not out and str(expected) in err
  else:
   result=json.loads(out);assert result['event']=='sync_onu_info' and result['reported']==fields
   assert result['peer']==target.hex(':') and not result['identity_authenticated']
  info_cases.append(case)
 resident_cases=[]
 addresses=[target,bytes.fromhex('020000000002')]
 tree=ast.parse((root/'scripts/test_fttr_sync_store.py').read_text())
 faults=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FAULTS' for t in n.targets))
 (d/'fault.c').write_text(faults)
 subprocess.run(['cc','-shared','-fPIC',str(d/'fault.c'),'-ldl','-o',str(d/'fault.so')],check=True)
 def resident(case,state_dir,revision='r1',restart=False):
  config=d/'policy.json'
  policy={'Peers':[{'MAC':mac.hex(':'),'Revision':revision,'EasyMesh':bool(i==0),
      'Registration':{'OUI':'020304','SN':'master','Ready':0 if case=='not-ready' else 1,
                      'ExpectedSN':f'peer-{i}'}} for i,mac in enumerate(addresses)]}
  wifi={'act':'config','SSIDIndex':'1','SSID':'synthetic-name','ENCRYPT':4,
        'Enable':1,'PWD':'synthetic-passphrase','SSIDAdvertisementEnabled':'true','Standard':'11bgn'}
  if case=='wifi-first':
   for entry in policy['Peers']:entry['WiFi']=[wifi]
  if case=='changed-policy':policy['Peers'][0]['EasyMesh']=False
  if case=='bad-mesh-type':policy['Peers'][0]['EasyMesh']=1
  config.write_text(json.dumps(policy));config.chmod(0o600)
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  counts=[0,0];registered=[0,0];wifi_writes=[0,0];wifi_reads=[0,0];info_reads=[0,0]
  with (d/'stdout').open('w+') as stdout,(d/'stderr').open('w+') as stderr:
   env=dict(os.environ)
   if case=='fail-store':env.update(LD_PRELOAD=str(d/'fault.so'),FTTR_TEST_SYNC_FAULT='fail-file')
   proc=subprocess.Popen([str(binary),'fttr-test','serve','0x88aa',str(config),'--state',str(state_dir)],stdout=stdout,stderr=stderr,env=env)
   deadline=time.monotonic()+(9.5 if case=='ack-only' and not restart else 1.5)
   def emit(i,value):send(value,source=addresses[i])
   try:
    while time.monotonic()<deadline and proc.poll() is None:
     ready,_,_=select.select([peer],[],[],.02)
     if not ready:continue
     frame,addr=peer.recvfrom(16384)
     if addr[2]==socket.PACKET_OUTGOING:continue
     if frame[14]&15==1:
      for i,mac in enumerate(addresses):
       peer.send(master+mac+b'\x88\xaa\x01\0')
       if not registered[i]:
        # A report before registration never authorizes a configuration.
        emit(i,dict(RPCMethod='SetAllSetting',Flag=2,STATUS=0))
        emit(i,dict(RPCMethod='Register',SN=f'peer-{i}'))
      continue
     value=json.loads(frame[20:20+int.from_bytes(frame[16:20],'big')])
     if frame[14]&15==7:
      registered[frame[14]>>4]+=1;continue
     if value['RPCMethod']=='GetONUInfo':
      i=frame[14]>>4;info_reads[i]+=1
      info=dict(RPCMethod='GetONUInfo',Result=1,MAC=addresses[i].hex().upper(),easymeshCapability=str(int(i==0)))
      if case=='missing-state' and i==0:del info['easymeshCapability'];info['deviceStatus']='online'
      if case=='mismatch' and i==0 and (counts[i] or restart):info['easymeshCapability']='0'
      emit(i,info)
      continue
     i=addresses.index(bytes.fromhex(value['DMAC']))
     if value['Flag']==1:
      assert case=='wifi-first' and not counts[i]
      if value['content']['act']=='get':
       wifi_reads[i]+=1
       rows=copy.deepcopy(wifi_rows[:2])
       if wifi_writes[i] or restart:
        rows[0].update(SSID=wifi['SSID'],Enable=str(wifi['Enable']),ENCRYPT='4',
                       SSIDAdvertisementEnabled=wifi['SSIDAdvertisementEnabled'],Standard=wifi['Standard'])
       emit(i,dict(RPCMethod='SetAllSetting',Flag=1,STATUS=0,content={'content':{'List':rows}}))
      else:
       assert value['content']==wifi;wifi_writes[i]+=1;emit(i,{'Result':1})
      continue
     assert value['Flag']==2 and value['content']=={'act':'config','EnableEasymesh':str(int(i==0))}
     if case=='wifi-first':assert wifi_reads[i]==2 and wifi_writes[i]==1
     counts[i]+=1;assert counts[i]==1 and registered[i] and not restart
     record=json.loads((state_dir/(addresses[i].hex()+f'-{revision}.json')).read_text())
     assert record['version']==2 and record['attempted']==((1<<16)|(1 if case=='wifi-first' else 0)) and record['easymesh']==bool(i==0)
     emit(i,{'Result':1})
     if case=='reregister' and i==0:
      emit(i,dict(RPCMethod='Register',SN='peer-0'))
     elif case=='replacement' and i==0:
      emit(i,dict(RPCMethod='Register',SN='changed'))
     elif case=='kill-write':
      if counts==[1,1]:proc.kill();break
     elif case=='ack-only' and i==0:continue
     else:
      emit(i,dict(RPCMethod='SetAllSetting',Flag=1,STATUS=0))
      emit(i,dict(RPCMethod='SetAllSetting',Flag=2,STATUS=0))
    if proc.poll() is None:proc.terminate()
    proc.wait(timeout=2)
   finally:
    if proc.poll() is None:proc.kill();proc.wait()
   stdout.seek(0);events=[json.loads(line) for line in stdout]
   stderr.seek(0);errors=stderr.read()
  if case=='fail-store':
   assert proc.returncode==1 and counts==[0,0] and str(-errno.EIO) in errors
   resident_cases.append(case);return
  if case in ('changed-policy','bad-mesh-type'):
   assert proc.returncode==1 and counts==[0,0] and not events
   assert str(-errno.ESTALE if case=='changed-policy' else -errno.EINVAL) in errors
   resident_cases.append(case);return
  if case=='kill-write' and not restart:
   assert counts==[1,1] and proc.returncode==-9
   resident_cases.append(case);return
  assert proc.returncode==0,(case,errors)
  if restart or case=='not-ready':assert counts==[0,0],counts
  elif case=='missing-state':assert counts==[0,1],counts
  else:assert counts==[1,1],counts
  mesh=[e for e in events if e['event']=='sync_resident_easymesh']
  if restart:
   assert len(mesh)==2 and all(e['recovered_read_only'] and not e['completion_reported'] for e in mesh)
   assert sorted(e['result'] for e in mesh)==([-errno.EUCLEAN,0] if case=='mismatch' else [0,0])
  elif case in ('normal','ack-only','wifi-first'):
   expected=[-errno.ETIMEDOUT,0] if case=='ack-only' else [0,0]
   assert sorted(e['result'] for e in mesh)==sorted(expected),(case,events)
  elif case in ('reregister','replacement'):
   assert len(mesh)==1 and mesh[0]['peer']==addresses[1].hex(':')
   assert any(e['event']=='sync_resident_cancel' for e in events)
  elif case in ('mismatch','missing-state'):
   assert sorted(e['result'] for e in mesh)==[-errno.EUCLEAN if case=='mismatch' else -errno.ENODATA,0]
  assert all(not e['configuration_verified'] for e in events)
  resident_cases.append(case+('-restart' if restart else ''))
 for case in ('normal','reregister','replacement','ack-only','not-ready','wifi-first','fail-store','kill-write','bad-mesh-type','mismatch','missing-state'):
  state=d/case;state.mkdir(mode=0o700)
  resident(case,state)
  if case not in ('not-ready','fail-store','bad-mesh-type','missing-state'):resident(case,state,restart=True)
 resident('changed-policy',d/'normal')
 # A new explicit policy revision permits a fresh attempt after registration.
 resident('normal',d/'normal',revision='r2')
 peer.close()
a.output.write_text(json.dumps(dict(cases=cases,info_cases=info_cases,resident_cases=resident_cases,invalid_value_before_network=True,
 scope='Actual native CLI/AF_PACKET/veth and original producer/encoder comparisons. Slave completion messages modeled; no real EasyMesh state/readback.'),indent=2)+'\n')
print(f'EasyMesh: {len(cases)} CLI, {len(info_cases)} GetONUInfo, {len(resident_cases)} resident/persistence cases plus invalid-input rejection passed')
