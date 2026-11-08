#!/usr/bin/env python3
"""Fault-test the real durable OMCI ownership codec; recovery is tested by autoprovision."""
from pathlib import Path
import os
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
code=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "service-store.h"
static int fault;
int fsync(int fd){if(fault && !--fault){errno=EIO;return -1;}return syscall(SYS_fsync,fd);}
static void put32(unsigned char *p,unsigned int n){p[0]=n>>24;p[1]=n>>16;p[2]=n>>8;p[3]=n;}
static void raw(int d,const char *name,unsigned char *p,size_t n){
 int fd=openat(d,name,O_WRONLY|O_TRUNC);assert(fd>=0);assert(write(fd,p,n)==(ssize_t)n);close(fd);
}
static void fixture(struct fttr_services *s){
 memset(s,0,sizeof(*s));s->count=1;s->stage=FTTR_SERVICES_READY;s->configured=true;
 struct fttr_service_job *j=s->job;
 j->config.slot=1;j->config.maximum=200;j->config.shared=true;j->config.backend=FTTR_ASIC9690;
 j->gem=fttr_miniolt_gem_id(7,1);j->tcont=0x101;j->queue=0x8001;j->previous_alloc=255;
 j->transaction.count=7;j->transaction.objects=j->objects;j->transaction.phase=FTTR_OBJECT_CONFIGURED;
 for(unsigned int i=0;i<7;i++){
  j->objects[i]=(struct fttr_omci_object){.class_id=268+i,.instance=i+1,.touched=true,
   .create={.mask=0x8000,.size=2,.value={1,2}},.presence=FTTR_OBJECT_VERIFIED};
 }
 j->objects[0].class_id=262;j->objects[0].update_only=j->objects[0].have_previous=true;
 j->objects[0].previous[1]=255;
 j->unis=calloc(1,sizeof(*j->unis));assert(j->unis);j->uni_count=1;
 struct fttr_service_uni *u=j->unis;u->instance=0x101;u->port=1;u->number=1;
 u->transaction.count=1;u->transaction.objects=u->objects;u->transaction.phase=FTTR_OBJECT_CONFIGURED;
 u->objects[0]=j->objects[3];
 u->vlan.plan.count=1;u->vlan.sent=1;u->vlan.verified=true;u->vlan.stage=FTTR_VLAN_DONE;
 u->vlan.before.table=(struct fttr_vlan_table){.capacity=8,.complete=true};
 u->vlan.after.table=(struct fttr_vlan_table){.capacity=8,.length=16,.received=16,.complete=true,.data=calloc(1,16)};
 j->stage=FTTR_SERVICE_READY;j->configured=j->grant_verified=true;
}
int main(int argc,char **argv){
 assert(argc==2);int d=fttr_service_store_open(argv[1]);assert(d>=0);
 assert(fttr_service_store_open(argv[1])==-EWOULDBLOCK);
 struct fttr_omci_link link={.host_mac={2,3,4,5,6,7},.olt_mac={10,11,12,13,14,15},.ethertype=0x8888};
 unsigned char sn[8]={0x54,0x45,0x53,0x54,0,0,0,7};
 struct fttr_services s,got;fixture(&s);struct fttr_service_job before=s.job[0];
 assert(fttr_service_store_load(d,&link,7,sn,3,&got)==-ENOENT);
 assert(!fttr_service_store_save(d,&link,7,sn,3,&s));
 assert(!memcmp(&s.job[0],&before,sizeof(before)) && s.job[0].unis[0].vlan.verified);
 assert(!fttr_service_store_load(d,&link,7,sn,3,&got));
 assert(!fttr_service_store_match(&got,&s));
 assert(got.job[0].objects[0].touched && got.job[0].objects[0].previous[1]==255);
 assert(got.job[0].grant_withdrawn && !got.job[0].configured && !got.job[0].grant_verified);
 assert(got.job[0].unis[0].vlan.sent==1 && got.job[0].unis[0].vlan.before.table.complete);
 fttr_services_clear(&got);
 const char *name="5445535400000007.service";unsigned char data[4096],old[4096];
 int fd=openat(d,name,O_RDONLY);ssize_t n=read(fd,old,sizeof(old));assert(n>36 && n<4096);close(fd);
 for(int i=0;i<n;i++){
  memcpy(data,old,n);data[i]^=1;raw(d,name,data,n);
  assert(fttr_service_store_load(d,&link,7,sn,3,&got)==-EBADMSG);
 }
 /* Bounds still hold after valid CRC; bad array dimensions never reach allocation/indexing. */
 for(unsigned int offset=32;offset<124;offset+=4){
  memcpy(data,old,n);put32(data+offset,UINT32_MAX);put32(data+n-4,fttr_omci_crc(data,n-4));raw(d,name,data,n);
  int result=fttr_service_store_load(d,&link,7,sn,3,&got);assert(result<0);
 }
 raw(d,name,old,n);
 struct fttr_omci_link wrong=link;wrong.host_mac[0]^=1;
 assert(fttr_service_store_load(d,&wrong,7,sn,3,&got)==-EXDEV);
 assert(fttr_service_store_load(d,&link,8,sn,3,&got)==-EXDEV);
 assert(fttr_service_store_load(d,&link,7,sn,1,&got)==-EXDEV);
 fault=1;assert(fttr_service_store_pending(d,&link,7,sn,3,NULL)==-EIO);
 assert(!fttr_service_store_load(d,&link,7,sn,3,&got));fttr_services_clear(&got);
 fault=2;assert(fttr_service_store_pending(d,&link,7,sn,3,NULL)==-EIO);
 assert(fttr_service_store_load(d,&link,7,sn,3,&got)==-ENOTRECOVERABLE);
 assert(!fttr_service_store_save(d,&link,7,sn,3,&s));
 assert(!fchmodat(d,name,0644,0));assert(fttr_service_store_load(d,&link,7,sn,3,&got)==-EACCES);
 assert(!unlinkat(d,name,0));assert(!symlinkat("/dev/null",d,name));
 assert(fttr_service_store_load(d,&link,7,sn,3,&got)==-ELOOP);
 assert(!fttr_service_store_remove(d,sn));
 assert(!fttr_service_store_save(d,&link,7,sn,3,&s));
 /* Pending state must never regain ownership merely by restarting. */
 assert(!fttr_service_store_pending(d,&link,7,sn,3,&s));
 fd=openat(d,name,O_RDONLY);assert(read(fd,data,sizeof(data))==n);close(fd);
 assert(data[26]==2 && !memcmp(data+32,old+32,n-36));
 assert(fttr_service_store_load(d,&link,7,sn,3,&got)==-ENOTRECOVERABLE);
 assert(!fttr_service_store_remove(d,sn));fttr_services_clear(&s);close(d);
 assert(!chmod(argv[1],0755));assert(fttr_service_store_open(argv[1])==-EACCES);
 printf("Durable service codec: %zd single-byte corruption cases, 23 bounded dimensions, ownership/lease/fsync/identity/permissions passed\n",n);
}
'''
with tempfile.TemporaryDirectory() as directory:
    p=Path(directory);(p/'state').mkdir(mode=0o700);(p/'test.c').write_text(code)
    src=ROOT/'port/packages/tyfza1-fttr/src'
    sources=['service-store','omci-services','omci-service','omci-association','omci-gem','omci-objects',
             'omci-mcast','omci-model','omci-vlan','omci-me','omci-mib','omci-socket','omci-wire','miniolt']
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined',
        '-I'+str(src),str(p/'test.c'),*[str(src/(s+'.c')) for s in sources],'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test'),str(p/'state')],check=True,env=dict(os.environ,UBSAN_OPTIONS="halt_on_error=1",ASAN_OPTIONS="detect_leaks=1"))
