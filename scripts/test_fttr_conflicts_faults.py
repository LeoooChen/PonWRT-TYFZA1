#!/usr/bin/env python3
"""Exercise complete AP conflict reply ownership and every JSON allocation failure."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
src = root/'port/packages/tyfza1-fttr/src'
code = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "sync-client.h"
static unsigned fail_at,calls;
static bool fail(void){return fail_at && ++calls==fail_at;}
struct json_object *__real_json_object_new_array(void);
struct json_object *__wrap_json_object_new_array(void){return fail()?NULL:__real_json_object_new_array();}
struct json_object *__real_json_object_new_object(void);
struct json_object *__wrap_json_object_new_object(void){return fail()?NULL:__real_json_object_new_object();}
struct json_object *__real_json_object_new_string(const char *s);
struct json_object *__wrap_json_object_new_string(const char *s){return fail()?NULL:__real_json_object_new_string(s);}
struct json_object *__real_json_object_new_boolean(json_bool b);
struct json_object *__wrap_json_object_new_boolean(json_bool b){return fail()?NULL:__real_json_object_new_boolean(b);}
int __real_json_object_object_add(struct json_object *o,const char *k,struct json_object *v);
int __wrap_json_object_object_add(struct json_object *o,const char *k,struct json_object *v){return fail()?-1:__real_json_object_object_add(o,k,v);}
int __real_json_object_array_add(struct json_object *o,struct json_object *v);
int __wrap_json_object_array_add(struct json_object *o,struct json_object *v){return fail()?-1:__real_json_object_array_add(o,v);}
static int decode(const char *text,struct json_object **summary,struct json_object **observed){
 const uint8_t peer[6]={2,0,0,0,0,1};bool ack=false;
 struct fttr_sync_message m={.payload=(const uint8_t *)text,.length=strlen(text)};
 return fttr_sync_reply_decode(&m,peer,18,false,&ack,summary,observed);
}
int main(void){
 const char *report="{\"RPCMethod\":\"CheckBSSIDConflict\",\"mac\":\"020000000001\",\"ApInfo\":[{\"onuMac\":\"020000000001\",\"onuModel\":\"synthetic\",\"rfband\":\"5G\",\"wlanMac\":\"020000000002\",\"ssidIndex\":\"1\",\"ssid\":\"synthetic SSID\"},{\"onuMac\":\"020000000005\",\"onuModel\":\"other\",\"rfband\":\"2.4G\",\"wlanMac\":\"020000000004\",\"ssidIndex\":\"2\",\"ssid\":\"\"}]}";
 unsigned faults=0;
 for(unsigned n=1;n<128;n++){
  struct json_object *summary=NULL,*observed=NULL;
  calls=0;fail_at=n;int ret=decode(report,&summary,&observed);unsigned count=calls;fail_at=0;
  if(count<n){assert(!ret&&summary&&observed&&json_object_array_length(observed)==2);json_object_put(summary);json_object_put(observed);break;}
  assert(ret==-ENOMEM&&!summary&&!observed);faults++;assert(n<127);
 }
 const char *bad[]={"{","[]","null","{} {}","{\"RPCMethod\":\"CheckBSSIDConflict\\u0000other\",\"ApInfo\":[]}",
  "{\"RPCMethod\":\"CheckBSSIDConflict\",\"ApInfo\":[{}]}",
  "{\"RPCMethod\":\"CheckBSSIDConflict\",\"ApInfo\":[{\"OnuMAC\":\"020000000001\",\"StaMAC\":\"020000000002\"},null]}"};
 for(unsigned i=0;i<sizeof(bad)/sizeof(*bad);i++){
  struct json_object *summary=NULL,*observed=NULL;
  assert(decode(bad[i],&summary,&observed)==-EBADMSG&&!summary&&!observed);
 }
 for(unsigned row=0;row<2;row++)for(unsigned field=0;field<6;field++){
  struct json_object *input=json_tokener_parse(report),*rows,*summary=NULL,*observed=NULL;
  json_object_object_get_ex(input,"ApInfo",&rows);
  struct json_object *entry=json_object_array_get_idx(rows,row);
  const char *name=(const char *[]){"onuMac","onuModel","rfband","wlanMac","ssidIndex","ssid"}[field];
  json_object_object_add(entry,name,json_object_new_boolean(1));
  assert(decode(json_object_to_json_string_ext(input,JSON_C_TO_STRING_PLAIN),&summary,&observed)==-EBADMSG&&!summary&&!observed);
  json_object_put(input);
 }
 for(unsigned row=0;row<2;row++)for(unsigned field=0;field<6;field++){
  struct json_object *input=json_tokener_parse(report),*rows,*summary=NULL,*observed=NULL;
  json_object_object_get_ex(input,"ApInfo",&rows);
  struct json_object *entry=json_object_array_get_idx(rows,row);
  const char *name=(const char *[]){"onuMac","onuModel","rfband","wlanMac","ssidIndex","ssid"}[field];
  json_object_object_del(entry,name);
  assert(decode(json_object_to_json_string_ext(input,JSON_C_TO_STRING_PLAIN),&summary,&observed)==-EBADMSG&&!summary&&!observed);
  json_object_put(input);
 }
 printf("AP conflict parser: %u allocation/copy failures, malformed/partial rows, all fields/types, no partial snapshot ASan/UBSan PASS\n",faults);
}
'''
with tempfile.TemporaryDirectory() as directory:
    d = Path(directory)
    (d/'test.c').write_text(code)
    wrapped = ('json_object_new_array','json_object_new_object','json_object_new_string',
               'json_object_new_boolean','json_object_object_add','json_object_array_add')
    subprocess.run(['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-I'+str(src),str(d/'test.c'),*[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],
                    '-ljson-c',*[f'-Wl,--wrap={n}' for n in wrapped],'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
