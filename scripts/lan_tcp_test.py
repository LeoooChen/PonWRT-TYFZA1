#!/usr/bin/env python3
"""Single-connection TCP transfer for a direct TYFZA1 LAN validation."""
import argparse,socket,time,hashlib,json
from pathlib import Path
p=Path(__file__).resolve().parents[1]
a=argparse.ArgumentParser(); a.add_argument('mode',choices=('receive','send'));a.add_argument('--label',default='lan');a.add_argument('--file',type=Path,default=p/'artifacts/lan-tcp/payload.bin');args=a.parse_args()
f=args.file;f.parent.mkdir(parents=True,exist_ok=True)
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('192.168.1.170',18080));s.listen(1)
print('Listening',args.mode,flush=True)
conn,peer=s.accept();start=time.monotonic();size=0
with conn:
 if args.mode=='receive':
  with f.open('wb') as stream:
   while True:
    data=conn.recv(65536)
    if not data:break
    stream.write(data);size+=len(data)
 else:
  data=f.read_bytes();conn.sendall(data);size=len(data)
record={'mode':args.mode,'peer':peer,'bytes':size,'seconds':round(time.monotonic()-start,3),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
(p/('logs/'+args.label+'-tcp-'+args.mode+'.json')).write_text(json.dumps(record,indent=2)+'\n')
print(record,flush=True)
