#!/usr/bin/env python3
"""Execute production time reply parser under sanitizers with malformed/partial data."""
from pathlib import Path
import subprocess,tempfile
r=Path(__file__).resolve().parents[1];src=r/'port/packages/tyfza1-fttr/src'
code=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "sync-client.h"
static int decode(const char *text,size_t length,unsigned settings,bool *ack){
 const uint8_t peer[6]={2,0,0,0,0,1};
 struct fttr_sync_message m={.payload=(const uint8_t*)text,.length=length};
 struct json_object *summary=NULL,*observed=NULL;
 int ret=fttr_sync_reply_decode(&m,peer,settings,settings==8,ack,&summary,&observed);
 if(!ret && settings==7){assert(summary&&observed&&!fttr_sync_time_validate(observed));}
 else assert(!summary&&!observed);
 json_object_put(summary);json_object_put(observed);return ret;
}
int main(void){
 const char *valid="{\"Result\":1,\"MAC\":\"020000000001\",\"TimeEnable\":1,\"TimeZone\":24,\"NTPServer1\":\"192.0.2.1\",\"NTPServer2\":\"\"}";
 bool ack=false;assert(!decode(valid,strlen(valid),7,&ack)&&ack);
 struct {const char *text;int error;} tests[]={
 {"{}",-EAGAIN}, {"{\"Result\":1}",-EAGAIN}, {"{\"Result\":0}",-EREMOTEIO},
 {"{\"Result\":true}",-EBADMSG}, {"{\"Result\":1,\"MAC\":\"020000000002\"}",-ESTALE},
 {"{\"Result\":1,\"MAC\":\"020000000001\",\"TimeEnable\":0}",-EAGAIN},
 {"{\"RPCMethod\":\"GetWifi\",\"Result\":1}",-EAGAIN},
 {"{\"RPCMethod\":\"Register\"}",-ESTALE},
 {"{\"RPCMethod\":\"GetTimeSetting\\u0000GetWifi\"}",-EBADMSG},
 {"[]",-EBADMSG}, {"null",-EBADMSG}, {"{} {}",-EBADMSG}, {"{",-EBADMSG}
 };
 for(unsigned i=0;i<sizeof(tests)/sizeof(tests[0]);i++)assert(decode(tests[i].text,strlen(tests[i].text),7,&ack)==tests[i].error);
 assert(!decode("{\"Result\":1}",12,8,&ack));
 /* Each reply must carry its own fields even after an earlier positive ACK. */
 assert(decode("{\"TimeEnable\":1}",16,7,&ack)==-EAGAIN);
 for(unsigned field=0;field<4;field++){
  const char *keys[]={"TimeEnable","TimeZone","NTPServer1","NTPServer2"};
  struct json_object *obj=json_tokener_parse(valid);
  json_object_object_add(obj,keys[field],json_object_new_boolean(1));
  const char *text=json_object_to_json_string_ext(obj,JSON_C_TO_STRING_PLAIN);
  assert(decode(text,strlen(text),7,&ack)==-EBADMSG);json_object_put(obj);
 }
 for(unsigned which=0;which<2;which++){
  struct json_object *obj=json_tokener_parse(valid);
  const char embedded[]={'a',0,'b'},longtext[]="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";
  json_object_object_add(obj,"NTPServer1",json_object_new_string_len(which?longtext:embedded,which?46:3));
  const char *text=json_object_to_json_string_ext(obj,JSON_C_TO_STRING_PLAIN);
  assert(decode(text,strlen(text),7,&ack)==-EBADMSG);json_object_put(obj);
 }
 puts("Time parser ASan/UBSan: incomplete/bare ACK, foreign method/MAC, malformed JSON/types/NUL/length and independent reply ownership PASS");
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(src),str(p/'test.c'),*[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],'-ljson-c','-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
