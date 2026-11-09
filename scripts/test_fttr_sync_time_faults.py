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
 int ret=fttr_sync_reply_decode(&m,peer,settings,(settings==8 || settings==10 || settings==12 || settings==14),ack,&summary,&observed);
 if(!ret && (settings==7 || settings==9)){assert(summary&&observed&&!(settings==9?fttr_sync_iptv_validate(observed):fttr_sync_time_validate(observed)));}
 else if(!ret && settings==11)assert(summary&&observed);
 else if(!ret && settings==13){
  assert(summary&&observed&&!fttr_sync_web_password_validate(observed));
  const char *printed=json_object_to_json_string_ext(summary,JSON_C_TO_STRING_PLAIN);
  assert(!strstr(printed,"WebAdminPassword")&&!strstr(printed,"secret-example"));
 }
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
 const char *iptv="{\"Result\":1,\"MAC\":\"020000000001\",\"IPTVEnable\":1,\"IPTVVlan\":100,\"IPTVLanPort\":\"LAN1,LAN3\"}";
 assert(!decode(iptv,strlen(iptv),9,&ack));
 assert(!decode("{\"Result\":1}",12,10,&ack));
 const char *disabled="{\"Result\":1,\"MAC\":\"020000000001\",\"IPTVEnable\":0}";
 assert(!decode(disabled,strlen(disabled),9,&ack));
 for(unsigned i=0;i<3;i++) {
  struct json_object *obj=json_tokener_parse(iptv);
  json_object_object_add(obj,(const char*[]){"IPTVEnable","IPTVVlan","IPTVLanPort"}[i],json_object_new_boolean(1));
  const char *text=json_object_to_json_string_ext(obj,JSON_C_TO_STRING_PLAIN);
  assert(decode(text,strlen(text),9,&ack)==-EBADMSG);json_object_put(obj);
 }
 const char *ports[]={"LAN2,LAN1","LAN1,LAN1","LAN5","LAN1,","LAN1,,LAN2","LAN01","lan1","LAN1;echo"};
 for(unsigned i=0;i<sizeof(ports)/sizeof(ports[0]);i++) {
  struct json_object *obj=json_tokener_parse(iptv);
  json_object_object_add(obj,"IPTVLanPort",json_object_new_string(ports[i]));
  const char *text=json_object_to_json_string_ext(obj,JSON_C_TO_STRING_PLAIN);
  assert(decode(text,strlen(text),9,&ack)==-EBADMSG);json_object_put(obj);
 }
 struct json_object *obj=json_tokener_parse(iptv);
 json_object_object_add(obj,"IPTVLanPort",json_object_new_string_len("LAN1\0LAN2",9));
 const char *text=json_object_to_json_string_ext(obj,JSON_C_TO_STRING_PLAIN);
 assert(decode(text,strlen(text),9,&ack)==-EBADMSG);json_object_put(obj);
 const char *partial="{\"Result\":1,\"MAC\":\"020000000001\",\"IPTVEnable\":1}";
 assert(decode(partial,strlen(partial),9,&ack)==-EAGAIN);
 const char *portreply="{\"Result\":1,\"MAC\":\"020000000001\",\"IPTVLanPort\":\"1,3\"}";
 assert(!decode(portreply,strlen(portreply),11,&ack));assert(!decode("{\"Result\":1}",12,12,&ack));
 const char *badports[]={"01","0","-1","1,1","1,","1,,2","LAN1","2147483648","1;2"};
 for(unsigned i=0;i<sizeof(badports)/sizeof(badports[0]);i++) {
  struct json_object *obj=json_tokener_parse(portreply);
  json_object_object_add(obj,"IPTVLanPort",json_object_new_string(badports[i]));
  const char *text=json_object_to_json_string_ext(obj,JSON_C_TO_STRING_PLAIN);
  assert(decode(text,strlen(text),11,&ack)==-EBADMSG);json_object_put(obj);
 }
 struct json_object *desired=json_tokener_parse("{\"IPTVLanPort\":\"1,3\",\"IPTVVlanId\":\"100\"}");
 obj=json_tokener_parse("{\"IPTVLanPort\":\"1,3\",\"IPTVVlanId\":\"200\"}");
 assert(!fttr_sync_iptv_ports_check(obj,desired,true));
 json_object_object_add(obj,"IPTVLanPort",json_object_new_string("1"));
 assert(fttr_sync_iptv_ports_check(obj,desired,true)==-EUCLEAN);
 json_object_put(obj);json_object_put(desired);
 const char *password="{\"Result\":1,\"MAC\":\"020000000001\",\"WebAdminPassword\":\"secret-example\"}";
 assert(!decode(password,strlen(password),13,&ack));assert(!decode("{\"Result\":1}",12,14,&ack));
 assert(decode("{\"Result\":1}",12,13,&ack)==-EAGAIN);
 for(unsigned i=0;i<3;i++) {
  struct json_object *obj=json_tokener_parse(password);
  json_object_object_add(obj,"WebAdminPassword",i==0?json_object_new_boolean(1):i==1?json_object_new_string("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"):json_object_new_string_len("a\0b",3));
  const char *text=json_object_to_json_string_ext(obj,JSON_C_TO_STRING_PLAIN);
  assert(decode(text,strlen(text),13,&ack)==-EBADMSG);json_object_put(obj);
 }
 puts("Settings/password parser ASan/UBSan: incomplete/bare ACK, foreign method/MAC, malformed JSON/types/NUL/length and independent reply ownership PASS");
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(src),str(p/'test.c'),*[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],'-ljson-c','-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
