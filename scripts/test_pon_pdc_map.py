#!/usr/bin/env python3
"""Run actual PDC initialization/refresh against modeled LLID and PDC tables."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-pon-pdc.c').read_text()


def function(name):
    start = re.search(r'^.*\b' + re.escape(name) + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "rtl9617c-pon-data.h"
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define dev_info(...) ((void)0)
struct mutex {bool held;};
struct rtl9617c_pon {void *mac,*dev;struct mutex lock,control_lock;bool mac_prepared,pdc_configured;int pdc_error;unsigned char pdc_source[64];};
static u32 registers[0xc000/4],maps[256][2];static unsigned int accesses,fail_access;
static void mutex_lock(struct mutex *lock) {assert(!lock->held);lock->held=true;}
static void mutex_unlock(struct mutex *lock) {assert(lock->held);lock->held=false;}
#define lockdep_assert_held(lock) assert((lock)->held)
static u32 readl(const void *p) {u32 value;memcpy(&value,p,4);return value;}
static void writel(u32 value,void *p) {
    unsigned int offset=(u32 *)p-registers;
    if(offset==0x9020/4) {
        if(++accesses==fail_access) {memcpy(p,&value,4);return;}
        unsigned int index=value&255;
        if(value&BIT(30)) {maps[index][0]=registers[0x9028/4];maps[index][1]=registers[0x9024/4];}
        value&=~BIT(31);
    }
    memcpy(p,&value,4);
}
static void rtl_pon_update(void *p,u32 mask,u32 value) {writel((readl(p)&~mask)|(value&mask),p);}
#define readl_poll_timeout(p,v,condition,delay,timeout) ({(v)=readl(p);(condition)?0:-ETIMEDOUT;})
'''
tests = r'''
int main(void) {
    struct rtl9617c_pon pon={.mac=registers,.control_lock={true},.mac_prepared=true};
    registers[(0x64+17*4)/4]=BIT(23)|(8<<24)|0x1234;
    registers[(0x64+20*4)/4]=BIT(23)|BIT(22)|0x4321;
    assert(rtl_pon_pdc_configure_locked(&pon)==0&&pon.pdc_configured&&accesses==256);
    for(unsigned int index=0;index<256;index++) {
        unsigned int entry=index&63;
        unsigned int expected=7;
        if(!(index&64))expected=entry==17?40:entry==20?32:32+(entry<32?entry:0);
        assert(((maps[index][0]>>9)&63)==7);
        assert(((maps[index][1]>>4)&511)==((index&64)?0:expected-32));
        assert(((maps[index][0]>>3)&63)==16 && (maps[index][0]&BIT(15)) && !(maps[index][1]&15));
        assert((maps[index][0]&7)==((index&64)?7:0));
    }
    /* Registration changes table slot17 from MAC8 to MAC3; only its two
     * business banks change, while MPCP/OAM remain in source7/COS7. */
    registers[(0x64+17*4)/4]=BIT(23)|(3<<24)|0x1234;
    pon.lock.held=true;
    unsigned int before=accesses;
    assert(rtl_pon_pdc_refresh(&pon)==0 && accesses==before+2);
    assert(((maps[17][1]>>4)&511)==3 && ((maps[145][1]>>4)&511)==3);
    assert(((maps[81][0]>>9)&63)==7 && ((maps[209][0]>>9)&63)==7);
    before=accesses;assert(rtl_pon_pdc_refresh(&pon)==0 && accesses==before);
    registers[(0x64+17*4)/4]=BIT(23)|(4<<24)|0x1234;
    fail_access=accesses+1;
    assert(rtl_pon_pdc_refresh(&pon)==-ETIMEDOUT && !pon.pdc_configured && pon.pdc_error==-ETIMEDOUT);
    pon.lock.held=false;fail_access=0;
    assert(rtl_pon_pdc_configure_locked(&pon)==0&&pon.pdc_configured);
    puts("Actual PDC: 256 entries, LLID slot-to-MAC mapping, business/control banks, changed-only refresh and table failure recovery passed");
}
'''
constants = '\n'.join(re.findall(r'^#define .*$', source, re.M))
code = model + constants + '\n' + ''.join(function(name) for name in ('rtl_pdc_access', 'rtl_pon_pdc_refresh', 'rtl_pon_pdc_configure_locked')) + tests
with tempfile.TemporaryDirectory(prefix='pdc-map-test-', dir=root / 'artifacts') as directory:
    directory = Path(directory)
    cfile = directory / 'pdc.c'
    binary = directory / 'pdc'
    cfile.write_text(code)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O1', '-g', '-fsanitize=address,undefined',
                    '-I', str(drivers), str(cfile), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/pon-pdc-map-test.json').write_text(json.dumps({'actual_functions':True,'entries':256,
    'LLID_table_MAC_index_used':True,'changed_only_refresh':True,'failure_recovery_tested':True,
    'hardware_access':False,'sanitizers':['ASan','UBSan']},indent=2)+'\n')
