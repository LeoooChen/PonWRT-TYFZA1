#!/usr/bin/env python3
"""Exercise daemon configuration through C/Rust FFI and real OMCI replies."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('library', type=Path, help='Host release/libtyfza1_omci.a')
args = parser.parse_args()
source = root / 'port/packages/tyfza1-pon-control/src'
daemon = (source / 'pon-omcid.c').read_text()

def function(name):
    start = re.search(r'^static (?:int|void) ' + name + r'\(', daemon, re.M).start()
    end = daemon.index('{', start) + 1
    depth = 1
    while depth:
        depth += (daemon[end] == '{') - (daemon[end] == '}')
        end += 1
    return daemon[start:end]

fixture = r'''
#include <assert.h>
#include <errno.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pon-omci.h"
struct pon_config {void *context;};
static const char *version,*disabled;
static int pon_config_load(struct pon_config *c,const char *path){return 0;}
static void uci_free_context(void *c){}
static const char *pon_config_option(struct pon_config *c,const char *name){
 if(!strcmp(name,"omcc_version"))return version;
 if(!strcmp(name,"disable_enhanced_security"))return disabled;
 return NULL;
}
static void get_version(struct omci_agent *agent,unsigned int expected,unsigned int extended){
 uint8_t frame[44]={0,1,0x49,10,1,1,0,0},reply[2048]={0};
 unsigned int offset=extended?10:8,length=extended?12:44;
 frame[3]=extended?11:10;
 if(extended)frame[9]=2;else frame[43]=0x28;
 frame[offset]=0x40;
 int bytes=tyfza1_omci_dispatch(agent,frame,length,reply,0);
 assert(bytes>0 && !reply[offset] && reply[offset+(extended?7:3)]==expected);
}
int main(void){
 const char *versions[]={NULL,"","0xb0","0x86"};
 const char *flags[]={NULL,"","0","1"};
 for(unsigned int v=0;v<4;v++)for(unsigned int f=0;f<4;f++){
  version=versions[v];disabled=flags[f];
  struct omci_identity id={0};
  assert(load_identity(&id)==0);
  assert(id.omcc_version==(v==3?0x86:0xb0));
  assert(id.disable_enhanced_security==(f==3));
  assert(!memcmp(id.serial,"TEST\1\2\3\4",8));
  for(unsigned int ext=0;ext<2;ext++){
   struct omci_agent *a=tyfza1_omci_new(&id);
   get_version(a,id.omcc_version,ext);
   uint8_t frame[44]={0,2,0x48,10,1,0x4c},reply[2048]={0},row[18];
   unsigned int offset=ext?10:8,length=ext?30:44;
   frame[3]=ext?11:10;if(ext)frame[9]=20;else frame[43]=0x28;
   frame[offset+1]=0x20;frame[offset+3]=0x40;
   memset(frame+offset+4,0x55,16);
   memset(row,0xa5,sizeof(row));
   assert(tyfza1_omci_broadcast(a,frame,length,row)==(f!=3));
   assert(tyfza1_omci_dispatch(a,frame,length,reply,0)>0);
   assert(reply[offset]==(f==3?4:0));
   if(f==3)for(unsigned int i=0;i<18;i++)assert(row[i]==0xa5);
   tyfza1_omci_epoch(a);frame[1]++;get_version(a,id.omcc_version,ext);
   assert(tyfza1_omci_broadcast(a,frame,length,row)==(f!=3));
   tyfza1_omci_free(a);
  }
 }
 struct omci_identity zero={0};struct omci_agent *a=tyfza1_omci_new(&zero);
 get_version(a,0xb0,0);tyfza1_omci_free(a);
 const char *invalid[]={"134","0x96","0x100","-1","0x86junk","0XB0"};
 disabled=NULL;
 for(unsigned int i=0;i<6;i++){version=invalid[i];assert(load_identity(&zero)==-EINVAL);}
 version=NULL;
 for(unsigned int i=0;i<6;i++){disabled=invalid[i];assert(load_identity(&zero)==-EINVAL);}
 puts("OMCI compatibility: defaults, independent switches, C/Rust layout, baseline/extended replies, epoch and disabled key writes passed");
}
'''
with tempfile.TemporaryDirectory(prefix='omci-compatibility-') as directory:
    work = Path(directory)
    (work / 'active_serial_number').write_text('5445535401020304\n')
    code = fixture[:fixture.index('static void get_version')] + '#define PON_SYSFS ' + json.dumps(str(work) + '/') + '\n'
    code += function('identity_field') + '\n' + function('load_identity') + '\n'
    code += fixture[fixture.index('static void get_version'):]
    (work / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', '-I' + str(source), str(work / 'test.c'),
                    str(args.library.resolve()), '-lpthread', '-lm', '-ldl', '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)

    ui = root / 'port/packages/luci-app-tyfza1-pon/htdocs/luci-static/resources/view/network/tyfza1-pon.js'
    (work / 'test.js').write_text(r'''
const assert=require('assert'),fs=require('fs');
const options={};
const section={tab(){},taboption(tab,type,name){
 const o={tab,values:[],dependencies:[],value(v){this.values.push(v);},depends(k,v){this.dependencies.push(v);}};
 options[name]=o;return o;
}};
const form={Flag:1,ListValue:2,Value:3,DummyValue:4,NamedSection:5,
 Map:function(){this.section=()=>section;this.render=()=>Promise.resolve({});}};
const ui={page(){},fold(){},authentication(){}};
const uci={get(){return null;}};
global.L={hasViewPermission:()=>true,url:()=>''};global.E=()=>({});
const view=new Function('view','form','ui','uci','status',fs.readFileSync(process.argv[2],'utf8'))(
 {extend:o=>o},form,ui,uci,{modes:{},callStatus(){},ponCard(){}});
view.render([null,null]).then(()=>{
 assert.deepEqual(options.omcc_version.values,['0xb0','0x86']);
 for(const [name,def] of [['omcc_version','0xb0'],['disable_enhanced_security','0']]){
  assert.equal(options[name].default,def);
  assert.equal(options[name].tab,'advanced');
  assert.equal(options[name].rmempty,false);assert.equal(options[name].retain,true);
  assert.deepEqual(options[name].dependencies,['gpon','xgpon','xgspon']);
 }
 console.log('LuCI compatibility: defaults, allowed versions and ITU-only independent options passed');
});
''')
    subprocess.run(['node', str(work / 'test.js'), str(ui)], check=True)
