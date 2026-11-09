#!/usr/bin/env python3
"""Original Radio RPCs through actual native CLI/resident AF_PACKET transactions."""
import argparse,ast,copy,ctypes as C,errno,json,os,select,socket,struct,subprocess,tempfile,time
from pathlib import Path
from trace_stock_fttr_radio import verify
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
for name in ('controller','manager','mib','output'):p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();original=verify(a.controller,a.manager,a.mib)
assert {v['ifname'] for v in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
src=root/'port/packages/tyfza1-fttr/src';host=bytes.fromhex('020304050607');target=bytes.fromhex('0a0b0c0d0e0f')
base=original['report']['RadioInfo'];desired={k:v for k,v in original['writes'][0].items() if k!='RPCMethod'}
cases=[]
with tempfile.TemporaryDirectory() as folder:
 d=Path(folder);binary=d/'fttr-sync';config=d/'config.json'
 subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*[str(src/n) for n in
  ('fttr-sync.c','sync-client.c','sync-controller.c','sync-store.c','sync-wire.c','sync-peer.c','sync-wifi.c','sync-json.c','sync-register.c','omci-socket.c','omci-wire.c')],'-ljson-c','-o',str(binary)],check=True)
 subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(src/'sync-wifi.c'),str(src/'sync-json.c'),'-ljson-c','-o',str(d/'radio.so')],check=True)
 lib=C.CDLL(str(d/'radio.so'));lib.json_tokener_parse.argtypes=[C.c_char_p];lib.json_tokener_parse.restype=C.c_void_p
 lib.json_object_put.argtypes=[C.c_void_p];lib.fttr_sync_radio_validate.argtypes=[C.c_void_p]
 lib.fttr_sync_radio_check.argtypes=[C.c_void_p,C.c_void_p,C.c_bool]
 matrix=0
 for enum in original['widths']:
  for power in ('100','80','60','40','20'):
   for channel in ('0','6' if enum['band']==1 else '149'):
    want=dict(desired,RadioType='2.4G' if enum['band']==1 else '5G',Standard='11bgn' if enum['band']==1 else '11ac',
              FrequencyWidth=enum['width'],TransmitPower=power,Channel=channel)
    ptr=lib.json_tokener_parse(json.dumps(want).encode())
    assert lib.fttr_sync_radio_validate(ptr)==(-errno.EINVAL if enum['result']==255 else 0)
    lib.json_object_put(ptr);matrix+=1
 subprocess.run(['ip','link','add','host','type','veth','peer','name','peer'],check=True)
 for dev,mac in (('host',host),('peer',target)):subprocess.run(['ip','link','set',dev,'address',mac.hex(':'),'mtu','1982','up'],check=True)
 peer=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa));peer.bind(('peer',0))
 def send(value,fragment=False,mac=target):
  text=json.dumps(value,separators=(',',':')).encode();body=len(text).to_bytes(4,'big')+text
  prefix=host+mac+b'\x88\xaa\xf7'
  if fragment:
   cut=len(body)//2
   peer.send(prefix+b'\x25'+body[cut:]);peer.send(prefix+b'\x21'+body[:cut])
  else:peer.send(prefix+b'\0'+body)
 for case in ('read','normal','auto-channel','high-band','fragment','reordered-fields','missing-band','duplicate',
              'wrong-onu','bad-type','invalid-width','invalid-power','invalid-channel','invalid-type',
              'timeout','nack','readback-mismatch','auto-mismatch','reregister'):
  want=copy.deepcopy(desired);state=copy.deepcopy(base);gets=writes=0
  if case=='auto-channel':want['Channel']='0'
  if case=='high-band':want={k:v for k,v in original['writes'][1].items() if k!='RPCMethod'}
  if case=='invalid-width':want['FrequencyWidth']='Auto20M40M80M'
  if case=='invalid-power':want['TransmitPower']='99'
  if case=='invalid-channel':want['Channel']='256'
  if case=='invalid-type':want['Enable']=1
  config.write_text(json.dumps(want));config.chmod(0o600)
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  proc=subprocess.Popen([str(binary),'host',target.hex(':'),'5','0x88aa',*(['radio-info'] if case=='read' else ['radio-apply',str(config)])],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  deadline=time.monotonic()+8
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,case
    if not select.select([peer],[],[],.02)[0]:continue
    frame,addr=peer.recvfrom(16384)
    if addr[2]==socket.PACKET_OUTGOING:continue
    assert frame[:12]==target+host
    value=json.loads(frame[20:20+int.from_bytes(frame[16:20],'big')])
    if value['RPCMethod']=='GetWifiRadio':
     gets+=1;assert value==original['query']
     rows=copy.deepcopy(state)
     if case=='missing-band':rows=rows[1:]
     if case=='duplicate':rows=[rows[0],rows[0]]
     if case=='wrong-onu':rows[0]['OnuMAC']='020000000099'
     if case=='bad-type':rows[0]['Enable']=1
     if case=='readback-mismatch' and gets==2:rows[0]['TransmitPower']='20'
     if case=='auto-mismatch' and gets==2:rows[0]['AutoChannelEnable']='1'
     if case=='reordered-fields':rows=[dict(reversed(list(v.items()))) for v in reversed(rows)]
     send(dict(RPCMethod='GetWifiRadio',RadioInfo=rows),fragment=case=='fragment')
    else:
     writes+=1;assert writes==1 and value==dict(RPCMethod='SetONURadioInfo',**want)
     if case=='reregister':send(dict(RPCMethod='Register',SN='new'));continue
     if case!='nack':
      row=next(r for r in state if r['RadioType']==want['RadioType']);row.update(want)
      row['AutoChannelEnable']='1' if want['Channel']=='0' else '0'
      if want['Channel']=='0':row['Channel']='6'
     if case=='timeout':continue
     send({'Result':0 if case=='nack' else 1})
   out,err=proc.communicate(timeout=1)
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
  if case=='read':assert not writes and json.loads(out)['RadioInfo']==base
  elif case.startswith('invalid-') or case in ('missing-band','duplicate','wrong-onu','bad-type'):
   assert proc.returncode==1 and not writes and not out,(case,out,err)
  else:
   result=json.loads(out)
   expected={'timeout':-errno.ETIMEDOUT,'nack':-errno.EREMOTEIO,'readback-mismatch':-errno.EUCLEAN,
             'auto-mismatch':-errno.EUCLEAN,'reregister':-errno.ESTALE}.get(case,0)
   assert proc.returncode==bool(expected) and (result['write_result'] or result['readback_result'])==expected,(case,out,err)
   assert writes==1 and gets==(1 if case=='reregister' else 2)
  cases.append(case)
 # Two frequency policies use separate durable bits; restart only reads them.
 resident=[];store=d/'state';store.mkdir(mode=0o700)
 radios=[desired,{k:v for k,v in original['writes'][1].items() if k!='RPCMethod'}]
 policy={'Peers':[{'MAC':target.hex(':'),'Revision':'r1','Registration':{'OUI':'020304','SN':'host','Ready':1,'ExpectedSN':'peer'},'Radios':radios}]}
 config.write_text(json.dumps(policy));state=copy.deepcopy(base)
 tree=ast.parse((root/'scripts/test_fttr_sync_store.py').read_text())
 faults=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FAULTS' for t in n.targets))
 (d/'fault.c').write_text(faults)
 subprocess.run(['cc','-shared','-fPIC',str(d/'fault.c'),'-ldl','-o',str(d/'fault.so')],check=True)
 for run in range(7):
  while select.select([peer],[],[],0)[0]:peer.recv(16384)
  if run==2:state[1]['Channel']='36'
  policy['Peers'][0]['Revision']='r1' if run<3 else 'r2'
  config.write_text(json.dumps(policy))
  gets=writes=0;registered=False
  with (d/'out').open('w+') as out,(d/'err').open('w+') as err:
   env=dict(os.environ)
   if run==3:env.update(LD_PRELOAD=str(d/'fault.so'),FTTR_TEST_SYNC_FAULT='fail-file')
   proc=subprocess.Popen([str(binary),'host','serve','0x88aa',str(config),'--state',str(store)],stdout=out,stderr=err,env=env)
   deadline=time.monotonic()+1.5
   try:
    while time.monotonic()<deadline and proc.poll() is None:
     if not select.select([peer],[],[],.02)[0]:continue
     frame,addr=peer.recvfrom(16384)
     if addr[2]==socket.PACKET_OUTGOING:continue
     if frame[14]&15==1:
      peer.send(host+target+b'\x88\xaa\x01\0')
      if not registered:send(dict(RPCMethod='Register',SN='peer'))
      continue
     value=json.loads(frame[20:20+int.from_bytes(frame[16:20],'big')])
     if frame[14]&15==7:registered=True;continue
     assert frame[:6]==target
     if value['RPCMethod']=='GetWifiRadio':gets+=1;send(dict(RPCMethod='GetWifiRadio',RadioInfo=state))
     else:
      assert run in (0,4,5);want=radios[1 if run==5 else writes];assert value==dict(RPCMethod='SetONURadioInfo',**want)
      record=json.loads((store/(target.hex()+'-'+policy['Peers'][0]['Revision']+'.json')).read_text());assert record['version']==3 and record['attempted']&(1<<(18 if run==5 else 17+writes))
      writes+=1;next(r for r in state if r['RadioType']==want['RadioType']).update(want)
      if run==4:proc.kill();break
      send({'Result':1})
    if proc.poll() is None:proc.terminate()
    proc.wait(timeout=2)
   finally:
    if proc.poll() is None:proc.kill();proc.wait()
   out.seek(0);events=[json.loads(v) for v in out];err.seek(0);errors=err.read()
  if run==3:
   assert proc.returncode==1 and not writes and gets==1 and str(-errno.EIO) in errors
   resident.append(dict(run=run,reads=gets,writes=writes));continue
  if run==4:
   assert proc.returncode==-9 and writes==1
   resident.append(dict(run=run,reads=gets,writes=writes));continue
  assert proc.returncode==0,errors
  assert writes==(2 if run==0 else 1 if run==5 else 0) and gets==(4 if run==0 else 3 if run==5 else 2),(run,writes,gets)
  applied=[e for e in events if e['event'] in ('sync_resident_apply','sync_resident_recovery')]
  assert len(applied)==2 and applied[-1]['result']==(-errno.EUCLEAN if run==2 else 0),(run,events)
  resident.append(dict(run=run,reads=gets,writes=writes))
 for bad in ('changed-policy','duplicate-band','invalid-radio'):
  changed=copy.deepcopy(policy)
  if bad=='changed-policy':changed['Peers'][0]['Radios'][0]['Channel']='11'
  if bad=='duplicate-band':changed['Peers'][0]['Radios']=[radios[0],radios[0]]
  if bad=='invalid-radio':changed['Peers'][0]['Radios'][0]['Enable']=True
  config.write_text(json.dumps(changed))
  result=subprocess.run([str(binary),'host','serve','0x88aa',str(config),'--state',str(store)],capture_output=True,text=True,timeout=3)
  assert result.returncode==1 and not result.stdout
  assert str(-errno.ESTALE if bad=='changed-policy' else -errno.EINVAL) in result.stderr
 peer.close()
a.output.write_text(json.dumps(dict(cases=cases,resident=resident,enum_cases=matrix,invalid_policies=3,scope='Actual native CLI/resident/veth and original RPC/enum consumers. Remote effects modeled; no physical radio or roaming acceptance.'),indent=2)+'\n')
print(f'Radio RPC: {matrix} enum, {len(cases)} CLI, three invalid-policy cases and seven dual-band durable lifecycle runs passed')
