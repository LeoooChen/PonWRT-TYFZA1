#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Reproduce cross-family status, LLID and stop effects in the actual control functions."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

project = Path(__file__).resolve().parents[1]
drivers = project / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=drivers / 'rtl9617c-pon.c')
parser.add_argument('--itu-stop', action='store_true', help='Start with the ITU stop reproducer')
args = parser.parse_args()
source = args.source.read_text()


def function(name):
    start = re.search(r'^(?:static )?(?:int|void|ssize_t) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include "rtl9617c-pon-mode.h"
typedef unsigned char u8;
#define READ_ONCE(value) (value)
struct net_device {int unused;};
struct device_attribute {int unused;};
struct rtl9617c_pon {
 int lock, control_lock, effects, cleanup_error;
 enum rtl_pon_mode mode;
 bool mpcp_enabled, fe_enabled, tx_disabled;
 unsigned int llid_count;
 unsigned char gpon_serial[8], xgpon_serial[8];
 unsigned int gpon_enabled, gpon_omcc_ready, gpon_omcc_onu_id, gpon_omcc_gem, gpon_ds_fec, gpon_recover;
 int gpon_activation_error;
 unsigned int gpon_last_irq;
 unsigned long long gpon_irq_count, gpon_key_switches;
 unsigned int xgpon_enabled, xgpon_omcc_ready, xgpon_omcc_id, xgpon_key_ready, xgpon_key_state;
 unsigned int xgpon_active_key, xgpon_recover, xgpon_last_transitions, xgpon_last_irq;
 int xgpon_activation_error;
 unsigned long long xgpon_irq_count, xgpon_key_reports, xgpon_key_tk4, xgpon_key_tk5, xgpon_deactivations;
};
struct device {struct rtl9617c_pon *pon;};
static struct rtl9617c_pon *dev_get_drvdata(struct device *dev) {return dev->pon;}
static void mutex_lock(int *lock) {assert(!*lock); *lock=1;}
static void mutex_unlock(int *lock) {assert(*lock); *lock=0;}
static int kstrtobool(const char *s, bool *value) {*value=s[0]=='1'; return 0;}
static int kstrtouint(const char *s, int base, unsigned int *value)
{(void)base; return sscanf(s,"%u",value)==1 ? 0 : -EINVAL;}
static int sysfs_emit(char *buf, const char *format, ...)
{va_list ap; va_start(ap,format); int length=vsnprintf(buf,4096,format,ap); va_end(ap); return length;}
static int effect(struct rtl9617c_pon *pon) {assert(pon->control_lock); pon->effects++; return 0;}
static int rtl_pon_epon_traffic_locked(struct rtl9617c_pon *pon, bool enable)
{pon->tx_disabled=!enable; pon->fe_enabled=enable; return effect(pon);}
static void rtl_pon_mpcp_stop_locked(struct rtl9617c_pon *pon)
{pon->fe_enabled=false; effect(pon);}
static int rtl_pon_fe_configure_locked(struct rtl9617c_pon *pon, bool enable)
{assert(pon->control_lock && !enable); return pon->cleanup_error;}
static int rtl_pon_mpcp_configure(struct rtl9617c_pon *pon)
{(void)pon; return -EOPNOTSUPP;}
static int rtl_pon_gpon_activation_locked(struct rtl9617c_pon *pon, bool enable)
{(void)enable; return effect(pon);}
static int rtl_pon_xgpon_activation_locked(struct rtl9617c_pon *pon, bool enable)
{(void)enable; return effect(pon);}
static int rtl_pon_oam_resize(struct rtl9617c_pon *pon, unsigned int count)
{(void)count; return effect(pon);}
static int rtl_pon_mpcp_state(struct rtl9617c_pon *pon, char *buf)
{(void)buf; pon->effects++; return 1;}
static int rtl_pon_llid_table(struct rtl9617c_pon *pon, char *buf)
{(void)buf; pon->effects++; return 1;}
static int rtl_pon_llid_update(struct rtl9617c_pon *pon, bool add, unsigned int index,
 unsigned int mac, unsigned int llid, unsigned int multicast)
{(void)add; (void)index; (void)mac; (void)llid; (void)multicast; return effect(pon);}
static int rtl_pon_pdc_refresh(struct rtl9617c_pon *pon) {return effect(pon);}
static void expect(ssize_t value, ssize_t expected, const char *reason)
{if(value!=expected) {fprintf(stderr,"%s: got %zd, expected %zd\n",reason,value,expected); exit(1);}}
'''

test = r'''
int main(void)
{
 for (int mode=FIRST_MODE; mode<RTL_PON_MODE_COUNT; mode++) {
  struct rtl9617c_pon pon={.mode=mode,.llid_count=1,.fe_enabled=true,
   .gpon_activation_error=-EIO,.xgpon_activation_error=-ENOLINK};
  struct device dev={&pon}; char buf[4096];
  if (rtl_pon_mode_is_epon(mode)) {
   expect(activation_show(&dev,NULL,buf),-EOPNOTSUPP,"EPON exposed stale GPON activation error");
   expect(active_serial_number_show(&dev,NULL,buf),-EOPNOTSUPP,"EPON exposed stale ITU SN");
   expect(activation_store(&dev,NULL,"0",1),-EOPNOTSUPP,"EPON dispatched ITU deactivation");
   assert(!pon.effects);
   expect(rtl_pon_epon_traffic(&pon,false),0,"EPON traffic stop failed");
   assert(pon.tx_disabled && !pon.fe_enabled && pon.effects==1);
   expect(mpcp_store(&dev,NULL,"0",1),1,"EPON MPCP stop failed");
   assert(pon.effects==2);
   expect(llid_count_store(&dev,NULL,"2",1),1,"EPON LLID resize failed");
   assert(pon.llid_count==2 && pon.effects==3);
   expect(llid_table_store(&dev,NULL,"add 1 0 1234 0",14),14,"EPON LLID update failed");
   assert(pon.effects==5);
   pon.fe_enabled=true;pon.cleanup_error=-EIO;
   expect(mpcp_store(&dev,NULL,"0",1),-EIO,"MPCP stop hid FE cleanup failure");
   assert(pon.effects==6);
  } else {
   expect(rtl_pon_epon_traffic(&pon,false),-EOPNOTSUPP,"EPON stop touched active ITU laser/FE");
   expect(mpcp_store(&dev,NULL,"0",1),-EOPNOTSUPP,"MPCP stop touched ITU forwarding");
   expect(llid_count_store(&dev,NULL,"2",1),-EOPNOTSUPP,"LLID resize changed ITU data devices");
   expect(llid_count_show(&dev,NULL,buf),-EOPNOTSUPP,"ITU exposed an EPON LLID count");
   expect(llid_table_store(&dev,NULL,"add 1 0 1234 0",14),-EOPNOTSUPP,"ITU programmed EPON LLID RAM");
   expect(llid_table_show(&dev,NULL,buf),-EOPNOTSUPP,"ITU read EPON LLID RAM");
   expect(mpcp_show(&dev,NULL,buf),-EOPNOTSUPP,"ITU read EPON MPCP registers");
   assert(!pon.effects && pon.fe_enabled && !pon.tx_disabled && pon.llid_count==1);
   assert(activation_show(&dev,NULL,buf)>0);
   assert(strstr(buf,mode==RTL_PON_GPON ? "error=-5" : "error=-67"));
   expect(activation_store(&dev,NULL,"0",1),1,"ITU activation control failed");
   assert(pon.effects==1);
  }
  assert(!pon.lock && !pon.control_lock);
 }
 puts("Family control: inactive-family state is unavailable; cross-family stop/LLID writes have no hardware or netdev effects; active-family operations and actual errors are retained");
}
'''

names = ('rtl_pon_epon_traffic', 'rtl_pon_mpcp_stop', 'activation_show', 'activation_store',
         'llid_count_show', 'llid_count_store', 'mpcp_show', 'mpcp_store',
         'llid_table_show', 'llid_table_store', 'active_serial_number_show')
with tempfile.TemporaryDirectory(prefix='pon-family-', dir=project / 'artifacts') as directory:
    directory = Path(directory)
    cfile, binary = directory / 'family.c', directory / 'family'
    cfile.write_text(model + ''.join(function(name) for name in names) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-sign-compare',
                    '-DFIRST_MODE=' + ('3' if args.itu_stop else '0'),
                    '-I', str(drivers), str(cfile), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
