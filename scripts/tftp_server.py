#!/usr/bin/env python3
"""Read-only RFC1350 TFTP server for TYFZA1 RAM boot; uses 512-byte blocks."""
import argparse, socket, struct, time, json, datetime
from pathlib import Path

a=argparse.ArgumentParser(description=__doc__)
a.add_argument('--bind', required=True)
a.add_argument('--root',type=Path,required=True)
a.add_argument('--port',type=int,default=69)
args=a.parse_args()
root=args.root.resolve()
logs=Path(__file__).resolve().parents[1]/'logs'
log=(logs/'tftp-transfers.jsonl').open('a',buffering=1)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind((args.bind,args.port))
print('TFTP listening on',s.getsockname(),'root',root,flush=True)
while True:
    request,peer=s.recvfrom(2048)
    if request[:2]!=b'\x00\x01': continue
    fields=request[2:].split(b'\0')
    name=fields[0].decode('ascii')
    file=root/name
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as transfer:
        transfer.bind((args.bind,0)); transfer.settimeout(1)
        if file.parent!=root or not file.is_file():
            transfer.sendto(b'\x00\x05\x00\x01File not found\0',peer); continue
        print('Sending',name,'to',peer,flush=True)
        sent=0; block=1; start=time.monotonic()
        try:
            with file.open('rb') as stream:
                while True:
                    chunk=stream.read(512)
                    packet=struct.pack('!HH',3,block)+chunk
                    for retry in range(8):
                        transfer.sendto(packet,peer)
                        try:
                            ack,source=transfer.recvfrom(2048)
                            if source==peer and ack==struct.pack('!HH',4,block): break
                        except socket.timeout: pass
                    else: raise TimeoutError('ACK missing for block '+str(block))
                    sent+=len(chunk)
                    if len(chunk)<512: break
                    block=(block+1)&65535
            status='complete'
        except (OSError,TimeoutError) as error:
            status=str(error)
        record={'time':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'file':name,'peer':peer,'bytes':sent,'seconds':round(time.monotonic()-start,3),'status':status}
        log.write(json.dumps(record)+'\n'); print(record,flush=True)
