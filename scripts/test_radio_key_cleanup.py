#!/usr/bin/env python3
"""Reproduce orphan CAM slots after successful DCTL unlink and failed CAM removal."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = [root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap',
           root / 'artifacts/live-radio-pe-rxq-5g']
common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define RTL_AX_BIT(n) BIT(n)
#define RTL_AX_AP_TRANSPORT_H
struct rtl_ax_io {void *ctx;u32(*read32)(void*,unsigned);void(*write32)(void*,unsigned,u32);};
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
static void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
#define RTL_AX_H2C_CMD 0
#define RTL_AX_H2C_DATA 1
#define HZ 100
#define WLAN_CIPHER_SUITE_CCMP 4
#define IEEE80211_KEY_FLAG_PAIRWISE BIT(0)
#define IEEE80211_KEY_FLAG_SW_MGMT_TX BIT(4)
#define dev_info(...) ((void)0)
#define for_each_set_bit(i,p,n) for(i=0;i<n;i++)if(p[i])
#define __set_bit(i,p) ((p)[i]=true)
#define __clear_bit(i,p) ((p)[i]=false)
#define test_bit(i,p) ((p)[i])
#define atomic64_read(p) (*(p))
enum set_key_cmd {SET_KEY,DISABLE_KEY};
struct ieee80211_key_conf {unsigned cipher,flags,keyidx,hw_key_idx;u8 key[16];u64 tx_pn;};
struct rtl_ax_station {unsigned macid;};
struct ieee80211_sta {struct rtl_ax_station drv_priv[1];};
struct ieee80211_vif {int dummy;};
struct rtl_ax_ap {struct rtl_ax_security_role security_roles[128];
 bool security_ids[128],security_deleting[128];int tx,cleanup_work;unsigned keys_installed,keys_removed;int key_result;};
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
static unsigned fail_remove,queued,clears;
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static unsigned find_first_zero_bit(bool *p,unsigned n){unsigned i;for(i=0;i<n&&p[i];i++);return i;}
static int rtl_ax_tx_flush_key(int tx,unsigned index){(void)tx;assert(index<128);return 0;}
static int __attribute__((unused)) rtl_ax_tx_ni_retire_peer(int tx,unsigned id){(void)tx;(void)id;return 0;}
static void wiphy_delayed_work_queue(void *w,int *work,unsigned delay){(void)w;(void)work;assert(delay==HZ);queued++;}
static int rtl_ax_command_flags(struct rtl_ax_ap *r,unsigned queue,unsigned cat,unsigned cls,unsigned func,
 const u8 *data,unsigned bytes,bool receive,bool done){
 (void)r;assert(cat==1 && !receive && done);
 if(cls==5){assert(queue==1&&func==9&&bytes==68);return 0;}
 assert(queue==0&&cls==10&&func==1&&bytes==24);
 if(!rtl_ax_ap_read32(data+4)) {clears++;if(fail_remove){fail_remove--;return -EIO;}}
 return 0;
}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,
 const u8 *data,unsigned len,bool done){return rtl_ax_command_flags(r,q,cat,cls,fn,data,len,false,done);}
'''
test = r'''
int main(void) {
 struct rtl_ax_ap r={};struct ieee80211_hw hw={&r,0};
 struct ieee80211_sta sta={{{7}}};struct ieee80211_vif vif={};
 struct ieee80211_key_conf key={.cipher=WLAN_CIPHER_SUITE_CCMP,.flags=IEEE80211_KEY_FLAG_PAIRWISE,.key={1}};
 for(unsigned n=0;n<128;n++) {
  assert(!rtl_ax_set_key(&hw,SET_KEY,&vif,&sta,&key));assert(key.hw_key_idx==n);
  fail_remove=1;assert(rtl_ax_set_key(&hw,DISABLE_KEY,&vif,&sta,&key)==-EIO);
  assert(!r.security_roles[7].valid && r.security_deleting[n] && r.security_ids[n]);
 }
 assert(queued==128 && rtl_ax_set_key(&hw,SET_KEY,&vif,&sta,&key)==-ENOSPC);
 fail_remove=128;assert(rtl_ax_security_cleanup(&r));
 assert(!r.keys_removed);
 assert(!rtl_ax_security_cleanup(&r));
 assert(r.keys_removed==128 && clears==384);
 for(unsigned n=0;n<128;n++)assert(!r.security_ids[n]&&!r.security_deleting[n]);
 assert(!rtl_ax_set_key(&hw,SET_KEY,&vif,&sta,&key)&&key.hw_key_idx==0);
 assert(!rtl_ax_set_key(&hw,DISABLE_KEY,&vif,&sta,&key));
 assert(r.keys_removed==129&&!r.security_deleting[0]&&!r.security_ids[0]);
}
'''
with tempfile.TemporaryDirectory(prefix='radio-key-cleanup-') as directory:
    path = Path(directory)
    for index, driver in enumerate(drivers):
        source = (driver / 'monitor.h').read_text()
        functions = ''
        for prefix, name in (('static bool ', 'rtl_ax_security_cleanup'), ('static int ', 'rtl_ax_set_key')):
            start = source.index(prefix + name + '(')
            end = source.index('\n}\n', start) + 3
            functions += source[start:end] + '\n'
        before, after = common.split('enum set_key_cmd', 1)
        c, binary = path / f'{index}.c', path / f'{index}'
        c.write_text(before + '#include "' + str(driver / 'security.h') + '"\n' +
                     'enum set_key_cmd' + after + functions + test)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
print('CAM cleanup: 128 failed deletes retained, failed retry retained, successful retry frees all slots; both drivers passed')
