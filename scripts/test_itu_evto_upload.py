#!/usr/bin/env python3
"""Use production snapshot parsing/upload code with OLT-generated wire fixtures."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-path.c').read_text()

def function(name):
    start = re.search(r'^ssize_t ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

fixture = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include "rtl9617c-pon-mode.h"
typedef uint8_t u8;typedef uint64_t u64;
#define READ_ONCE(v) (v)
#define BIT(n) (1U<<(n))
#define GFP_KERNEL 0
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define lockdep_is_held(p) 1
#define rcu_dereference_protected(p,c) (p)
#define rcu_access_pointer(p) (p)
#define rcu_replace_pointer(p,v,c) ({__typeof__(p) old=(p);(p)=(v);old;})
#define kvfree_rcu(p,m) free(p)
#define kvfree(p) free(p)
#define kvmalloc(n,g) malloc(n)
#define kvzalloc(n,g) calloc(1,n)
#define netif_carrier_ok(d) 1
#define netif_running(d) 1
#define netif_tx_disable(d) ((void)(d))
#define netif_tx_wake_all_queues(d) ((void)(d))
static unsigned int get_unaligned_le16(const void *p){const u8 *b=p;return b[0]|b[1]<<8;}
static unsigned int get_unaligned_le32(const void *p){const u8 *b=p;return b[0]|b[1]<<8|b[2]<<16|(unsigned)b[3]<<24;}
#include "rtl9617c-pon-path.h"
struct net_device {int unused;};
struct rtl_itu_path_state {int rcu;struct rtl_itu_paths paths;};
struct rtl9617c_pon {
 int mode,puc_configured,pdc_configured,control_lock,lock,itu_paths_ready,itu_paths_error;
 unsigned long long itu_path_epoch,itu_line_epoch,itu_upload_epoch;
 struct net_device *omci_device,*data_devices[1];void *ni_dev;
 struct rtl_itu_path_state *itu_paths;
 void *itu_upload;size_t itu_upload_size,itu_upload_received;
};
static unsigned int programs;
static int path_program(struct rtl9617c_pon *pon,const struct rtl_itu_paths *paths){
 (void)pon;assert(paths->count<=248);programs++;return 0;}
static int rtl9617c_ni_itu_paths(void *ni,struct net_device *dev,const struct rtl_itu_paths *paths){
 (void)ni;(void)dev;(void)paths;return 0;}
static void rtl_pon_itu_paths_invalidate(struct rtl9617c_pon *pon){pon->itu_paths_ready=false;pon->itu_path_epoch++;}
static void rtl_pon_itu_carrier(struct rtl9617c_pon *pon){(void)pon;}
'''
cases = r'''
static unsigned char *load(const char *name,size_t *length){
 FILE *f=fopen(name,"rb");assert(f);assert(!fseek(f,0,SEEK_END));long n=ftell(f);assert(n>0);
 unsigned char *p=malloc(n);assert(p);rewind(f);assert(fread(p,1,n,f)==(size_t)n);fclose(f);*length=n;return p;
}
int main(int argc,char **argv){
 assert(argc==4 || argc==5);size_t small_size,large_size,mapped_size;
 unsigned char *small=load(argv[1],&small_size),*large=load(argv[2],&large_size);
 struct net_device dev={};struct rtl9617c_pon pon={.mode=RTL_PON_GPON,.puc_configured=1,.pdc_configured=1,.data_devices={&dev}};
 assert(rtl_pon_itu_paths_upload(&pon,(void *)small,0,small_size)==(ssize_t)small_size);
 assert(programs==1 && pon.itu_paths_ready && pon.itu_paths->paths.profile_count==2);
 const struct rtl_itu_paths *paths=&pon.itu_paths->paths;
 struct rtl_evto_packet before={.protocol=0x0800},wire,after;
 const struct rtl_itu_path *path=rtl_itu_path_transmit_vlan(paths,&before,&wire);
 assert(path && path->gem_id==4000 && path->vlan==100 && wire.count==1);
 assert(wire.tag[0].tpid==0x8100 && wire.tag[0].tci==(5<<13|200));
 assert(rtl_itu_path_receive_vlan(paths,path->index,&wire,&after) && after.count==0);
 struct rtl_itu_path_state *old=pon.itu_paths;
 assert(large_size>4096);
 assert(rtl_pon_itu_paths_upload(&pon,(void *)large,0,4096)==4096);
 assert(pon.itu_paths==old && programs==1); /* No partial graph publication. */
 assert(rtl_pon_itu_paths_upload(&pon,(void *)(large+4096),4096,large_size-4096)==(ssize_t)(large_size-4096));
 assert(programs==2 && pon.itu_paths->paths.count==8 && pon.itu_paths->paths.profile_count==8);
 for(unsigned int i=0;i<8;i++)assert(pon.itu_paths->paths.profile[i].count==32);
 old=pon.itu_paths;
 for(size_t at=0;at<large_size;){size_t n=large_size-at<4096?large_size-at:4096;
  assert(rtl_pon_itu_paths_upload(&pon,(void *)(large+at),at,n)==(ssize_t)n);at+=n;}
 assert(pon.itu_paths==old && programs==2); /* Unchanged rules retain live flows. */
 unsigned char empty[12]={4,0,0,0,0,0,0,0,12};
 assert(rtl_pon_itu_paths_upload(&pon,(void *)empty,0,sizeof(empty))==12);
 assert(!pon.itu_paths_ready && pon.itu_paths->paths.count==0 && programs==3);
 unsigned char *mapped=load(argv[3],&mapped_size);
 assert(rtl_pon_itu_paths_upload(&pon,(void *)mapped,0,mapped_size)==(ssize_t)mapped_size);
 paths=&pon.itu_paths->paths;
 before=(struct rtl_evto_packet){.protocol=0x0800};
 path=rtl_itu_path_transmit_vlan(paths,&before,&wire);
 assert(path && path->gem_id==4000 && path->pbits==1 && wire.tag[0].tci==(1<<13|200));
 assert(rtl_itu_path_receive_vlan(paths,path->index,&wire,&after) && !after.count);
 /* Version4 adds a T-CONT scheduler after the unchanged path/profile records. */
 unsigned char scheduled[74]={4,0,1,0,0,0,1,0,74};
 scheduled[12]=0x27;scheduled[13]=4; /* Alloc-ID1063. */
 scheduled[14]=0xa0;scheduled[15]=15; /* GEM4000. */
 scheduled[16]=scheduled[17]=255;scheduled[18]=scheduled[24]=255;
 scheduled[20]=1;scheduled[22]=1;
 memset(scheduled+25,255,10); /* No VLAN profiles. */
 scheduled[62]=1;scheduled[66]=3;scheduled[67]=5;
 unsigned int before_programs=programs;
 assert(rtl_pon_itu_paths_upload(&pon,(void *)scheduled,0,sizeof(scheduled))==74);
 assert(programs==before_programs+1 && pon.itu_paths->paths.weights[1][0]==3 &&
        pon.itu_paths->paths.weights[1][1]==5 && pon.itu_paths->paths.path[0].queue==1);
 old=pon.itu_paths;
 assert(rtl_pon_itu_paths_upload(&pon,(void *)scheduled,0,sizeof(scheduled))==74);
 assert(pon.itu_paths==old && programs==before_programs+1);
 scheduled[67]=9;
 assert(rtl_pon_itu_paths_upload(&pon,(void *)scheduled,0,sizeof(scheduled))==74);
 assert(programs==before_programs+2 && pon.itu_paths->paths.weights[1][1]==9);
 old=pon.itu_paths;
 scheduled[66]=0; /* PUC cannot place SP queue0 below WRR queue1. */
 assert(rtl_pon_itu_paths_upload(&pon,(void *)scheduled,0,sizeof(scheduled))==-EINVAL);
 assert(pon.itu_paths==old && programs==before_programs+2);
 scheduled[66]=3;scheduled[62]=0; /* Management T-CONT0 stays reserved. */
 assert(rtl_pon_itu_paths_upload(&pon,(void *)scheduled,0,sizeof(scheduled))==-EINVAL);
 assert(pon.itu_paths==old && programs==before_programs+2);
 if(argc==5) {
  size_t queue_size;unsigned char *queue=load(argv[4],&queue_size);
  assert(rtl_pon_itu_paths_upload(&pon,(void *)queue,0,queue_size)==(ssize_t)queue_size);
  paths=&pon.itu_paths->paths;
  assert(paths->count==2 && paths->path[0].alloc_id==2000 && paths->path[0].tcont==2);
  assert(paths->weights[2][0]==9 && paths->weights[2][1]==9);
  free(queue);
 }
 /* A partial GPON snapshot must not program the later XG-PON session. */
 before_programs=programs;old=pon.itu_paths;pon.itu_line_epoch=17;
 assert(rtl_pon_itu_paths_upload(&pon,(void*)large,0,4096)==4096);
 pon.mode=RTL_PON_XGPON;pon.itu_line_epoch++;pon.itu_paths_ready=false;
 assert(rtl_pon_itu_paths_upload(&pon,(void*)(large+4096),4096,large_size-4096)==-ESTALE);
 assert(programs==before_programs && pon.itu_paths==old && !pon.itu_paths_ready);
 /* Also cover an epoch change after upload detaches a complete buffer,
  * before the commit takes its state lock, including an empty snapshot. */
 assert(rtl_pon_itu_paths_write(&pon,(void*)large,large_size,17)==-ESTALE);
 assert(rtl_pon_itu_paths_write(&pon,(void*)empty,sizeof(empty),17)==-ESTALE);
 assert(programs==before_programs && pon.itu_paths==old);
 for(size_t at=0;at<large_size;){size_t n=large_size-at<4096?large_size-at:4096;
  assert(rtl_pon_itu_paths_upload(&pon,(void*)(large+at),at,n)==(ssize_t)n);at+=n;}
 assert(programs==before_programs+1 && pon.itu_paths_ready);
 /* Same-protocol LOS/recovery advances the line epoch as well. */
 before_programs=programs;
 assert(rtl_pon_itu_paths_upload(&pon,(void*)large,0,4096)==4096);
 pon.itu_line_epoch++;
 assert(rtl_pon_itu_paths_upload(&pon,(void*)(large+4096),4096,large_size-4096)==-ESTALE);
 assert(programs==before_programs);
 free(mapped);
 free(pon.itu_paths);free(pon.itu_upload);free(small);free(large);
 puts("Production ITU upload: OLT graph, atomic snapshots, scheduler updates; stale cross-family/recovery epochs rejected before hardware; fresh session reapplies");
}
'''
with tempfile.TemporaryDirectory(prefix='tyf-evto-upload-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(fixture + function('rtl_pon_itu_paths_write') +
                               function('rtl_pon_itu_paths_upload') + cases)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare',
                    '-fsanitize=address,undefined', '-I', str(drivers), str(work / 'test.c'),
                    '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test'), str(root / 'artifacts/goal-evto/veip-ani.bin'),
                    str(root / 'artifacts/goal-evto/large-profile.bin'),
                    str(root / 'artifacts/goal-evto/mapper-stage.bin')] + sys.argv[1:], check=True)
