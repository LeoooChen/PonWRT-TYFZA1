#!/usr/bin/env python3
"""Exercise neighbor accumulation ownership, neighbor fields and every JSON allocation failure."""
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
#include <stdlib.h>
#include "sync-client.h"
static unsigned fail_at,calls;
static bool fail(void){return fail_at && ++calls==fail_at;}
void *__real_malloc(size_t n);
void *__wrap_malloc(size_t n){return fail()?NULL:__real_malloc(n);}
struct json_object *__real_json_object_new_string_len(const char *s,int n);
struct json_object *__wrap_json_object_new_string_len(const char *s,int n){return fail()?NULL:__real_json_object_new_string_len(s,n);}
struct json_object *__real_json_object_new_int(int v);
struct json_object *__wrap_json_object_new_int(int v){return fail()?NULL:__real_json_object_new_int(v);}
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
 return fttr_sync_reply_decode(&m,peer,17,false,&ack,summary,observed);
}
int main(void){
 const char *first="{\"RPCMethod\":\"GetWifiNeighbor\",\"MAC\":\"020000000001\",\"RECVFLAG\":\"NEW\",\"scanResult\":\"first\"}";
 const char *more="{\"RPCMethod\":\"GetWifiNeighbor\",\"MAC\":\"020000000001\",\"RECVFLAG\":\"REMAINING\",\"scanResult\":\"second\"}";
 unsigned faults=0;
 for(unsigned prior=0;prior<2;prior++)for(unsigned fresh=0;fresh<2;fresh++){
  if(!prior&&!fresh)continue;
  for(unsigned n=1;n<128;n++){
   struct json_object *summary=NULL,*observed=NULL,*saved;
   if(prior)assert(!decode(first,&summary,&observed));
   saved=summary;
   calls=0;fail_at=n;int ret=decode(fresh?first:more,&summary,&observed);unsigned count=calls;fail_at=0;
   assert(!observed);
   if(count<n){assert(!ret&&summary);json_object_put(summary);break;}
   assert(ret==-ENOMEM&&summary==saved);json_object_put(summary);faults++;assert(n<127);
  }
 }
 struct json_object *summary=NULL,*observed=NULL,*field;
 assert(decode(more,&summary,&observed)==-EPROTO&&!summary);
 assert(!decode(first,&summary,&observed));assert(!decode(more,&summary,&observed));
 json_object_object_get_ex(summary,"scanResult",&field);assert(!strcmp(json_object_get_string(field),"firstsecond"));
 assert(!decode(first,&summary,&observed));
 json_object_object_get_ex(summary,"scanResult",&field);assert(!strcmp(json_object_get_string(field),"first"));
 json_object_object_get_ex(summary,"restarts",&field);assert(json_object_get_int(field)==1);
 for(unsigned i=3;i<64;i++)assert(!decode(more,&summary,&observed));
 assert(decode(first,&summary,&observed)==-E2BIG);json_object_put(summary);summary=NULL;
 struct json_object *input=json_tokener_parse(first);char *large=malloc(65537);assert(large);memset(large,'a',65536);large[65536]=0;
 json_object_object_add(input,"scanResult",json_object_new_string(large));
 assert(!decode(json_object_to_json_string_ext(input,JSON_C_TO_STRING_PLAIN),&summary,&observed));
 assert(decode(more,&summary,&observed)==-E2BIG);
 json_object_put(input);json_object_put(summary);summary=NULL;free(large);
 const char *fields[]={"scanResult","RECVFLAG","MAC","RPCMethod"};
 for(unsigned i=0;i<4;i++)for(unsigned variant=0;variant<3;variant++){
  input=json_tokener_parse(first);
  json_object_object_add(input,fields[i],variant==0?json_object_new_boolean(1):variant==1?json_object_new_int(1):json_object_new_string_len("a\0b",3));
  assert(decode(json_object_to_json_string_ext(input,JSON_C_TO_STRING_PLAIN),&summary,&observed)<0&&!summary&&!observed);json_object_put(input);
 }
 const char *rowtext="row:1,5G,Synthetic,020000000001,11ax,36,55,80\n";
 const uint8_t peer[6]={2,0,0,0,0,1};
 for(unsigned n=1;n<128;n++){
  summary=json_object_new_object();json_object_object_add(summary,"scanResult",json_object_new_string(rowtext));
  calls=0;fail_at=n;int ret=fttr_sync_neighbor_fields(summary,peer);unsigned count=calls;fail_at=0;
  bool published=json_object_object_get_ex(summary,"neighbors",&field);
  if(count<n){assert(!ret&&published&&json_object_array_length(field)==1);json_object_put(summary);break;}
  assert(ret==-ENOMEM&&!published);json_object_put(summary);faults++;assert(n<127);
 }
 printf("Neighbor parser: %u allocation/copy failures, replacement preserves old state on failure, orphan/bounds/NUL/type ASan/UBSan PASS\n",faults);
}

'''
with tempfile.TemporaryDirectory() as directory:
    d = Path(directory)
    (d/'test.c').write_text(code)
    wrapped = ('json_object_new_array','json_object_new_object','json_object_new_string',
               'json_object_new_boolean','json_object_object_add','json_object_array_add','json_object_new_string_len','json_object_new_int','malloc')
    subprocess.run(['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                    '-I'+str(src),str(d/'test.c'),*[str(src/(n+'.c')) for n in ('sync-client','sync-wire','sync-peer')],
                    '-ljson-c',*[f'-Wl,--wrap={n}' for n in wrapped],'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
