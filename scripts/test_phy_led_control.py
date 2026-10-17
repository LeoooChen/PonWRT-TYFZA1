#!/usr/bin/env python3
"""Exercise production LED arbitration and the PHY's original three output modes."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers'


def function(source, name):
    start = re.search(r'^static (?:int|ssize_t) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


leds = (drivers / 'leds/leds-tyfza1.c').read_text()
ni = (drivers / 'net/ethernet/realtek/rtl9617c-xram.c').read_text()
shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
#include <sys/types.h>
enum led_brightness {LED_OFF,LED_ON};
struct led_classdev {int unused;};
struct tyfza1_led {struct led_classdev cdev; int (*output)(struct tyfza1_led *,unsigned int); unsigned int requested;};
struct phy_device {};
struct net_device {struct phy_device *phydev;};
struct rtl_xram {struct net_device *ports[5];struct tyfza1_led phy_led;};
struct device {}; struct device_attribute {};
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define MDIO_MMD_VEND2 31
#define dev_err(...) ((void)0)
static struct rtl_xram x;
static unsigned int tyfza1_led_mode=2;
static int tyfza1_led_lock,fail_write,writes;
static unsigned int reg=0xa5a5;
static int phy_modify_mmd(struct phy_device *p,int mmd,int addr,unsigned int mask,unsigned int value) {
 assert(p==x.ports[4]->phydev && mmd==31 && addr==0xd044 && tyfza1_led_lock);
 writes++;if(fail_write)return -EIO;reg=(reg&~mask)|value;return 0;
}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static int kstrtouint(const char *s,int base,unsigned int *v){*v=strtoul(s,NULL,base);return 0;}
#define list_for_each_entry(p,h,n) for(p=&x.phy_led;p;p=NULL)
'''
cases = r'''
int main(void) {
 struct phy_device phy;struct net_device net={&phy};x.ports[4]=&net;
 x.phy_led.output=rtl_phy_led_output;x.phy_led.requested=2;
 unsigned int unrelated=reg&~0x11U;
 assert(tyfza1_led_set(&x.phy_led.cdev,2)==0 && (reg&0x11)==0x10);
 assert(mode_store(NULL,NULL,"0",1)==1 && (reg&0x11)==0 && x.phy_led.requested==2);
 assert(mode_store(NULL,NULL,"1",1)==1 && (reg&0x11)==1 && x.phy_led.requested==2);
 assert(mode_store(NULL,NULL,"2",1)==1 && (reg&0x11)==0x10);
 assert((reg&~0x11U)==unrelated);
 fail_write=1;
 assert(mode_store(NULL,NULL,"0",1)==-EIO);
 assert(tyfza1_led_set(&x.phy_led.cdev,LED_ON)==-EIO);
 fail_write=0;
 assert(mode_store(NULL,NULL,"2",1)==1 && (reg&0x11)==1);
 assert(tyfza1_led_set(&x.phy_led.cdev,2)==0 && (reg&0x11)==0x10);
 int before=writes;assert(mode_store(NULL,NULL,"3",1)==-EINVAL && writes==before);
}
'''
with tempfile.TemporaryDirectory(prefix='phy-led-control-') as directory:
    path = Path(directory) / 'test.c'
    path.write_text(shim + function(ni, 'rtl_phy_led_output') + '\n' +
        '\n'.join(function(leds, n) for n in ('tyfza1_led_output', 'tyfza1_led_set', 'mode_store')) + cases)
    binary = path.with_suffix('')
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
        '-fsanitize=undefined', str(path), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PHY LED: auto/off/on, automatic-state restoration, unrelated bits and MDIO error propagation passed')
