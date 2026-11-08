// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>
#include "move-store.h"

static void put32(uint8_t *p,uint32_t v){p[0]=v>>24;p[1]=v>>16;p[2]=v>>8;p[3]=v;}
static uint32_t get32(const uint8_t *p){return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3];}
static bool valid(const struct fttr_move_record *r)
{
 return r->request.version==2 && r->request.action==FTTR_ADMIN_MOVE &&
  r->request.id<FTTR_ONUS && r->request.target<FTTR_ONUS && r->request.id!=r->request.target &&
  memcmp(r->request.serial,(uint8_t[8]){0},8) &&
  (r->stage==FTTR_MOVE_RELEASE || r->stage==FTTR_MOVE_RESERVATION);
}
int fttr_move_store_save(int directory,const struct fttr_omci_link *link,
 enum fttr_backend backend,const struct fttr_move_record *r)
{
 uint8_t data[64]={ 'F','T','M','1' };
 int fd,ret=0;
 if(!valid(r))return -EINVAL;
 memcpy(data+4,link->host_mac,6);memcpy(data+10,link->olt_mac,6);
 data[16]=backend;data[17]=link->downstream_length;data[18]=link->ethertype>>8;data[19]=link->ethertype;
 data[20]=r->stage;data[21]=r->request.id;data[22]=r->request.target;data[23]=r->service;
 memcpy(data+24,r->request.serial,8);memcpy(data+32,r->request.target_serial,8);
 put32(data+60,fttr_omci_crc(data,60));
 if(unlinkat(directory,"move.new",0)&&errno!=ENOENT)return -errno;
 fd=openat(directory,"move.new",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
 if(fd<0)return -errno;
 if(write(fd,data,64)!=64)ret=-EIO;
 if(!ret&&(fsync(fd)||renameat(directory,"move.new",directory,"move")||fsync(directory)))ret=-errno;
 close(fd);if(ret)unlinkat(directory,"move.new",0);
 return ret;
}
int fttr_move_store_load(int directory,const struct fttr_omci_link *link,
 enum fttr_backend backend,struct fttr_move_record *r)
{
 uint8_t data[64];struct stat st;int ret=0;
 int fd=openat(directory,"move",O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
 if(fd<0)return -errno;
 if(fstat(fd,&st)){ret=-errno;goto out;}
 if(!S_ISREG(st.st_mode)||st.st_uid!=geteuid()||(st.st_mode&077)){ret=-EACCES;goto out;}
 if(st.st_size!=64){ret=-EBADMSG;goto out;}
 if(read(fd,data,64)!=64){ret=-EIO;goto out;}
 if(memcmp(data,"FTM1",4)||get32(data+60)!=fttr_omci_crc(data,60)||
    data[23]>1||memcmp(data+40,(uint8_t[20]){0},20)){ret=-EBADMSG;goto out;}
 if(memcmp(data+4,link->host_mac,6)||memcmp(data+10,link->olt_mac,6)||
    data[16]!=backend||data[17]!=link->downstream_length||
    data[18]!=(link->ethertype>>8)||data[19]!=(link->ethertype&255)){ret=-EXDEV;goto out;}
 memset(r,0,sizeof(*r));r->request.version=2;r->request.action=FTTR_ADMIN_MOVE;
 r->stage=data[20];r->request.id=data[21];r->request.target=data[22];r->service=data[23];
 memcpy(r->request.serial,data+24,8);memcpy(r->request.target_serial,data+32,8);
 if(!valid(r))ret=-EBADMSG;
out:
 close(fd);return ret;
}
int fttr_move_store_remove(int directory)
{
 if(unlinkat(directory,"move",0)&&errno!=ENOENT)return -errno;
 return fsync(directory)?-errno:0;
}
int fttr_move_observe(const struct fttr_move_record *r,
 const struct fttr_onu_state *source,const struct fttr_onu_state *target)
{
 if(!valid(r))return -EINVAL;
 if(r->stage==FTTR_MOVE_RESERVATION && !memcmp(source->serial,(uint8_t[8]){0},8) &&
    (!(source->downstream&(1U<<18)) || (source->downstream&(1U<<16))) &&
    !memcmp(target->serial,r->request.serial,8) && !(target->downstream&(1U<<16)))return 1;
 if(memcmp(source->serial,r->request.serial,8) || memcmp(target->serial,r->request.target_serial,8))return -ESTALE;
 if(!(source->downstream&(1U<<18)) || source->downstream&(1U<<16))return -ESTALE;
 if(target->downstream&((1U<<18)|(1U<<16)))return -EBUSY;
 return 0;
}
