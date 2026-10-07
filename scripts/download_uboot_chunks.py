#!/usr/bin/env python3
"""Download the exact OpenWrt U-Boot source through eight HTTP range requests."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import urllib.request, hashlib, json, re, time, os
P=Path(__file__).resolve().parents[1]
URL='https://git.u-boot-project.org/u-boot/u-boot/-/releases/v2026.07/downloads/u-boot-2026.07.tar.bz2'
SHA='78e8bfc382fe388f9b55aa1daf8c563522a037779b5d4c349d1415e381f1243e'
PROXY='http://host.orb.internal:7897'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':PROXY,'https':PROXY}))
with opener.open(urllib.request.Request(URL,headers={'Range':'bytes=0-0'}),timeout=60) as response:
    total=int(response.headers['Content-Range'].split('/')[-1]); response.read()
D=P/'artifacts/download-uboot'; D.mkdir(exist_ok=True)
step=1024*1024
ranges=[(i,start,min(start+step,total)-1) for i,start in enumerate(range(0,total,step))]
def fetch(row):
    i,start,end=row; f=D/f'{i:04d}.part'
    if f.exists() and f.stat().st_size==end-start+1:return i
    with opener.open(urllib.request.Request(URL,headers={'Range':f'bytes={start}-{end}'}),timeout=90) as response:
        data=response.read()
    if len(data)!=end-start+1:raise ValueError('Unexpected range length')
    f.write_bytes(data); return i
start=time.monotonic()
with ThreadPoolExecutor(max_workers=8) as pool:
    for done,future in enumerate(as_completed([pool.submit(fetch,row) for row in ranges]),1):
        future.result()
        if done%8==0 or done==len(ranges):print(f'{done}/{len(ranges)} chunks',flush=True)
out=D/'u-boot-2026.07.tar.bz2'
with out.open('wb') as stream:
    for i,_,_ in ranges:stream.write((D/f'{i:04d}.part').read_bytes())
actual=hashlib.sha256(out.read_bytes()).hexdigest()
if actual!=SHA:raise ValueError('OpenWrt source hash mismatch')
import shutil
shutil.copyfile(out,P/'sources/openwrt/dl/u-boot-2026.07.tar.bz2')
receipt={'url':URL,'bytes':total,'sha256':actual,'seconds':round(time.monotonic()-start,2),'parallel_connections':8}
(P/'manifests/uboot-download.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(receipt,flush=True)
