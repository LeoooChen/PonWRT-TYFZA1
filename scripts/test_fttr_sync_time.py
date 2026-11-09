#!/usr/bin/env python3
"""Exercise original time requests and native CLI/resident readback over isolated veth."""
import argparse,ast,copy,ctypes as C,errno,json,os,select,socket,subprocess,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/'tools'))
from trace_stock_fttr_time import TimeOriginal
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
stock=TimeOriginal(a.controller);host=stock.mac;target=stock.peer;group=bytes.fromhex('0110ec000002');master=bytes.fromhex('0110ec000001')
config={k:v for k,v in stock.time(True).items() if k not in ('RPCMethod','MAC')}
base=dict(config,TimeEnable=0,TimeZone=0)
src=ROOT/'port/packages/tyfza1-fttr/src'
with tempfile.TemporaryDirectory() as temp:
 work=Path(temp);binary=work/'fttr-sync';files=('fttr-sync','sync-client','sync-controller','sync-store','sync-wire','sync-peer','sync-wifi','sync-json','sync-register','omci-socket','omci-wire')
 subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*[str(src/(n+'.c')) for n in files],'-ljson-c','-o',str(binary)],check=True)
 subprocess.run(['cc','-std=c11','-O1','-Wall','-Wextra','-Werror','-shared','-fPIC',*[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],'-ljson-c','-o',str(work/'client.so')],check=True)
 lib=C.CDLL(str(work/'client.so'));lib.json_tokener_parse.argtypes=[C.c_char_p];lib.json_tokener_parse.restype=C.c_void_p;lib.json_object_put.argtypes=[C.c_void_p];lib.fttr_sync_time_validate.argtypes=[C.c_void_p]
 invalid=[None,[],{},dict(config,TimeEnable=True),dict(config,TimeEnable=2),dict(config,TimeZone=2147483648),dict(config,TimeZone=-2147483649),dict(config,TimeZone='24'),dict(config,NTPServer1='x'*46),dict(config,NTPServer2='a\0b'),dict(config,extra=1)]
 for index,value in enumerate([config,dict(config,TimeZone=-2147483648,NTPServer2=''),dict(config,TimeZone=2147483647,NTPServer1='x'*45),*invalid]):
  obj=lib.json_tokener_parse(json.dumps(value).encode());ret=lib.fttr_sync_time_validate(obj);lib.json_object_put(obj)
  assert ret==(-errno.EINVAL if index>=3 else 0),(value,ret)
 subprocess.run(['ip','link','add','time-host','type','veth','peer','name','time-peer'],check=True)
 for dev,mac in (('time-host',host),('time-peer',target)):subprocess.run(['ip','link','set',dev,'address',mac.hex(':'),'mtu','1982','up'],check=True)
 remote=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x88aa));remote.bind(('time-peer',0x88aa));remote.setblocking(False)
 def send(value,fragment=False,other=False):
  text=json.dumps(value,separators=(',',':')).encode();body=len(text).to_bytes(4,'big')+text;prefix=master+(bytes.fromhex('020000004444') if other else target)+b'\x88\xaa\xf7'
  if not fragment:remote.send(prefix+b'\0'+body)
  else:
   cut=len(body)//2
   for n,part in ((1,body[cut:]),(0,body[:cut])):remote.send(prefix+bytes([32|(n<<2)|1])+part)
 def packets():
  while True:
   try:frame,addr=remote.recvfrom(4096)
   except BlockingIOError:break
   if addr[2]!=socket.PACKET_OUTGOING:yield frame
 def read_reply(state,method=True,fragment=False):
  send(dict(state,Result=1,MAC=target.hex().upper(),**({'RPCMethod':'GetTimeSetting'} if method else {})),fragment)
 cases=[]
 def run(case):
  desired=copy.deepcopy(config);state=copy.deepcopy(base);path=work/'time.json';path.write_text(json.dumps(desired)+'\n');path.chmod(0o600)
  if case=='private-mode':path.chmod(0o644)
  if case=='invalid':path.write_text(json.dumps(dict(desired,TimeEnable=2)))
  list(packets());cmd=[str(binary),'time-host',target.hex(':'),'auto' if case=='auto' else '5','0x88aa','time-settings' if case=='read-only' else 'time-apply']
  if case!='read-only':cmd.append(str(path))
  proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);deadline=time.monotonic()+12;gets=writes=0
  try:
   while proc.poll() is None:
    assert time.monotonic()<deadline,case
    if not select.select([remote],[],[],0.01)[0]:continue
    for frame in packets():
     if frame[14]&15==1:
      assert case=='auto';remote.send(master+target+b'\x88\xaa\x01\0');continue
     query=json.loads(frame[20:]);assert frame[:12]==group+host and frame[14]&15==6
     if query['RPCMethod']=='GetTimeSetting':
      assert query==stock.time();gets+=1
      if case=='preflight-timeout' or case=='ack-only':send({'Result':1});continue
      if case=='readback-timeout' and gets==2:continue
      if case=='wrong-mac':send(dict(state,Result=1,MAC='02000000BEEF'));continue
      if case=='malformed':send(dict(state,Result=1,MAC=target.hex().upper(),TimeZone='24'));continue
      if case=='partial':send(dict(Result=1,MAC=target.hex().upper(),TimeEnable=1));continue
      if case=='unrelated':send(dict(state,Result=1,MAC=target.hex().upper(),RPCMethod='GetWifi'),other=False)
      if case=='foreign-source':send(dict(state,Result=1,MAC=target.hex().upper()),other=True)
      read_reply(state,method=case!='no-method',fragment=case=='fragments')
     else:
      assert query==stock.time(True);writes+=1;assert writes==1
      if case!='mismatch':state=copy.deepcopy(config)
      if case=='registration':send({'RPCMethod':'Register'});continue
      if case=='write-timeout':continue
      send({'Result':0 if case=='nack' else 1,**({'RPCMethod':'SetTimeSetting'} if case!='no-method' else {})})
   out,err=proc.communicate();result=json.loads(out) if out.strip() else None
   good=case in ('ok','no-method','fragments','unrelated','foreign-source','auto','read-only')
   assert (proc.returncode==0)==good,(case,proc.returncode,out,err)
   if case in ('private-mode','invalid','preflight-timeout','ack-only','wrong-mac','malformed','partial'):assert writes==0
   if case=='registration':assert gets==1 and writes==1
   if result and case!='read-only':
    assert result['write_attempted'] and not result['clock_synchronized'] and not result['configuration_verified']
    assert result['readable_fields_match']==(case not in ('mismatch','readback-timeout','registration'))
    if case in ('nack','write-timeout'):assert result['write_result'] and not result['readback_result']
   if case=='read-only':assert result['reported']==base and not result['clock_synchronized']
   cases.append(dict(case=case,gets=gets,writes=writes,exit=proc.returncode))
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
 for case in ('ok','read-only','no-method','fragments','unrelated','foreign-source','auto','private-mode','invalid','preflight-timeout','ack-only','wrong-mac','malformed','partial','mismatch','readback-timeout','write-timeout','nack','registration'):run(case)
 # Resident automatic transaction with durable revision and read-only restart.
 faults=ast.parse((ROOT/'scripts/test_fttr_sync_store.py').read_text())
 fault_code=next(ast.literal_eval(n.value) for n in faults.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FAULTS' for t in n.targets))
 (work/'fault.c').write_text(fault_code);subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(work/'fault.c'),'-ldl','-o',str(work/'fault.so')],check=True)
 store=work/'state';store.mkdir(mode=0o700);policy={'Peers':[{'MAC':target.hex(':'),'Revision':'time-1','Registration':{'OUI':'020304','SN':'synthetic-master','Ready':1,'ExpectedSN':'synthetic-peer'},'Time':config}]};path=work/'policy.json'
 def resident(case,kill_after_write=False):
  path.write_text(json.dumps(policy));path.chmod(0o600);list(packets());state=copy.deepcopy(base if case=='initial' else config)
  env=os.environ.copy()
  if case=='fsync-failure':env.update(LD_PRELOAD=str(work/'fault.so'),FTTR_TEST_SYNC_FAULT='fail-file')
  proc=subprocess.Popen([str(binary),'time-host','serve','0x88aa',str(path),'--state',str(store)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
  end=time.monotonic()+12;registered=False;gets=writes=0;order=[]
  try:
   while proc.poll() is None:
    assert time.monotonic()<end,case
    select.select([remote],[],[],0.01)
    for frame in packets():
     if frame[14]&15==1:
      remote.send(master+target+b'\x88\xaa\x01\0')
      if not registered:send({'RPCMethod':'Register','SN':'synthetic-peer','GPONSN':'ABCD00000001','LOID':'synthetic-loid'});registered=True
      continue
     query=json.loads(frame[20:])
     if frame[14]&15==7 or query.get('RPCMethod')=='Register':continue
     if query['RPCMethod']=='GetTimeSetting':
      if case=='mixed':assert order==['WiFi','Radio']
      gets+=1;read_reply(state)
      if case in ('restart','ready-zero'):end=min(end,time.monotonic()+1)
     elif query['RPCMethod']=='SetTimeSetting':
      writes+=1;records=list(store.glob('*.json'));assert len(records)==1
      record=json.loads(records[0].read_text());assert record['version']==4 and record['attempted']==((1<<19)|(1<<17)|1 if case=='mixed' else 1<<19) and record['time']==config
      state=copy.deepcopy(config)
      if kill_after_write:proc.kill();break
      send({'Result':1})
     elif query['RPCMethod']=='SetAllSetting':
      assert case=='mixed' and not gets and query['Flag']==1
      want=policy['Peers'][0]['WiFi'][0]
      if query['content']['act']=='config':assert query['content']==want;order.append('WiFi');send({'Result':1})
      else:
       row={k:str(v) for k,v in want.items() if k not in ('act','PWD')};row.update(ENCRYPT='1',RadioType='2.4G')
       send({'RPCMethod':'SetAllSetting','Flag':1,'STATUS':0,'content':{'content':{'List':[row]}}})
     elif query['RPCMethod']=='GetWifiRadio':
      assert case=='mixed' and order in (['WiFi'],['WiFi','Radio'])
      row=dict(policy['Peers'][0]['Radios'][0],OnuMAC=target.hex().upper(),AutoChannelEnable='0')
      send({'RPCMethod':'GetWifiRadio','RadioInfo':[row]})
     elif query['RPCMethod']=='SetONURadioInfo':
      assert case=='mixed' and order==['WiFi'];order.append('Radio');send({'Result':1})
    if (case in ('initial','mixed') and gets==2) or (case in ('restart','ready-zero') and gets==1):
     time.sleep(0.05);proc.terminate();break
   out,err=proc.communicate(timeout=3)
   events=[json.loads(x) for x in out.splitlines()]
   if case in ('initial','mixed'):assert writes==1 and gets==2 and any(x['event']=='sync_resident_apply' and x.get('setting')=='Time' and x['readable_fields_match'] for x in events),(out,err)
   if case=='restart':assert writes==0 and gets==1 and any(x['event']=='sync_resident_recovery' and x.get('setting')=='Time' and not x['write_replayed'] for x in events),(out,err)
   if case=='drift':assert proc.returncode!=0 and not gets and not writes
   if case=='crash':assert proc.returncode==-9 and gets==1 and writes==1
   if case=='fsync-failure':assert proc.returncode!=0 and gets==1 and writes==0
   if case=='ready-zero':assert not writes and any(x['event']=='sync_resident_observed' for x in events)
   cases.append(dict(case='resident-'+case,gets=gets,writes=writes))
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
 resident('initial');resident('restart');policy['Peers'][0]['Time']=dict(config,TimeZone=0);resident('drift')
 policy['Peers'][0]['Time']=config
 # Each isolated revision has a separate durable intent scope.
 for file in store.iterdir():
  if file.suffix=='.json':file.unlink()
 resident('crash',kill_after_write=True);resident('restart')
 for file in store.iterdir():
  if file.suffix=='.json':file.unlink()
 resident('fsync-failure')
 for file in store.iterdir():
  if file.name.endswith('.json') or file.name.endswith('.new'):file.unlink()
 policy['Peers'][0]['Registration']['Ready']=0;resident('ready-zero')
 for file in store.iterdir():
  if file.suffix=='.json':file.unlink()
 policy['Peers'][0]['Registration']['Ready']=1
 policy['Peers'][0]['WiFi']=[{'act':'config','SSIDIndex':'1','SSID':'time-test','PWD':'','ENCRYPT':0,'Enable':1,'Standard':'11bgn','SSIDAdvertisementEnabled':'true'}]
 policy['Peers'][0]['Radios']=[{'RadioType':'2.4G','Enable':'1','TransmitPower':'80','Standard':'11bgn','Channel':'6','FrequencyWidth':'20M'}]
 resident('mixed')
 remote.close()
a.output.write_text(json.dumps({'cases':cases,'validation_cases':len(invalid)+3,'scope':'Actual CLI/controller over AF_PACKET/veth; original request builders, synthetic remote reports, no physical peer or NTP synchronization.'},indent=2)+'\n')
print(f'Time settings: {len(cases)} CLI/resident/readback/restart cases and {len(invalid)+3} validation cases PASS')
