#!/usr/bin/env python3
"""Exercise original time/IPTV requests and native CLI/resident readback over veth."""
import argparse,ast,copy,ctypes as C,errno,json,os,select,socket,subprocess,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/'tools'))
from trace_stock_fttr_time import TimeOriginal
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--controller',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--iptv',action='store_true');a=p.parse_args()
assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link'],text=True))}=={'lo'}
if a.iptv:
 from trace_stock_fttr_iptv import IptvOriginal
 stock=IptvOriginal(a.controller);stock.rows=[stock.row(mask=5,vid=100)]
 original=stock.iptv;setting='IPTV';command='iptv';read_method='GetIPTVSetting';write_method='SetIPTVSetting'
 enabled='IPTVEnable';mismatch='IPTVVlan';verification='traffic_verified';version=5;attempt=1<<20
else:
 stock=TimeOriginal(a.controller);original=stock.time;setting='Time';command='time';read_method='GetTimeSetting';write_method='SetTimeSetting'
 enabled='TimeEnable';mismatch='TimeZone';verification='clock_synchronized';version=4;attempt=1<<19
host=stock.mac;target=stock.peer;group=bytes.fromhex('0110ec000002');master=bytes.fromhex('0110ec000001')
config={k:v for k,v in original(True).items() if k not in ('RPCMethod','MAC')}
base={enabled:0} if a.iptv else dict(config,TimeEnable=0,TimeZone=0)
src=ROOT/'port/packages/tyfza1-fttr/src'
with tempfile.TemporaryDirectory() as temp:
 work=Path(temp);binary=work/'fttr-sync';files=('fttr-sync','sync-client','sync-controller','sync-store','sync-wire','sync-peer','sync-wifi','sync-json','sync-register','omci-socket','omci-wire')
 subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*[str(src/(n+'.c')) for n in files],'-ljson-c','-o',str(binary)],check=True)
 subprocess.run(['cc','-std=c11','-O1','-Wall','-Wextra','-Werror','-shared','-fPIC',*[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],'-ljson-c','-o',str(work/'client.so')],check=True)
 lib=C.CDLL(str(work/'client.so'));lib.json_tokener_parse.argtypes=[C.c_char_p];lib.json_tokener_parse.restype=C.c_void_p;lib.json_object_put.argtypes=[C.c_void_p];validate=lib.fttr_sync_iptv_validate if a.iptv else lib.fttr_sync_time_validate;validate.argtypes=[C.c_void_p]
 if a.iptv:
  invalid=[None,[],{},dict(config,IPTVEnable=True),dict(config,IPTVEnable=2),dict(config,IPTVVlan=-1),dict(config,IPTVVlan=4095),dict(config,IPTVVlan='100'),dict(config,extra=1),dict(config,IPTVEnable=0)]
  invalid += [dict(config,IPTVLanPort=ports) for ports in ('LAN0','LAN5','LAN1,LAN1','LAN2,LAN1','LAN1,','LAN1,,LAN2','LAN1\0LAN2','lan1','LAN01',' LAN1')]
  valid=[config,dict(IPTVEnable=0),dict(config,IPTVVlan=0,IPTVLanPort=''),dict(config,IPTVVlan=4094,IPTVLanPort='LAN1,LAN2,LAN3,LAN4')]
  for mask in range(16):
   stock.rows=[stock.row(mask=mask,vid=100)]
   valid.append({k:v for k,v in original(True).items() if k not in ('RPCMethod','MAC')})
  stock.rows=[stock.row(mask=5,vid=100)]
 else:
  invalid=[None,[],{},dict(config,TimeEnable=True),dict(config,TimeEnable=2),dict(config,TimeZone=2147483648),dict(config,TimeZone=-2147483649),dict(config,TimeZone='24'),dict(config,NTPServer1='x'*46),dict(config,NTPServer2='a\0b'),dict(config,extra=1)]
  valid=[config,dict(config,TimeZone=-2147483648,NTPServer2=''),dict(config,TimeZone=2147483647,NTPServer1='x'*45)]
 for index,value in enumerate(valid+invalid):
  obj=lib.json_tokener_parse(json.dumps(value).encode());ret=validate(obj);lib.json_object_put(obj)
  assert ret==(-errno.EINVAL if index>=len(valid) else 0),(value,ret)
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
  send(dict(state,Result=1,MAC=target.hex().upper(),**({'RPCMethod':read_method} if method else {})),fragment)
 cases=[]
 def run(case):
  desired=copy.deepcopy(config);state=copy.deepcopy(base)
  if case=='disable':desired={enabled:0};state=copy.deepcopy(config);stock.rows=[]
  if case=='untagged':desired=dict(config,IPTVVlan=0,IPTVLanPort='LAN1,LAN2,LAN3,LAN4');stock.rows=[stock.row(mask=15,tagged=False)]
  if case=='empty-ports':desired=dict(config,IPTVLanPort='');stock.rows=[stock.row(mask=16,vid=100)]
  path=work/'time.json';path.write_text(json.dumps(desired)+'\n');path.chmod(0o600)
  if case=='private-mode':path.chmod(0o644)
  if case=='invalid':path.write_text(json.dumps(dict(desired,**{enabled:2})))
  list(packets());cmd=[str(binary),'time-host',target.hex(':'),'auto' if case=='auto' else '5','0x88aa',command+'-settings' if case=='read-only' else command+'-apply']
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
     if query['RPCMethod']==read_method:
      assert query==original();gets+=1
      if case=='preflight-timeout' or case=='ack-only':send({'Result':1});continue
      if case=='readback-timeout' and gets==2:continue
      if case=='wrong-mac':send(dict(state,Result=1,MAC='02000000BEEF'));continue
      if case=='malformed':send(dict(state,Result=1,MAC=target.hex().upper(),**{enabled:'1'}));continue
      if case=='partial':send(dict(Result=1,MAC=target.hex().upper(),**{enabled:1}));continue
      if case=='unrelated':send(dict(state,Result=1,MAC=target.hex().upper(),RPCMethod='GetWifi'),other=False)
      if case=='foreign-source':send(dict(state,Result=1,MAC=target.hex().upper()),other=True)
      read_reply(state,method=case!='no-method',fragment=case=='fragments')
     else:
      assert query==original(True);writes+=1;assert writes==1
      if case!='mismatch':state=copy.deepcopy(desired)
      if case=='registration':send({'RPCMethod':'Register'});continue
      if case=='write-timeout':continue
      send({'Result':0 if case=='nack' else 1,**({'RPCMethod':write_method} if case!='no-method' else {})})
   out,err=proc.communicate();result=json.loads(out) if out.strip() else None
   good=case in ('ok','no-method','fragments','unrelated','foreign-source','auto','read-only','disable','untagged','empty-ports')
   assert (proc.returncode==0)==good,(case,proc.returncode,out,err)
   if case in ('private-mode','invalid','preflight-timeout','ack-only','wrong-mac','malformed','partial'):assert writes==0
   if case=='registration':assert gets==1 and writes==1
   if result and case!='read-only':
    assert result['write_attempted'] and not result[verification] and not result['configuration_verified']
    assert result['readable_fields_match']==(case not in ('mismatch','readback-timeout','registration'))
    if case in ('nack','write-timeout'):assert result['write_result'] and not result['readback_result']
   if case=='read-only':assert result['reported']==base and not result[verification]
   cases.append(dict(case=case,gets=gets,writes=writes,exit=proc.returncode))
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
 for case in ('ok','read-only','no-method','fragments','unrelated','foreign-source','auto','private-mode','invalid','preflight-timeout','ack-only','wrong-mac','malformed','partial','mismatch','readback-timeout','write-timeout','nack','registration'):run(case)
 if a.iptv:
  for case in ('disable','untagged','empty-ports'):run(case)
  stock.rows=[stock.row(mask=5,vid=100)]
 # Resident automatic transaction with durable revision and read-only restart.
 faults=ast.parse((ROOT/'scripts/test_fttr_sync_store.py').read_text())
 fault_code=next(ast.literal_eval(n.value) for n in faults.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FAULTS' for t in n.targets))
 (work/'fault.c').write_text(fault_code);subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(work/'fault.c'),'-ldl','-o',str(work/'fault.so')],check=True)
 store=work/'state';store.mkdir(mode=0o700);policy={'Peers':[{'MAC':target.hex(':'),'Revision':'time-1','Registration':{'OUI':'020304','SN':'synthetic-master','Ready':1,'ExpectedSN':'synthetic-peer'},setting:config}]};path=work/'policy.json'
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
     if query['RPCMethod']==read_method:
      if case=='mixed':assert order==(['WiFi','Radio','Time'] if a.iptv else ['WiFi','Radio'])
      gets+=1;read_reply(state)
      if case in ('restart','ready-zero'):end=min(end,time.monotonic()+1)
     elif query['RPCMethod']==write_method:
      writes+=1;records=list(store.glob('*.json'));assert len(records)==1
      record=json.loads(records[0].read_text());assert record['version']==version and record['attempted']==(attempt|(1<<17)|1|((1<<19) if a.iptv else 0) if case=='mixed' else attempt) and record[setting.lower()]==config
      state=copy.deepcopy(config)
      if kill_after_write:proc.kill();break
      send({'Result':1})
     elif a.iptv and query['RPCMethod']=='GetTimeSetting':
      assert case=='mixed' and order in (['WiFi','Radio'],['WiFi','Radio','Time'])
      send(dict(policy['Peers'][0]['Time'],Result=1,MAC=target.hex().upper()))
     elif a.iptv and query['RPCMethod']=='SetTimeSetting':
      assert case=='mixed' and order==['WiFi','Radio'];order.append('Time');send({'Result':1})
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
   if case in ('initial','mixed'):assert writes==1 and gets==2 and any(x['event']=='sync_resident_apply' and x.get('setting')==setting and x['readable_fields_match'] for x in events),(out,err)
   if case=='restart':assert writes==0 and gets==1 and any(x['event']=='sync_resident_recovery' and x.get('setting')==setting and not x['write_replayed'] for x in events),(out,err)
   if case=='drift':assert proc.returncode!=0 and not gets and not writes
   if case=='crash':assert proc.returncode==-9 and gets==1 and writes==1
   if case=='fsync-failure':assert proc.returncode!=0 and gets==1 and writes==0
   if case=='ready-zero':assert not writes and any(x['event']=='sync_resident_observed' for x in events)
   cases.append(dict(case='resident-'+case,gets=gets,writes=writes))
  finally:
   if proc.poll() is None:proc.kill();proc.communicate()
 resident('initial');resident('restart');policy['Peers'][0][setting]=dict(config,**{mismatch:0});resident('drift')
 policy['Peers'][0][setting]=config
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
 if a.iptv:policy['Peers'][0]['Time']={'TimeEnable':1,'TimeZone':24,'NTPServer1':'192.0.2.123','NTPServer2':''}
 resident('mixed')
 remote.close()
a.output.write_text(json.dumps({'cases':cases,'validation_cases':len(invalid)+len(valid),'scope':'Actual CLI/controller over AF_PACKET/veth; original request builders, synthetic remote reports, no physical peer, time synchronization or IPTV forwarding.'},indent=2)+'\n')
print(f'{setting} settings: {len(cases)} CLI/resident/readback/restart cases and {len(invalid)+len(valid)} validation cases PASS')
