#!/usr/bin/env python3
"""Exercise the real daemon service-policy parser before hardware ownership."""
from pathlib import Path
import argparse,subprocess,tempfile
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--daemon",type=Path);args=parser.parse_args()
r=Path(__file__).resolve().parents[1];src=r/'port/packages/tyfza1-fttr/src';s=(src/'fttrd.c').read_text()
a=s.index('static int load_policies(');b=s.index('\nstatic int load_wifi_policies(',a)
code=r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdint.h>
#include <stdbool.h>
#include <net/if.h>
#ifndef IFNAMSIZ
#define IFNAMSIZ IF_NAMESIZE
#endif
#include "omci-service.h"
struct service_policy {bool wildcard;uint8_t serial[8];unsigned ordinal;char bridge[IFNAMSIZ];struct fttr_service_config config;struct service_policy *next;};
struct controller {struct service_policy *policies;unsigned policy_count;bool multicast_enabled;};
'''+s[a:b]+r'''
int main(int argc,char **argv){
 assert(argc==3);struct controller c={};int expected=atoi(argv[2]);int ret=load_policies(&c,argv[1]);
 assert(ret==expected);if(!ret)assert(c.policy_count);
 while(c.policies){struct service_policy *p=c.policies;c.policies=p->next;free(p);}
}
'''
with tempfile.TemporaryDirectory(prefix='fttr-policy-') as temp:
 p=Path(temp);(p/'t.c').write_text(code)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-ffunction-sections','-fdata-sections','-I'+str(src),str(p/'t.c'),str(src/'omci-service.c'),'-Wl,--gc-sections','-o',str(p/'t')],check=True)
 rows=[(b'* factory 2 br-fttr\n',0),(b'* shared 0 1 0 none 0 0 100 br-fttr\n',0),
       (b'* factory 2 br-fttr\0 trailing-data\n',-22),
       (b'* shared 0 1 0 none 0 0 100 br-fttr\0 extra\n',-22),
       (b'# comment\0 hidden\n* factory 2 br-fttr\n',-22),
       (b'\0* factory 2 br-fttr\n',-22),
       (b'* factory 2 br-fttr\n5445535400000001 factory 2 br-fttr\0 hidden\n',-22)]
 for n,(data,result) in enumerate(rows):
  path=p/str(n);path.write_bytes(data);subprocess.run([str(p/'t'),str(path),str(result)],check=True)
 if args.daemon:
  for folder in ('miniolt','ni','store'):(p/folder).mkdir()
  sentinels=[p/'miniolt/backend',p/'miniolt/tx_enable',p/'ni/fttr_forward',p/'store/status.json']
  for path in sentinels:path.write_bytes(b'unchanged')
  for n,(data,result) in enumerate(rows):
   if not result:continue
   run=subprocess.run([str(args.daemon.resolve()),str(p/'miniolt'),str(p/'ni'),str(p/'store'),'offline0','--services',str(p/str(n))],capture_output=True,text=True,timeout=2)
   assert run.returncode==1 and 'FTTR policy ' in run.stderr and 'Invalid argument' in run.stderr
   assert all(path.read_bytes()==b'unchanged' for path in sentinels)
  print('Full daemon: malformed policy rejected before backend reads or hardware/state writes')
 print('FTTR service policy: valid explicit/default rows and embedded-NUL rejection passed')
