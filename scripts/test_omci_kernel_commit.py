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
 static const unsigned int vids[]={46,41,45};
 unsigned char body[26]={};be16(body,vids[n]);body[24]=4;body[25]=1;
 assert(!request(agent,4,84,0x300+n,body,sizeof(body)));
}
int main(void){
 for(unsigned int mode=RTL_PON_GPON;mode<=RTL_PON_XGSPON;mode++) {
 pon=(struct rtl9617c_pon){.mode=mode,.puc_configured=1,.pdc_configured=1,.data_devices={&dev}};
 struct omci_identity identity={.serial={'T','E','S','T',1,2,3,4}};
 struct omci_agent *agent=tyfza1_omci_new(&identity);
 unsigned char value[4],paths[65536];
 be16(value,1063);assert(!set(agent,262,0x8000,1,value,2));
 gem(agent,0);assert(!bridge(agent,0));
 assert(pon.itu_paths_ready && pon.itu_paths->paths.count==1);
 /* OLTs may create all ANI ports before attaching their VLAN filters. */
 for(unsigned int n=1;n<3;n++) {
  gem(agent,n);assert(!bridge(agent,n));
  assert(pon.itu_paths_ready && pon.itu_paths->paths.count==n+1);
  struct rtl_evto_packet input={.count=1,.protocol=0x8863,.tag={{0x8100,41}}},output;
  assert(!rtl_itu_path_transmit_vlan(&pon.itu_paths->paths,&input,&output));
  assert(!rtl_itu_path_select(&pon.itu_paths->paths,41,0));
  assert(!rtl_itu_path_flow(&pon.itu_paths->paths,41));
  assert(!rtl_itu_path_downstream(&pon.itu_paths->paths,41,255));
 }
 assert(tyfza1_omci_paths(agent,paths)==12+3*RTL_ITU_PATH_WIRE_BYTES);
 filter(agent,0);
 assert(rtl_itu_path_flow(&pon.itu_paths->paths,46)->gem_id==4000);
 assert(!rtl_itu_path_flow(&pon.itu_paths->paths,41));
 filter(agent,1);filter(agent,2);
 assert(pon.itu_paths_ready && pon.itu_paths->paths.count==3);
 const unsigned int vids[]={46,41,45};
 for(unsigned int n=0;n<3;n++)for(unsigned int p=0;p<8;p++) {
  struct rtl_evto_packet input={.count=1,.protocol=0x8863,.priority=p,
   .tag={{0x8100,(p<<13)|vids[n]}}},output,received;
  const struct rtl_itu_path *path=rtl_itu_path_transmit_vlan(&pon.itu_paths->paths,&input,&output);
  assert(path && path->gem_id==4000+n && output.count==1 && output.tag[0].tci==input.tag[0].tci);
  assert(rtl_itu_path_flow(&pon.itu_paths->paths,vids[n])==path);
  assert(rtl_itu_path_downstream(&pon.itu_paths->paths,vids[n],255)==path);
  assert(rtl_itu_path_receive_vlan(&pon.itu_paths->paths,path->index,&output,&received)==path);
  assert(received.count==1 && received.tag[0].tci==input.tag[0].tci);
 }
 assert(!rtl_itu_path_flow(&pon.itu_paths->paths,50));
 /* Relaxing classifier overlap must not relax T-CONT hardware consistency. */
 int length=tyfza1_omci_paths(agent,paths);assert(length>12);
 struct rtl_itu_path_state *consistent=pon.itu_paths;
 paths[12+RTL_ITU_PATH_WIRE_BYTES]^=1;
 assert(rtl_pon_itu_paths_write(&pon,(const char*)paths,length,pon.itu_line_epoch)==-EINVAL);
 assert(pon.itu_paths==consistent && pon.itu_paths_ready);
 value[0]=2;assert(!set(agent,278,0x8000,3,value,1));
 be16(value,0x8000);assert(!set(agent,277,0x8000,7,value,2));
 assert(pon.itu_paths->paths.weights[1][0]==1 && pon.itu_paths->paths.weights[1][1]==1);
 struct rtl_itu_path_state *old=pon.itu_paths;
 unsigned int previous=programs;
 program_error=-ETIMEDOUT;value[0]=9;
 assert(set(agent,277,0x8000,8,value,1)==1);
 assert(!pon.itu_paths_ready && pon.itu_paths==old && programs==previous+1);
 program_error=0;value[0]=1; /* Same committed MIB value, but hardware was invalidated. */
 assert(!set(agent,277,0x8000,8,value,1));
 assert(pon.itu_paths_ready && programs==previous+2);
 assert(pon.itu_paths->paths.weights[1][0]==1 && pon.itu_paths->paths.weights[1][1]==1);
 tyfza1_omci_free(agent);free(pon.itu_paths);
 }
 puts("Real OMCI FFI/kernel commit: ANI ports before VLAN filters accepted; ambiguous forwarding blocked; three PPPoE VLAN classifiers and hardware-failure retry passed");
}
'''
library = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/tyfza1-omci-queue-target/release/libtyfza1_omci.a')
with tempfile.TemporaryDirectory(prefix='tyf-omci-kernel-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(fixture + function('rtl_pon_itu_paths_write') + cases)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare',
                    '-fsanitize=address,undefined',
                    '-I' + str(drivers), '-I' + str(root / 'port/packages/tyfza1-pon-control/src'),
                    str(work / 'test.c'), str(library), '-lpthread', '-lm', '-ldl',
                    '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
