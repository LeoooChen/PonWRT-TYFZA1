#!/usr/bin/env python3
"""Connect real OMCI FFI mutations to the production ITU snapshot commit path."""
import ast
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
tree = ast.parse((root / 'scripts/test_itu_evto_upload.py').read_text())
fixture = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == 'fixture' for target in node.targets))
fixture = fixture.replace('static unsigned int programs;', 'static unsigned int programs;\nstatic int program_error;')
fixture = fixture.replace('(void)pon;assert(paths->count<=248);programs++;return 0;',
                          '(void)pon;assert(paths->count<=248);programs++;return program_error;')
fixture = fixture.replace('pon->itu_paths_ready=false;pon->itu_path_epoch++;',
                          'pon->itu_paths_ready=false;pon->itu_path_epoch++;pon->itu_paths_error=0;')
source = (drivers / 'rtl9617c-pon-path.c').read_text()


def function(name):
    start = re.search(r'^ssize_t ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


cases = r'''
#include "pon-omci.h"
static struct net_device dev;
static struct rtl9617c_pon pon={.mode=RTL_PON_GPON,.puc_configured=1,.pdc_configured=1,.data_devices={&dev}};
static unsigned char frame[44],reply[2048];
static unsigned int transaction;
static void be16(unsigned char *p,unsigned int value){p[0]=value>>8;p[1]=value;}
static int apply(void *context,const unsigned char *paths,size_t length){
 (void)context;assert(reply[8]==0xa5);
 int ret=rtl_pon_itu_paths_write(&pon,(const char*)paths,length,pon.itu_line_epoch);
 return ret<0?ret:0;
}
static unsigned int dispatch(struct omci_agent *agent,int *error){
 memset(reply,0xa5,sizeof(reply));
 assert(tyfza1_omci_dispatch_apply(agent,frame,sizeof(frame),reply,error,apply,NULL)==44);
 return reply[8];
}
static unsigned int request(struct omci_agent *agent,unsigned int action,unsigned int cls,
 unsigned int id,const unsigned char *body,unsigned int size){
 memset(frame,0,sizeof(frame));be16(frame,++transaction);frame[2]=0x40|action;frame[3]=10;
 be16(frame+4,cls);be16(frame+6,id);frame[43]=0x28;memcpy(frame+8,body,size);
 int error=0;return dispatch(agent,&error);
}
static unsigned int set(struct omci_agent *agent,unsigned int cls,unsigned int id,
 unsigned int attribute,const unsigned char *value,unsigned int size){
 unsigned char body[8]={};be16(body,0x8000U>>(attribute-1));memcpy(body+2,value,size);
 return request(agent,8,cls,id,body,size+2);
}
static void gem(struct omci_agent *agent,unsigned int n){
 unsigned char body[32]={};be16(body,4000+n);be16(body+2,0x8000);body[4]=3;
 be16(body+5,0x8000+n);be16(body+7,0xffff);be16(body+9,0xffff);be16(body+11,0xffff);
 assert(!request(agent,4,268,0x100+n,body,13));
 memset(body,0,sizeof(body));be16(body,0x100+n);body[2]=5;
 be16(body+3,0xffff);be16(body+5,0xffff);
 assert(!request(agent,4,266,0x200+n,body,10));
}
static unsigned int bridge(struct omci_agent *agent,unsigned int n){
 unsigned char body[14]={};be16(body,1);body[2]=n+1;body[3]=5;be16(body+4,0x200+n);
 return request(agent,4,47,0x300+n,body,sizeof(body));
}
static void filter(struct omci_agent *agent,unsigned int n){
 unsigned char body[26]={};be16(body,100+n);body[24]=4;body[25]=1;
 assert(!request(agent,4,84,0x300+n,body,sizeof(body)));
}
int main(void){
 struct omci_identity identity={.serial={'T','E','S','T',1,2,3,4}};
 struct omci_agent *agent=tyfza1_omci_new(&identity);
 unsigned char value[4],failed[44],paths[65536];
 be16(value,1063);assert(!set(agent,262,0x8000,1,value,2));
 gem(agent,0);assert(!bridge(agent,0));
 assert(pon.itu_paths_ready && pon.itu_paths->paths.count==1);
 struct rtl_itu_path_state *old=pon.itu_paths;
 unsigned int previous=programs;
 gem(agent,1);
 assert(bridge(agent,1)==1);memcpy(failed,frame,sizeof(failed));
 assert(pon.itu_paths==old && pon.itu_paths_ready && programs==previous);
 assert(tyfza1_omci_paths(agent,paths)==62);
 filter(agent,0);filter(agent,1);
 memcpy(frame,failed,sizeof(frame));int error=0;
 assert(!dispatch(agent,&error) && !error);
 assert(pon.itu_paths_ready && pon.itu_paths->paths.count==2);
 assert(pon.itu_paths->paths.path[0].vlan==100 && pon.itu_paths->paths.path[1].vlan==101);
 value[0]=2;assert(!set(agent,278,0x8000,3,value,1));
 be16(value,0x8000);assert(!set(agent,277,0x8000,7,value,2));
 assert(pon.itu_paths->paths.weights[1][0]==1 && pon.itu_paths->paths.weights[1][1]==1);
 old=pon.itu_paths;previous=programs;
 program_error=-ETIMEDOUT;value[0]=9;
 assert(set(agent,277,0x8000,8,value,1)==1);
 assert(!pon.itu_paths_ready && pon.itu_paths==old && programs==previous+1);
 program_error=0;value[0]=1; /* Same committed MIB value, but hardware was invalidated. */
 assert(!set(agent,277,0x8000,8,value,1));
 assert(pon.itu_paths_ready && programs==previous+2);
 assert(pon.itu_paths->paths.weights[1][0]==1 && pon.itu_paths->paths.weights[1][1]==1);
 tyfza1_omci_free(agent);free(pon.itu_paths);
 puts("Real OMCI FFI/kernel commit: conflicting intermediate GEM rejected before ACK, filter then retry succeeds, failed hardware commit is reapplied by same-value SET");
}
'''
library = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/tyfza1-omci-queue-target/release/libtyfza1_omci.a')
with tempfile.TemporaryDirectory(prefix='tyf-omci-kernel-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(fixture + function('rtl_pon_itu_paths_write') + cases)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare',
                    '-I' + str(drivers), '-I' + str(root / 'port/packages/tyfza1-pon-control/src'),
                    str(work / 'test.c'), str(library), '-lpthread', '-lm', '-ldl',
                    '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
