#!/usr/bin/env python3
"""Exercise the production durable upgrade record and read-only reconciliation."""
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
code=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <sys/syscall.h>
#include "upgrade-store.h"
#include "omci-wire.h"
static int fault;
int real_fsync(int fd){return syscall(SYS_fsync,fd);}
int fsync(int fd){if(fault && !--fault){errno=EIO;return -1;}return real_fsync(fd);}
int main(int argc,char **argv){
 assert(argc==2);int directory=fttr_upgrade_store_open(argv[1]);assert(directory>=0);
 assert(fttr_upgrade_store_open(argv[1])==-EWOULDBLOCK);
 struct fttr_upgrade_record r={.serial={0x54,0x45,0x53,0x54,0,0,0,7},.backend=3,
 .action=FTTR_UPGRADE_ACTIVATE,.stage=FTTR_UPGRADE_ACTIVATING,.image=1,.pending=true};
 memcpy(r.version,"VERSION0000001",14);struct fttr_upgrade_record got;
 assert(fttr_upgrade_store_load(directory,r.serial,&got)==-ENOENT);
 assert(!fttr_upgrade_store_save(directory,&r));assert(!fttr_upgrade_store_load(directory,r.serial,&got));
 assert(!memcmp(&r,&got,sizeof(r)));
 const char *name="5445535400000007.upgrade";
 unsigned char old[64],data[64];int fd=openat(directory,name,O_RDONLY);assert(read(fd,old,64)==64);close(fd);
 /* Every changed byte, including version/identity/CRC, rejects. */
 for(unsigned int i=0;i<64;i++){
  memcpy(data,old,64);data[i]^=1;fd=openat(directory,name,O_WRONLY|O_TRUNC);assert(write(fd,data,64)==64);close(fd);
  assert(fttr_upgrade_store_load(directory,r.serial,&got)==-EBADMSG);
 }
 assert(!fttr_upgrade_store_save(directory,&r));
 /* File fsync failure leaves the old committed intent. Directory fsync
  * failure may expose the new intent, but its caller must stop before IO. */
 r.pending=false;fault=1;assert(fttr_upgrade_store_save(directory,&r)==-EIO);
 assert(!fttr_upgrade_store_load(directory,r.serial,&got) && got.pending);
 fault=2;assert(fttr_upgrade_store_save(directory,&r)==-EIO);
 assert(!fttr_upgrade_store_load(directory,r.serial,&got) && !got.pending);
 assert(!fttr_upgrade_store_save(directory,&r));
 assert(!fchmodat(directory,name,0644,0));assert(fttr_upgrade_store_load(directory,r.serial,&got)==-EACCES);
 assert(!fchmodat(directory,name,0600,0));
 assert(!unlinkat(directory,name,0));assert(!symlinkat("/dev/null",directory,name));
 assert(fttr_upgrade_store_load(directory,r.serial,&got)==-ELOOP);
 assert(!fttr_upgrade_store_remove(directory,r.serial));
 unsigned char other[8]={1};assert(fttr_upgrade_store_load(directory,other,&got)==-ENOENT);
 /* A stale staging file is never accepted; save atomically replaces it. */
 fd=openat(directory,"5445535400000007.upgrade.new",O_CREAT|O_WRONLY,0600);close(fd);
 assert(fttr_upgrade_store_load(directory,r.serial,&got)==-ENOENT);
 assert(!fttr_upgrade_store_save(directory,&r));assert(!fttr_upgrade_store_remove(directory,r.serial));
 close(directory);assert(!chmod(argv[1],0755));assert(fttr_upgrade_store_open(argv[1])==-EACCES);
 puts("Upgrade store: corruption64, atomic fsync failures, lease, identity, permissions and symlink checks passed");
}
'''
with tempfile.TemporaryDirectory() as folder:
    p=Path(folder);state=p/'state';state.mkdir(mode=0o700);(p/'test.c').write_text(code)
    src=ROOT/'port/packages/tyfza1-fttr/src'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(src),str(p/'test.c'),str(src/'upgrade-store.c'),str(src/'omci-wire.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test'),str(state)],check=True)
