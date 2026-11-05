#!/usr/bin/env python3
"""Check factory power policy and Linux caps using real board power files.

Execute the original ARM64 capability selection and MIB defaults. Compile
the production parsers, TPU writer and readback verifier; only firmware IO,
MMIO and TX-shape programming are substituted. This is not an RF test.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--firmware-root', type=Path, default=root / 'sources/openwrt/files/lib/firmware/realtek/tyfza1')
parser.add_argument('--power-source', type=Path, default=driver / 'power-tables.c')
args = parser.parse_args()
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

module = root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko'
assert hashlib.sha256(module.read_bytes()).hexdigest() == (
    '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e')
factory_enables = []


def callback(name, values):
    if name in ('printk', 'rtw_load_phy_file_path'):
        return 0
    if name == 'dev_get_band_cap':
        return band
    if name == 'rtw_hal_mac_write_pwr_limit_en':
        factory_enables.append(oracle.unpack(hal + 1122 + 344 * values[1], 'B')[0])
        return 0
    if name == 'memset':
        oracle.uc.mem_write(values[0], bytes([values[1]]) * values[2])
        return values[0]
    raise AssertionError((name, values))


oracle = ModuleOracle(module, ['phl_sw_cap_init', 'rtw_core_update_default_setting',
    'rtw_hal_final_cap_decision', 'halrf_get_efuse_power_table_switch',
    'set_rtw_mib_default_tbl'], callback)
adapter, phl, hal, info, rf, device = [0x6000000 + i * 0x100000 for i in range(6)]
for address in (adapter, phl, hal, info, rf, device):
    oracle.uc.mem_map(address, 0x100000)
for band in (1, 2):  # Original band capability masks: 2G and 5G.
    for address in (adapter, phl, hal, info, rf, device):
        oracle.uc.mem_write(address, bytes(0x100000))
    oracle.word(adapter + 4552, phl)
    oracle.word(adapter + 0x145e0, device)
    oracle.word(info + 8, hal)
    oracle.word(rf, phl)
    oracle.word(rf + 8, hal)
    oracle.call('phl_sw_cap_init', phl)
    oracle.call('rtw_core_update_default_setting', adapter)
    oracle.call('rtw_hal_final_cap_decision', phl, info)
    oracle.call('halrf_get_efuse_power_table_switch', rf, 0)
    # core_mib_power_limit reads adapter+0x9824 and uses its boolean value.
    oracle.call('set_rtw_mib_default_tbl', adapter)
    assert oracle.unpack(adapter + 0x9824, 'B')[0] == 0
assert factory_enables == [0, 0], factory_enables

source = r'''
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include "power.h"
typedef uint8_t u8; typedef int8_t s8; typedef uint16_t u16; typedef uint32_t u32;
#define RTL_AX_PHY_STAGES 2
#define RTL_AX_PHY_BASE 0
#define RTL_AX_PHY_FEM 1
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define BIT(n) (1U << (n))
#define min(a,b) ((a)<(b)?(a):(b))
#define GFP_KERNEL 0
#define strscpy(d,s,n) snprintf(d,n,"%s",s)
struct device {};
struct firmware {size_t size;char *data;};
static const char *firmware_root;
static int request_firmware(const struct firmware **fw,const char *name,struct device *dev){
 char path[1024];snprintf(path,sizeof(path),"%s/%s",firmware_root,name+strlen("realtek/tyfza1/"));
 FILE *f=fopen(path,"rb");assert(f);
 struct firmware *r=calloc(1,sizeof(*r));fseek(f,0,SEEK_END);r->size=ftell(f);rewind(f);
 r->data=malloc(r->size);assert(fread(r->data,1,r->size,f)==r->size);fclose(f);*fw=r;return 0;
}
static void release_firmware(const struct firmware *fw){free(fw->data);free((void *)fw);}
static char *kmemdup_nul(const void *p,size_t n,int flags){char *out=malloc(n+1);memcpy(out,p,n);out[n]=0;return out;}
#define kfree free
#define kvfree free
#define kvmalloc_array(n,s,f) malloc((n)*(s))
static char *skip_spaces(const char *s){while(isspace(*s))s++;return (char *)s;}
static char *strim(char *s){s=skip_spaces(s);char *end=s+strlen(s);while(end>s && isspace(end[-1]))*--end=0;return s;}
static int kstrtoint(const char *s,unsigned base,int *v){char *e;*v=strtol(s,&e,base);return *e?-EINVAL:0;}
static int kstrtouint(const char *s,unsigned base,unsigned *v){char *e;*v=strtoul(s,&e,base);return *e?-EINVAL:0;}
static int kstrtou8(const char *s,unsigned base,u8 *v){unsigned n;int r=kstrtouint(s,base,&n);*v=n;return r;}
#define dev_err(d,...) fprintf(stderr,__VA_ARGS__)
static int dev_err_probe(struct device *d,int ret,const char *fmt,...){return ret;}
static void rtl_ax_tx_shape_zero(const struct rtl_ax_io *io,unsigned chip){}
static u32 registers[0x20000/4];
static u32 read32(void *p,unsigned r){return registers[r/4];}
static void write32(void *p,unsigned r,u32 v){registers[r/4]=v;}
'''
for name in ('limits.h', 'power-tables.h', 'limits.c', 'power-tables.c'):
    path = args.power_source if name == 'power-tables.c' else driver / name
    source += '\n'.join(line for line in path.read_text().splitlines()
                        if not line.startswith('#include')) + '\n'
source += r'''
#define IEEE80211_CONF_CHANGE_POWER 1
#define IEEE80211_CONF_CHANGE_CHANNEL 2
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
struct ieee80211_vif {};
struct rtl_ax_ap {
 struct rtl_ax_io io;struct rtl_ax_power_inputs power_inputs;
 struct rtl_ax_power_hardware power_hardware;
 unsigned chip;int command_mutex,requested_tx_dbm;bool radar_required;
 char power_country[3];
 struct {unsigned band,primary,center,bandwidth;bool configured;}channel;
};
struct ieee80211_hw {
 struct rtl_ax_ap *radio;
 struct {int power_level,chandef;bool radar_enabled;}conf;
};
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static int rtl_ax_chandef_locked(struct rtl_ax_ap *r,int *def){assert(0);return 0;}
'''
monitor = (driver / 'monitor.h').read_text()
for name in ('rtl_ax_monitor_config', 'rtl_ax_monitor_get_txpower'):
    start = monitor.rfind('\n', 0, monitor.index(name + '(')) + 1
    end = monitor.index('{', start) + 1
    depth = 1
    while depth:
        depth += (monitor[end] == '{') - (monitor[end] == '}')
        end += 1
    source += monitor[start:end] + '\n'
source += r'''
static int half_dbm(u8 v){return v&64?(int)v-128:v;}
int main(int argc,char **argv){
 const char *countries[]={"CN","00","US"};
 unsigned cases=0;
 firmware_root=argv[1];struct rtl_ax_io io={.read32=read32,.write32=write32};
 for(unsigned chip=RTL_AX_8852C;chip<=RTL_AX_8192XB;chip++){
  struct rtl_ax_ap radio={.io=io,.chip=chip};
  struct ieee80211_hw mac={.radio=&radio};
  struct rtl_ax_power_inputs *in=&radio.power_inputs;
  struct rtl_ax_power_hardware *hw=&radio.power_hardware;
  assert(!rtl_ax_power_inputs_load(NULL,chip,in));
  unsigned band=chip==RTL_AX_8852C;
  for(unsigned bw=0;bw<(band?4U:2U);bw++)
   for(unsigned country=0;country<ARRAY_SIZE(countries);country++)
    for(int cap=0;cap<=30;cap++){
     unsigned primary=band?36:6,center=band?(bw==3?50:bw==2?42:bw==1?38:36):(bw?8:6);
     /* Include old-firmware enabled bits and unrelated nonzero fields. */
     registers[0xd208/4]=0xa5b5a5a5;registers[0xd21c/4]=0xa5a5a5a5;
     radio.channel.band=band;radio.channel.primary=primary;radio.channel.center=center;
     radio.channel.bandwidth=bw;radio.channel.configured=true;
     strcpy(radio.power_country,countries[country]);
     mac.conf.power_level=cap;
     assert(!rtl_ax_monitor_config(&mac,0,IEEE80211_CONF_CHANGE_POWER));
     assert(registers[0xd208/4]==(0xa5b5a5a5U&~0x00300000U));
     assert(registers[0xd21c/4]==(0xa5a5a5a5U&~0x00040000U));
     int reported=-1;
     assert(!rtl_ax_monitor_get_txpower(&mac,NULL,0,&reported)&&reported==cap);
     for(unsigned i=0;i<sizeof(hw->rate);i++)assert(half_dbm(hw->rate[i])<=cap*2);
     /* Requests above the factory targets must not boost OFDM6 or
      * either stream's HE MCS11. Keep the original mode offsets too. */
     const struct rtl_ax_power_stage *factory=&in->stage[RTL_AX_PHY_FEM];
     assert(half_dbm(hw->rate[4])==min(factory->rate[band][4],cap*4)/2);
     assert(half_dbm(hw->rate[23])==min(factory->rate[band][95],cap*4)/2);
     assert(half_dbm(hw->rate[39])==min(factory->rate[band][107],cap*4)/2);
     u32 offsets=0;
     for(unsigned i=0;i<5;i++)
      offsets|=((factory->rate[i==4?0:band][140+i]/2)&15)<<(i*4);
     assert((registers[0xd204/4]&0x000fffff)==offsets);
     assert(hw->limit_dbm==cap);
     registers[0xd208/4]|=0x00300000;
     assert(rtl_ax_monitor_get_txpower(&mac,NULL,0,&reported)==-EIO);
     assert(!rtl_ax_monitor_config(&mac,0,IEEE80211_CONF_CHANGE_POWER));
     registers[0xd21c/4]|=0x00040000;
     assert(rtl_ax_monitor_get_txpower(&mac,NULL,0,&reported)==-EIO);
     if(!bw&&!country&&(cap==23||cap==30)){
      printf("chip=%02x Linux_cap=%ddBm OFDM6=%.1fdBm optional_vendor_OFDM_limit=%.1f/%.1fdBm\n",
       chip,cap,half_dbm(hw->rate[4])/2.0,half_dbm(hw->limit20[4])/2.0,half_dbm(hw->limit20[44])/2.0);
     }
     cases++;
    }
  rtl_ax_power_inputs_release(in);
 }
 printf("PASS: %u actual-table power cases; both radios, all supported widths, CN/world/US, requested caps 0-30, no factory-target boost, stale gate detection\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-power-policy-') as folder:
    work = Path(folder)
    (work / 'native.c').write_text(source)
    subprocess.run(['cc', '-O2', '-Wall', '-Werror', '-I' + str(driver),
                    str(work / 'native.c'), '-o', str(work / 'native')], check=True)
    subprocess.run([str(work / 'native'), str(args.firmware_root)], check=True)
print('PASS: original ARM64 capability selection and power_limit MIB disable optional vendor limits on both radios')
