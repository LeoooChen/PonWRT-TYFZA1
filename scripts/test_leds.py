#!/usr/bin/env python3
"""Focused LED checks: output overrides, radio registers, PON state and OMCI auth."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
overlay = root / 'port/overlay/target/linux/rtl9617c/files-6.18'
source = (overlay / 'drivers/leds/leds-tyfza1.c').read_text()


def function(name):
    start = re.search(r'^static .*\b' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#include "power.h"
#include "led.h"
#include "pon-omci.h"
#define container_of(p,t,f) ((t *)((char *)(p)-offsetof(t,f)))
enum led_brightness {LED_OFF, LED_ON};
struct device {}; struct device_attribute {}; struct led_classdev {};
struct tyfza1_led {struct led_classdev cdev; unsigned int requested; int (*output)(struct tyfza1_led *,unsigned int);};
static unsigned int tyfza1_led_mode=2; static int tyfza1_led_lock;
static struct tyfza1_led leds[2]; static bool physical[2];
#define list_for_each_entry(p,h,n) for(p=leds;p<leds+2;p++)
#define dev_err(...) ((void)0)
static void mutex_lock(int *v){assert(!*v);*v=1;}
static void mutex_unlock(int *v){assert(*v);*v=0;}
static int kstrtouint(const char *s,int base,unsigned int *v){*v=strtoul(s,NULL,base);return 0;}
static int output(struct tyfza1_led *led,unsigned int on){assert(tyfza1_led_lock);physical[led-leds]=on;return 0;}
static unsigned char regs[0x300];
static unsigned char read8(void *c,unsigned int r){(void)c;return regs[r];}
static unsigned short read16(void *c,unsigned int r){(void)c;return regs[r]|regs[r+1]<<8;}
static void write8(void *c,unsigned int r,unsigned char v){(void)c;regs[r]=v;}
static void write16(void *c,unsigned int r,unsigned short v){(void)c;regs[r]=v;regs[r+1]=v>>8;}
static int apply_error;
static int apply(void *c,const uint8_t *p,size_t n){(void)c;(void)p;(void)n;return apply_error;}
'''
cases = r'''
int main(void){
 for(unsigned int i=0;i<2;i++)leds[i].output=output;
 assert(!tyfza1_led_set(&leds[0].cdev,LED_ON)&&physical[0]);
 assert(mode_store(NULL,NULL,"0",1)==1&&!physical[0]);
 tyfza1_led_set(&leds[0].cdev,LED_OFF);tyfza1_led_set(&leds[1].cdev,LED_ON);
 assert(!physical[0]&&!physical[1]);
 assert(mode_store(NULL,NULL,"2",1)==1&&!physical[0]&&physical[1]);
 assert(mode_store(NULL,NULL,"1",1)==1&&physical[0]&&physical[1]);
 tyfza1_led_set(&leds[1].cdev,LED_OFF);
 assert(mode_store(NULL,NULL,"2",1)==1&&!physical[0]&&!physical[1]);
 assert(mode_store(NULL,NULL,"3",1)==-EINVAL);
 struct rtl_ax_io io={.read8=read8,.read16=read16,.write8=write8,.write16=write16};
 memset(regs,0xa5,sizeof(regs));rtl_ax_led_init(&io);
 assert(regs[0x2d4]==0xaf&&read16(NULL,0x62)==0xa5a5);
 rtl_ax_led_output(&io,true);assert(regs[0x61]==0xa4);
 rtl_ax_led_output(&io,false);assert(regs[0x61]==0xa5);
 struct omci_identity identity={.serial={'T','E','S','T',0,0,0,1}};
 struct omci_agent *agent=tyfza1_omci_new(&identity);
 uint8_t request[44]={0,1,0x48,10,0xff,0xfa,0,0,0x10,0,1},reply[64];request[43]=0x28;
 int error=0;apply_error=-EIO;
 assert(tyfza1_omci_dispatch_apply(agent,request,44,reply,&error,apply,NULL)==44&&reply[8]==1);
 assert(!tyfza1_omci_led_auth(agent));
 error=apply_error=0;
 assert(tyfza1_omci_dispatch_apply(agent,request,44,reply,&error,apply,NULL)==44&&!reply[8]);
 assert(tyfza1_omci_led_auth(agent)==1);
 tyfza1_omci_epoch(agent);assert(!tyfza1_omci_led_auth(agent));tyfza1_omci_free(agent);
 puts("LED override/restore, original radio register vectors, committed OMCI auth and epoch reset passed");
}
'''
ucode = (root / 'port/packages/tyfza1-pon-control/files/leds.uc').read_text()
mock = r'''
let step=0, wifi_bindings=0;
const cases=[
 {state:'O5',auth:0,want:250}, {state:'O5',auth:1,want:1},
 {state:'O6',auth:1,want:1}, {state:'O5',auth:1,want:250},
 {state:'O5',auth:2,want:0}, {state:'O5',auth:1,stale:true,want:250},
 {state:'O5',auth:1,disabled:true,want:0},
 {epon:true,registered:1,want:250}, {epon:true,registered:1,stable:true,want:1},
 {epon:true,registered:0,want:0}, {state:'O5',auth:1,old_session:true,want:250}
];
function readfile(path){
 let c=cases[step];
 if(index(path,'/sys/class/net/')==0){
  if(index(path,'/phy80211/device/device')>=0)return '0xc852\n';
  if(index(path,'/type')>=0)return index(path,'/mon0/')>=0?'803\n':'1\n';
  if(index(path,'/ifindex')>=0)return step<6?'42\n':'43\n';
 }
 if(path=='/sys/class/leds/LED_WLAN_5G/max_brightness')return '1\n';
 if(path=='/proc/uptime')return '10.00 0';
 if(path=='/proc/123/stat')return '123 (pon-omci) S '+join(' ',[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,999]);
 if(path=='/var/run/pon-oam/llid-0.json')return sprintf('%J',{
  pid:123,process_start:'999',updated_ms:10000,registered:!!c.registered,discovery_stable:!!c.stable});
 if(path=='/var/run/pon-omci')return 'pid=123 process_start=999 updated_ms='+(c.stale?100:10000)+
  ' session='+(c.old_session?1:2)+' auth='+c.auth;
 if(path=='/sys/bus/platform/devices/4f5500000.pon/led_state')
  return 'mode='+(c.epon?'epon-d10g':'xgpon')+' enabled='+(c.disabled?0:1)+
   ' los=0 session=2 registered='+c.registered+' state='+(c.state=='O6'?4:3);
 die('unexpected read '+path);
}
function writefile(path,text){
 if(index(path,'/device_name')>=0){
  if(text!='ap5\n')die('wrong wireless LED interface');
  wifi_bindings++;
 }
 return length(text);
}
function glob(path){return ['/sys/class/net/ap5/phy80211/device/device','/sys/class/net/mon0/phy80211/device/device'];}
'''
ucode = ucode.replace("import { readfile, writefile, glob } from 'fs';", mock)
ucode = ucode.replace('while (true)', 'while (step < length(cases))')
ucode = ucode.replace('sleep(1000);', "if (patterns.LED_PON != cases[step].want) die(sprintf('PON case %d: %J',step,patterns)); step++;")
ucode += "\nif(wifi_bindings!=2)die('wireless LED did not follow recreated interface');\n"
with tempfile.TemporaryDirectory(prefix='tyfza1-led-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(fixture + ''.join(function(n) for n in
        ('tyfza1_led_output', 'tyfza1_led_set', 'mode_store')) + cases)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
        '-I' + str(overlay / 'drivers/net/wireless/realtek/rtl_ax_ap'),
        '-I' + str(root / 'port/packages/tyfza1-pon-control/src'), str(work / 'test.c'),
        sys.argv[1], '-lpthread', '-lm', '-ldl', '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
    (work / 'test.uc').write_text(ucode)
    subprocess.run([str(root / 'sources/openwrt/staging_dir/hostpkg/bin/ucode'), str(work / 'test.uc')], check=True)
print('PON LED: O5/auth/O6, stale daemon, old session, stopped PON and EPON discovery passed')
