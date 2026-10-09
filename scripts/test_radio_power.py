#!/usr/bin/env python3
"""Compare native AP power with the actual GPL functions for both radios."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
base = root / 'evidence/rtl-workspace/downloads/reference/phoebus-sdk-20261005/vendor/realtek-wireless/g6_wifi_driver/phl/hal_g6/mac'
native = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/power.h'
references = [base / 'mac_reg_ax.h', base / 'mac_ax/hw.c', base / 'mac_ax/efuse.c']

def function(path, name):
    text = path.read_text()
    start = re.search(r'^(?:static )?(?:u32|void) ' + name + r'\([^;{]*\)\s*\{', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'

common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "power.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define SET_CLR_WORD(v,w,f) (((v)&~((f##_MSK)<<(f##_SH)))|(((w)&(f##_MSK))<<(f##_SH)))
'''
registers = (base / 'mac_reg_ax.h').read_text()
common += '\n'.join(line for line in registers.splitlines() if line.startswith('#define '))
common += r'''
#define MACSUCCESS 0
#define MACPOLLTO 1
#define MACEFUSEREAD 2
#define MACCHIPID 3
#define MACNOITEM 4
#define MACBUFALLOC 5
#define MAC_AX_EFUSE_BANK_WIFI 0
#define MAC_AX_CHIP_ID_8852C 2
#define MAC_AX_CHIP_ID_8192XB 3
#define MAC_AX_CHIP_ID_8852B 4
#define MAC_AX_CHIP_ID_8851B 5
#define MAC_AX_CHIP_ID_8851E 6
#define MAC_AX_CHIP_ID_8852D 7
#define MAC_AX_CHIP_ID_8852A 8
#define MAC_AX_CHIP_ID_8852BT 9
#define MAC_AX_8852C_SUPPORT 1
#define MAC_AX_8192XB_SUPPORT 1
#define CHIP_WIFI6_8192XB 1
#define CHIP_WIFI6_8832BR 2
#define PLTFM_GET_CHIP_ID(...) CHIP_WIFI6_8192XB
#define EF_FV_OFSET_AX 0x5ea
#define EF_AID_OFSET_AX 0x5f0
#define EF_CV_SH 4
#define EF_CV_MSK 15
#define EF_FV_SH 0
#define EF_FV_MSK 15
#define FV_TYPE1 15
#define FV_TYPE2 14
#define FV_TYPE3 12
enum rtw_fv {FTV,FUV,FSV};
#define PLTFM_MALLOC malloc
#define PLTFM_FREE(p,n) free(p)
#define MAC_AX_PWR_ON 1
#define MAC_AX_PLAT_ON 1
#define MAC_AX_IO_ST_NORM 1
#define MAC_AX_FUNC_ON 1
#define PWR_K_CHK_OFFSET 0x5e9
#define PROCESS_CHK_OFFSET 0x5ea
#define PWR_K_CHK_VALUE 0xaa
#define PROCESS_CHK_T 0xff
#define UNLOCK_CODE 0x69
#define XTAL_SI_POLLING_CNT 1000
#define XTAL_SI_POLLING_DLY_US 50
#define XTAL_SI_NORMAL_WRITE 0
#define XTAL_SI_ANAPAR_WL 0x90
#define XTAL_SI_WL_RFC_S0 0x80
#define XTAL_SI_WL_RFC_S1 0x81
#define XTAL_SI_XTAL_XMD_2 0x24
#define XTAL_SI_XTAL_XMD_4 0x26
#define EFUSE_WAIT_CNT_PLUS 1000000
#define PLTFM_MSG_ERR(...) ((void)0)
#define PLTFM_MSG_ALWAYS(...) ((void)0)
static unsigned char memory[65536],efuse[1536];
struct event {unsigned int kind,reg,value;};
static struct event trace[512];static unsigned int used;
static unsigned int fail_reg,fail_mask,xtal_count,fail_xtal;
static bool fail_efuse;
static void event(unsigned int kind,unsigned int reg,unsigned int value){
 assert(used<512);trace[used++]=(struct event){kind,reg,value};
}
static u8 r8(unsigned int reg){assert(reg<sizeof(memory));return memory[reg];}
static u16 r16(unsigned int reg){u16 v;assert(reg+2<=sizeof(memory));memcpy(&v,memory+reg,2);return v;}
static u32 r32(unsigned int reg){
 u32 v;assert(reg+4<=sizeof(memory));memcpy(&v,memory+reg,4);
 if(reg==RTL_AX_XTAL && xtal_count!=fail_xtal)v&=~BIT(31);
 if(reg==RTL_AX_EFUSE){unsigned int address=(v>>16)&0x7ff;
  assert(address<sizeof(efuse));v=(v&~255U)|efuse[address];if(!fail_efuse)v|=BIT(29);}
 return v;
}
static void w8(unsigned int reg,u8 v){event(8,reg,v);memory[reg]=v;}
static void w16(unsigned int reg,u16 v){event(16,reg,v);memcpy(memory+reg,&v,2);}
static void w32(unsigned int reg,u32 v){
 /* GPL's final LED1 expression discards its result and writes the unchanged
  * value. It has no register effect; the native AP sequence omits that no-op. */
 if(reg!=0x2dc)event(32,reg,v);
 if(reg==RTL_AX_XTAL)xtal_count++;
 memcpy(memory+reg,&v,4);
}
static void delay(unsigned int us){event(0,0,us);}
#define MAC_REG_R8 r8
#define MAC_REG_R16 r16
#define MAC_REG_R32 r32
#define MAC_REG_W8 w8
#define MAC_REG_W16 w16
#define MAC_REG_W32 w32
#define PLTFM_DELAY_US delay
#define PLTFM_DELAY_MS(ms) delay((ms)*1000)
enum rtw_dv_sel {DDV,DAV};
struct mac_ax_intf_ops {};
struct mac_ax_adapter;
struct mac_ax_priv_ops {
 void (*enable_efuse_sw_pwr_cut)(struct mac_ax_adapter *,bool);
 void (*disable_efuse_sw_pwr_cut)(struct mac_ax_adapter *,bool);
};
struct mac_ax_ops {
 u32 (*read_efuse)(struct mac_ax_adapter *,u32,u32,u8 *,int);
};
static struct mac_ax_ops reference_mac_ops;
#define adapter_to_mac_ops(a) (&reference_mac_ops)
struct mac_bank_efuse_info {u32 *efuse_start;};
struct mac_ax_adapter {
 struct mac_ax_hw_info {unsigned int chip_id,cv,fv,aid,dav_full_efuse_size;} *hw_info;
 struct {int pwr,plat,io_st,dmac_func,cmac0_func;} sm;
 struct {struct mac_bank_efuse_info bank_efuse_info;enum rtw_dv_sel dv_sel;
  u16 read_efuse_cnt,efuse_ctrl;} efuse_param;
 struct mac_ax_priv_ops *priv;
};
#define adapter_to_intf_ops(a) ((struct mac_ax_intf_ops *)0)
#define adapter_to_priv_ops(a) ((a)->priv)
#define is_chip_id(a,id) ((a)->hw_info->chip_id==(id))
static int enable_efuse_pwr_cut_dav(struct mac_ax_adapter *a,bool wr){assert(0);return 0;}
static int disable_efuse_pwr_cut_dav(struct mac_ax_adapter *a,bool wr){assert(0);return 0;}
static int read_hw_efuse_dav(struct mac_ax_adapter *a,u32 o,u32 n,u8 *p){assert(0);return 0;}
/* These queries only update software identity fields; their implementation
 * is outside the power-sequence trace, and is not claimed by this test. */
static u32 get_fv_info(struct mac_ax_adapter *a){return 0;}
static u32 get_aid_info(struct mac_ax_adapter *a){return 0;}
static bool chk_patch_ck_buf_level(struct mac_ax_adapter *a){return false;}
static u32 pwr_poll_u32(struct mac_ax_adapter *a,u32 reg,u32 mask,u32 expected){
 event(1,reg,(mask<<1)|!!expected);
 if(reg==fail_reg && mask==fail_mask)return MACPOLLTO;
 u32 v=r32(reg);v=(v&~mask)|expected;memcpy(memory+reg,&v,4);return 0;
}
'''
common += function(base / 'mac_ax/hw.c', 'mac_write_xtal_si')
for family in ('8852c', '8192xb'):
    path = base / f'mac_ax/mac_{family}/efuse_{family}.c'
    references.append(path)
    for operation in ('enable', 'disable'):
        common += function(path, f'{operation}_efuse_sw_pwr_cut_{family}')
common += function(base / 'mac_ax/efuse.c', 'read_hw_efuse')
common += r'''
static u32 mac_read_efuse_plus(struct mac_ax_adapter *a,u32 address,u32 n,u8 *p,int bank){
 assert(bank==MAC_AX_EFUSE_BANK_WIFI && address+n<=1536);
 return read_hw_efuse(a,address,n,p);
}
'''
for name in ('get_fv_info', 'get_aid_info'):
    common += function(base / 'mac_ax/efuse.c', name).replace(name, 'reference_' + name)
for family in ('8852c', '8192xb'):
    path = base / f'mac_ax/mac_{family}/pwr_seq_func_{family}.c'
    references.append(path)
    for operation in ('on', 'off'):
        common += function(path, f'mac_pwr_{operation}_ap_pcie_{family}')
unit = r'''
static unsigned char nr8(void *c,unsigned int r){return r8(r);}
static unsigned short nr16(void *c,unsigned int r){return r16(r);}
static unsigned int nr32(void *c,unsigned int r){return r32(r);}
static void nw8(void *c,unsigned int r,unsigned char v){w8(r,v);}
static void nw16(void *c,unsigned int r,unsigned short v){w16(r,v);}
static void nw32(void *c,unsigned int r,unsigned int v){w32(r,v);}
static void ndelay(void *c,unsigned int us){delay(us);}
static int npoll(void *c,unsigned int reg,unsigned int mask,unsigned int expected,
 unsigned int us,unsigned int limit,unsigned int *last){
 if(reg==RTL_AX_POWER){
  int ret=pwr_poll_u32(NULL,reg,mask,expected);*last=r32(reg);return ret?-ETIMEDOUT:0;
 }
 *last=r32(reg);
 if((*last&mask)!=expected)return -ETIMEDOUT;
 return 0;
}
static const struct rtl_ax_io io={NULL,nr8,nr16,nr32,nw8,nw16,nw32,ndelay,npoll};
static void prepare(unsigned int vector){
 used=xtal_count=0;
 for(unsigned int n=0;n<sizeof(memory);n++)memory[n]=(n*37+vector*19)&255;
 u32 p=r32(RTL_AX_POWER)|BIT(17);memcpy(memory+RTL_AX_POWER,&p,4);
}
int main(void){
 unsigned int cases=0;u32 start=0;
 struct mac_ax_hw_info identity={.dav_full_efuse_size=128};
 struct mac_ax_priv_ops priv={};struct mac_ax_adapter a={.priv=&priv};
 a.hw_info=&identity;reference_mac_ops.read_efuse=mac_read_efuse_plus;
 a.efuse_param.bank_efuse_info.efuse_start=&start;
 a.efuse_param.efuse_ctrl=RTL_AX_EFUSE;a.efuse_param.read_efuse_cnt=100;
 struct event expected[512];unsigned char final[65536];
 for(unsigned int chip=RTL_AX_8852C;chip<=RTL_AX_8192XB;chip++)
  for(unsigned int tag=0;tag<2;tag++)for(unsigned int process=0;process<2;process++)
   for(unsigned int vector=0;vector<128;vector++) {
    identity.chip_id=chip==RTL_AX_8852C?2:3;
    efuse[0x5e9]=tag?0xaa:0xff;efuse[0x5ea]=process?0xff:0;
    priv.enable_efuse_sw_pwr_cut=chip==RTL_AX_8852C?
     enable_efuse_sw_pwr_cut_8852c:enable_efuse_sw_pwr_cut_8192xb;
    priv.disable_efuse_sw_pwr_cut=chip==RTL_AX_8852C?
     disable_efuse_sw_pwr_cut_8852c:disable_efuse_sw_pwr_cut_8192xb;
    if(chip==RTL_AX_8192XB){
    prepare(vector);
    assert(!(chip==RTL_AX_8852C?mac_pwr_on_ap_pcie_8852c(&a):mac_pwr_on_ap_pcie_8192xb(&a)));
    unsigned int count=used;memcpy(expected,trace,used*sizeof(*trace));memcpy(final,memory,sizeof(memory));
    prepare(vector);struct rtl_ax_calibration cal={};
    assert(!rtl_ax_ap_power_on(&io,chip,&cal));
    if(used!=count || memcmp(expected,trace,used*sizeof(*trace))) {
     printf("mismatch chip=%x tag=%u process=%u vector=%u original=%u native=%u\n",chip,tag,process,vector,count,used);
     for(unsigned int i=0;i<count || i<used;i++)if(memcmp(expected+i,trace+i,sizeof(*trace)))
      printf("%u expected %u/%x/%x native %u/%x/%x\n",i,expected[i].kind,expected[i].reg,expected[i].value,trace[i].kind,trace[i].reg,trace[i].value);
     return 1;
    }
    assert(!memcmp(final,memory,sizeof(memory)));assert(cal.tag==efuse[0x5e9]);cases++;
    assert(cal.process==efuse[0x5ea]);
    }
    prepare(vector);
    assert(!(chip==RTL_AX_8852C?mac_pwr_off_ap_pcie_8852c(&a):mac_pwr_off_ap_pcie_8192xb(&a)));
    unsigned int count=used;memcpy(expected,trace,used*sizeof(*trace));memcpy(final,memory,sizeof(memory));
    prepare(vector);assert(!rtl_ax_ap_power_off(&io));
    assert(used==count && !memcmp(expected,trace,used*sizeof(*trace)));
    assert(!memcmp(final,memory,sizeof(memory)));cases++;
   }
 /* Inject each power/XTAL handshake failure into production sequencing. */
 for(unsigned int chip=RTL_AX_8852C;chip<=RTL_AX_8192XB;chip++) {
  fail_reg=RTL_AX_POWER;fail_mask=BIT(17);prepare(0);struct rtl_ax_calibration cal={};
  assert(rtl_ax_ap_power_on(&io,chip,&cal)==-ETIMEDOUT);
  fail_mask=BIT(8);prepare(0);assert(rtl_ax_ap_power_on(&io,chip,&cal)==-ETIMEDOUT);
  fail_mask=BIT(9);prepare(0);assert(rtl_ax_ap_power_off(&io)==-ETIMEDOUT);
  fail_reg=fail_mask=0;
  for(unsigned int n=1;n<=10;n++) {
   fail_xtal=n;prepare(0);assert(rtl_ax_ap_power_on(&io,chip,&cal)==-ETIMEDOUT);
   prepare(0);assert(rtl_ax_ap_power_off(&io)==-ETIMEDOUT);
  }
 }
 fail_xtal=0;
 unsigned int identity_cases=0;
 for(unsigned int chip=RTL_AX_8852C;chip<=RTL_AX_8192XB;chip++)
  for(unsigned int cv=0;cv<16;cv++)for(unsigned int fv=1;fv<256;fv++) {
   identity.chip_id=chip==RTL_AX_8852C?2:3;identity.cv=cv;identity.fv=255;
   priv.enable_efuse_sw_pwr_cut=chip==RTL_AX_8852C?
    enable_efuse_sw_pwr_cut_8852c:enable_efuse_sw_pwr_cut_8192xb;
   priv.disable_efuse_sw_pwr_cut=chip==RTL_AX_8852C?
    disable_efuse_sw_pwr_cut_8852c:disable_efuse_sw_pwr_cut_8192xb;
   efuse[0x5ea]=fv;efuse[0x5f0]=fv&1?255:0x34;efuse[0x5f1]=fv&1?255:0x12;
   prepare(0);reference_get_fv_info(&a);assert(!reference_get_aid_info(&a));
   prepare(0);struct rtl_ax_identity actual={.hardware_cv=cv};struct rtl_ax_calibration cal={};
   assert(!rtl_ax_read_identity(&io,chip,&cal,&actual));
   assert(actual.cv==identity.cv && actual.adie==identity.aid);
   assert(actual.factory==(identity.fv==255?'?':"TUS"[identity.fv]));
   assert(cal.process==fv);identity_cases++;
  }
 fail_efuse=true;prepare(0);unsigned char value=0x5a;
 assert(rtl_ax_efuse_read(&io,0x5e9,&value)==-ETIMEDOUT && value==0x5a);
 assert(!(r16(RTL_AX_ISO)&(BIT(14)|BIT(15))));
 assert((r16(RTL_AX_ISO)&BIT(8)) && !(r8(RTL_AX_DEBUG)&BIT(2)));
 used=0;assert(rtl_ax_efuse_read(&io,1536,&value)==-EINVAL && !used);
 struct rtl_ax_calibration cal={};assert(rtl_ax_ap_power_on(&io,0x50,&cal)==-ENODEV && !used);
 printf("Native AP power:%u GPL register traces;both chips,calibrated/uncalibrated,process/ADIE variants;handshake failures and efuse release passed\n",cases);
 printf("Native identity:%u GPL CV/FV/AID vectors;efuse CV override precedes firmware selection\n",identity_cases);
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-power-') as directory:
    path = Path(directory)
    source, binary = path / 'power.c', path / 'power'
    source.write_text(common + unit)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', '-I' + str(native.parent),
                    str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/radio-power-test.json').write_text(json.dumps({
    'native_source_sha256': hashlib.sha256(native.read_bytes()).hexdigest(),
    'references': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in references},
    'original_on_off_trace_vectors': 1536, 'chips': ['RTL8852C', 'RTL8192XB'],
    'GPL_power_on_chips': ['RTL8192XB'], 'stock_power_on_test': 'scripts/test_radio_stock_power.py',
    'GPL_identity_vectors': 8160, 'stock_zero_FV_exception_test': 'scripts/test_radio_stock_power.py',
    'calibration_and_process_and_ADIE_branches': True,
    'get_fv_and_get_aid_queries_in_power_oracle': False,
    'original_no_effect_LED1_write_omitted': True,
    'power_and_XTAL_timeouts': True, 'efuse_timeout_releases_read_window': True,
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
