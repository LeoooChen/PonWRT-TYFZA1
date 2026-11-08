#!/usr/bin/env python3
"""Real migration journal fault injection and original write-order observations."""
from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1];src=root/'port/packages/tyfza1-fttr/src'
code=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "move-store.h"
static int fault;
int fsync(int fd){if(fault&&! --fault){errno=EIO;return -1;}return syscall(SYS_fsync,fd);}
static void raw(int d,const unsigned char *p){int fd=openat(d,"move",O_WRONLY|O_TRUNC);assert(write(fd,p,64)==64);close(fd);}
int main(int argc,char **argv){
 assert(argc==2);int d=open(argv[1],O_RDONLY|O_DIRECTORY);assert(d>=0);
 struct fttr_omci_link link={.host_mac={2,3,4,5,6,7},.olt_mac={8,9,10,11,12,13},.ethertype=0x8888};
 struct fttr_move_record r={.request={.version=2,.id=7,.target=11,.action=FTTR_ADMIN_MOVE},.stage=FTTR_MOVE_RELEASE,.service=true},got;
 memcpy(r.request.serial,"TEST0007",8);memcpy(r.request.target_serial,"OLD!0011",8);
 assert(fttr_move_store_load(d,&link,3,&got)==-ENOENT);
 assert(!fttr_move_store_save(d,&link,3,&r));assert(!fttr_move_store_load(d,&link,3,&got));assert(!memcmp(&r,&got,sizeof(r)));
 unsigned char old[64],data[64];int fd=openat(d,"move",O_RDONLY);assert(read(fd,old,64)==64);close(fd);
 for(unsigned i=0;i<64;i++){memcpy(data,old,64);data[i]^=1;raw(d,data);assert(fttr_move_store_load(d,&link,3,&got)==-EBADMSG);}raw(d,old);
 struct fttr_omci_link wrong=link;wrong.host_mac[1]^=1;assert(fttr_move_store_load(d,&wrong,3,&got)==-EXDEV);
 assert(fttr_move_store_load(d,&link,1,&got)==-EXDEV);
 r.stage=FTTR_MOVE_RESERVATION;fault=1;assert(fttr_move_store_save(d,&link,3,&r)==-EIO);
 assert(!fttr_move_store_load(d,&link,3,&got)&&got.stage==FTTR_MOVE_RELEASE);
 fault=2;assert(fttr_move_store_save(d,&link,3,&r)==-EIO);
 assert(!fttr_move_store_load(d,&link,3,&got)&&got.stage==FTTR_MOVE_RESERVATION);
 struct fttr_onu_state source={.downstream=1U<<18},target={0};
 memcpy(source.serial,r.request.serial,8);memcpy(target.serial,r.request.target_serial,8);
 assert(!fttr_move_observe(&r,&source,&target));
 struct fttr_move_record equal=r;memcpy(equal.request.target_serial,r.request.serial,8);
 memcpy(target.serial,r.request.serial,8);assert(!fttr_move_observe(&equal,&source,&target));
 memcpy(target.serial,r.request.target_serial,8);
 for(unsigned state=0;state<4;state++){
  target.downstream=(state&1?1U<<18:0)|(state&2?1U<<16:0);
  assert(fttr_move_observe(&r,&source,&target)==(state?-EBUSY:0));
 }
 target.downstream=0;memcpy(target.serial,source.serial,8);
 assert(fttr_move_observe(&r,&source,&target)==2); /* target written, source not retired */
 source.downstream|=1U<<16;assert(fttr_move_observe(&r,&source,&target)==2);
 /* Original writes vendor and serial halves separately. Neither a half
  * target reservation nor half source clear proves a complete move. */
 memcpy(target.serial,r.request.target_serial,8);memcpy(target.serial,r.request.serial,4);
 assert(fttr_move_observe(&r,&source,&target)==-ESTALE);
 memcpy(target.serial,r.request.serial,8);memset(source.serial,0,4);
 assert(fttr_move_observe(&r,&source,&target)==2);
 memset(source.serial,0,8);assert(fttr_move_observe(&r,&source,&target)==1);
 target.downstream=1U<<18;assert(fttr_move_observe(&r,&source,&target)==1);
 target.downstream|=1U<<16;assert(fttr_move_observe(&r,&source,&target)==-ESTALE);
 target.downstream=0;r.stage=FTTR_MOVE_RELEASE;assert(fttr_move_observe(&r,&source,&target)==-ESTALE);
 assert(!fchmodat(d,"move",0644,0));assert(fttr_move_store_load(d,&link,3,&got)==-EACCES);
 assert(!unlinkat(d,"move",0));assert(!symlinkat("/dev/null",d,"move"));assert(fttr_move_store_load(d,&link,3,&got)==-ELOOP);
 assert(!fttr_move_store_remove(d));assert(fttr_move_store_load(d,&link,3,&got)==-ENOENT);
 r.request.target=r.request.id;assert(fttr_move_store_save(d,&link,3,&r)==-EINVAL);
 close(d);puts("Migration journal: 64 corruptions, fsync stage transitions, identity and partial/complete original write ordering passed");
}
'''
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-I'+str(src),str(p/'test.c'),str(src/'move-store.c'),str(src/'omci-wire.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test'),folder],check=True,env=dict(os.environ,UBSAN_OPTIONS='halt_on_error=1'))
