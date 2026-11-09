#!/usr/bin/env python3
"""Compare production native CAM allocation with target multi-BSSID transitions."""
import argparse,ctypes as C,json,subprocess,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,required=True);a=p.parse_args()
original=json.loads(a.original.read_text());driver=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef uint8_t u8;
'''+ '#include "'+str(driver/'cam-owner.h')+'"\n'+r'''
static struct rtl_ax_cam_owners owners;
void reset(void){memset(&owners,0,sizeof(owners));}
int prepare(u8 *cam){return rtl_ax_cam_prepare(&owners,cam);}
void complete(const u8 *cam){rtl_ax_cam_complete(&owners,cam);}
void faults(void){
 u8 cam[60]={},saved[60];reset();
 /* An initial uncertain AP CAM has no acknowledged role_cam snapshot. */
 owners.role[0].reserved=true;owners.bssid[0].references=1;owners.bssid[0].address[0]=2;
 assert(!prepare(cam)&&cam[4]==0&&cam[6]==32&&cam[48]==0&&cam[50]==8&&!(cam[52]&1));
 complete(cam);assert(!owners.role[0].reserved);reset();
 /* MACID and CAM index differ, reserve before any possibly accepted H2C. */
 cam[8]=7;cam[32]=99;cam[52]=0xfd;cam[54]=2;cam[59]=1;
 assert(!prepare(cam)&&cam[4]==0&&cam[48]==0);memcpy(saved,cam,60);
 for(unsigned retry=0;retry<8;retry++)assert(!prepare(cam)&&cam[4]==0&&owners.bssid[0].references==1);
 cam[32]=41;assert(!prepare(cam)&&cam[4]==1&&cam[48]==0&&owners.bssid[0].references==2);
 /* Cannot change another peer's shared BSSID on an update. */
 cam[59]=2;assert(prepare(cam)==-EBUSY&&owners.bssid[0].references==2);cam[59]=1;
 cam[8]&=~1;assert(!prepare(cam)&&cam[52]&1);complete(cam);
 assert(!owners.role[41].reserved&&owners.bssid[0].references==1);
 /* Last-owner deletion keeps old indices across timeout and newly freed slots. */
 memcpy(cam,saved,60);cam[8]&=~1;assert(!prepare(cam)&&!(cam[52]&1));
 assert(owners.role[99].reserved&&owners.bssid[0].references==1);
 cam[32]=41;cam[8]=7;cam[59]=2;assert(!prepare(cam)&&cam[4]==1&&cam[48]==1);
 memcpy(cam,saved,60);cam[8]&=~1;assert(!prepare(cam)&&cam[4]==0&&cam[48]==0&&!(cam[52]&1));complete(cam);
 assert(!owners.role[99].reserved&&owners.bssid[1].references==1);
 assert(prepare(cam)==-ENOENT);complete(cam);assert(owners.bssid[1].references==1);
 /* A new same-BSSID owner can arrive after an uncertain last clear. The
  * retry must restore valid1 and retain the new owner's reference. */
 reset();memset(cam,0,60);cam[8]=7;cam[32]=41;cam[54]=2;cam[59]=1;assert(!prepare(cam));
 memcpy(saved,cam,60);saved[8]&=~1;assert(!prepare(saved)&&!(saved[52]&1));
 cam[32]=99;assert(!prepare(cam)&&cam[52]&1&&owners.bssid[0].references==2);
 assert(!prepare(saved)&&saved[52]&1);complete(saved);
 assert(owners.role[99].reserved&&owners.bssid[0].references==1);
 /* Single-owner STA reassociation uses its slot; sharing still checks BSSID. */
 cam[32]=99;cam[8]=7;cam[59]=3;assert(!prepare(cam)&&cam[48]==0&&owners.bssid[0].address[5]==3);
 reset();
 for(unsigned i=0;i<128;i++){
  memset(cam,0,60);cam[8]=7;cam[32]=127-i;cam[54]=2;cam[59]=1;
  assert(!prepare(cam)&&cam[4]==i&&cam[48]==0);
 }
 assert(owners.bssid[0].references==128);
 for(unsigned i=0;i<128;i++){
  memset(cam,0,60);cam[32]=i;assert(!prepare(cam)&&cam[4]==127-i&&bool(cam[52]&1)==(i<127));complete(cam);
 }
 assert(!owners.bssid[0].references);reset();
 for(unsigned i=0;i<20;i++){
  memset(cam,0,60);cam[8]=7;cam[32]=i;cam[54]=2;cam[59]=i;
  assert(!prepare(cam)&&cam[4]==i&&cam[48]==i);
 }
 cam[32]=20;cam[59]=20;assert(prepare(cam)==-ENOSPC&&!owners.role[20].reserved);
 cam[8]=0;cam[32]=7;assert(!prepare(cam)&&cam[4]==7&&cam[48]==7);complete(cam);
 cam[8]=7;cam[32]=20;cam[59]=20;assert(!prepare(cam)&&cam[4]==7&&cam[48]==7);
 puts("Native CAM owners: retry/quarantine, reassociation, 128 address slots, 20 BSSID slots and exact last-reference cleanup passed");
}
'''.replace('bool(cam[52]&1)','!!(cam[52]&1)')
with tempfile.TemporaryDirectory() as temp:
 c=Path(temp)/'test.c';so=Path(temp)/'test.so';c.write_text(code)
 subprocess.run(['cc','-shared','-fPIC','-O2','-Wall','-Wextra','-Werror',str(c),'-o',str(so)],check=True)
 (Path(temp)/'sanitized.c').write_text(code+'\nint main(void){faults();return 0;}\n')
 subprocess.run(['cc','-O1','-g','-fsanitize=address,undefined',str(Path(temp)/'sanitized.c'),'-o',str(Path(temp)/'sanitized')],check=True)
 subprocess.run([str(Path(temp)/'sanitized')],check=True)
 lib=C.CDLL(str(so));lib.prepare.argtypes=[C.c_void_p];lib.complete.argtypes=[C.c_void_p]
 current=None;owned={};count=0;corrected=0
 for row in original['records']:
  if current!=row['chip']:lib.reset();owned={};current=row['chip']
  expected=bytes.fromhex(row['wire']);wire=bytearray(expected);mid=row['macid']
  if row['operation']!='remove':wire[4]=wire[12]=wire[48]=0
  else:
   wire[4],wire[48]=owned.pop(mid);wire[12]=(wire[12]&~63)|wire[48]
   wire[52]=(wire[52]&~1)|row['shared']
  buf=C.create_string_buffer(bytes(wire),60);assert lib.prepare(buf)==0
  actual=buf.raw
  if row['operation']!='remove':
   assert actual==expected,(row,actual.hex());owned[mid]=(actual[4],actual[48])
  else:
   assert actual==bytes(wire),(row,actual.hex());corrected+=actual!=expected
  lib.complete(buf);count+=1
 assert corrected>0;lib.faults()
print(f'Compared {count} target role transitions; corrected {corrected} original final-BSSID deletion commands')
