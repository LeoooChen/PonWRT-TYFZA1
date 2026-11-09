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
#include <sys/socket.h>
#include <unistd.h>
#include "sync-client.h"
static int failure, calls;
static bool fail(void) {return failure && ++calls==failure;}
struct json_object *__real_json_object_new_object(void);
struct json_object *__wrap_json_object_new_object(void) {return fail()?NULL:__real_json_object_new_object();}
struct json_object *__real_json_object_new_string(const char *s);
struct json_object *__wrap_json_object_new_string(const char *s) {return fail()?NULL:__real_json_object_new_string(s);}
int __real_json_object_object_add(struct json_object *o,const char *k,struct json_object *v);
int __wrap_json_object_object_add(struct json_object *o,const char *k,struct json_object *v) {return fail()?-1:__real_json_object_object_add(o,k,v);}
const char *__real_json_object_to_json_string_ext(struct json_object *o,int f);
const char *__wrap_json_object_to_json_string_ext(struct json_object *o,int f) {return fail()?NULL:__real_json_object_to_json_string_ext(o,f);}
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
 int fd[2];assert(!socketpair(AF_UNIX,SOCK_DGRAM,0,fd));
 struct json_object *input=json_tokener_parse("{\"ahsapd.qos\":{\"synthetic\":[1,null,\"two\"],\"SyncFromMaster\":\"0\"}}");
 const uint8_t source[6]={2,0,0,0,0,1},target[6]={2,0,0,0,0,2};
 char before[256];snprintf(before,sizeof(before),"%s",json_object_to_json_string_ext(input,JSON_C_TO_STRING_PLAIN));
 for(int n=1;n<64;n++) {
  calls=0;failure=n;
  int result=fttr_sync_request(fd[0],source,target,0x88aa,5,15,input);
  int count=calls;failure=0;
  char frame[2048];ssize_t got=recv(fd[1],frame,sizeof(frame),MSG_DONTWAIT);
  assert(!strcmp(before,json_object_to_json_string_ext(input,JSON_C_TO_STRING_PLAIN)));
  if(count<n) {assert(!result && got>20);break;}
  /* The first serialization is the pre-existing generic content shortcut;
   * QosSync builds its own payload, so failure there is not consequential. */
  if(n==1)assert(!result && got>20);
  else assert(result==-ENOMEM && got<0 && errno==EAGAIN);
  assert(n!=63);
 }
 json_object_put(input);close(fd[0]);close(fd[1]);
 bool qos_ack=false;
 const char *qos_replies[]={"{\"Result\":1}","{\"RPCMethod\":\"QosSync\",\"Result\":1}",
  "{\"Result\":0}","{\"Result\":true}","{\"RPCMethod\":\"Register\"}",
  "{\"RPCMethod\":\"WanIPChange\",\"Result\":1}","{\"RPCMethod\":\"QosSync\\u0000x\",\"Result\":1}","{}"};
 const int qos_errors[]={0,0,-EREMOTEIO,-EBADMSG,-ESTALE,-EAGAIN,-EBADMSG,-EAGAIN};
 for(unsigned i=0;i<sizeof(qos_errors)/sizeof(*qos_errors);i++) {
  qos_ack=false;
  assert(decode(qos_replies[i],strlen(qos_replies[i]),15,&qos_ack)==qos_errors[i]);
  assert(qos_ack==!qos_errors[i]);
 }
 const char *qos_inputs[]={"{\"ahsapd.qos\":{}}","{\"ahsapd.qos\":{\"nested\":[null,true,42,\"test\"],\"SyncFromMaster\":\"0\"}}",
  "{}","{\"ahsapd.qos\":null}","{\"ahsapd.qos\":[]}","{\"ahsapd.qos\":{},\"RPCMethod\":\"Other\"}"};
 for(unsigned i=0;i<sizeof(qos_inputs)/sizeof(*qos_inputs);i++) {
  struct json_object *q=json_tokener_parse(qos_inputs[i]);
  char saved[256];snprintf(saved,sizeof(saved),"%s",json_object_to_json_string_ext(q,JSON_C_TO_STRING_PLAIN));
  assert(fttr_sync_qos_validate(q)==(i<2?0:-EINVAL));
  assert(!strcmp(saved,json_object_to_json_string_ext(q,JSON_C_TO_STRING_PLAIN)));
  json_object_put(q);
 }
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
 puts("Settings/password/QoS parser and allocation faults ASan/UBSan: incomplete/bare ACK, foreign method/MAC, malformed JSON/types/NUL/length and independent reply ownership PASS");
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(src),str(p/'test.c'),*[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],'-ljson-c',*[f'-Wl,--wrap={n}' for n in ('json_object_new_object','json_object_new_string','json_object_object_add','json_object_to_json_string_ext')],'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
