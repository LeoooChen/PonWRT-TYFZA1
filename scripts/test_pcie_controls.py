#!/usr/bin/env python3
"""Compare production PHY masks with the exact Taurus GPL power function."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
reference = root / 'evidence/pcie-native-reference-20261009/phy-rtk-taurus.c'
native = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/phy/phy-rtl9617c-pcie.c'
stock, current = reference.read_text(), native.read_text()
def function(source, name):
    start = re.search(r'^static (?:int|void) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
typedef uint32_t u32;
#define __iomem
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) (((~0U)<<(l))&((~0U)>>(31-(h))))
enum {PHY_S0_PCIE0,PHY_S0_PCIE1,PHY_S1_PCIE0,PHY_S1_PCIE1,PHY_S2_PCIE0,
 PHY_S2_USB,PHY_S3_USB,PHY_S3_SGMII,PHY_S3_HSGMII,PHY_S0_RXAUI};
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define mutex_lock(...) ((void)0)
#define mutex_unlock(...) ((void)0)
#define spin_lock_irqsave(l,f) do{(void)(l);(f)=0;}while(0)
#define spin_unlock_irqrestore(l,f) do{(void)(l);(void)(f);}while(0)
static u32 readl(void *reg){return *(u32 *)reg;}
static void writel(u32 value,void *reg){*(u32 *)reg=value;}
static int debug;
struct rtk_taurus_phy_core {void *dev,*reg_ctrl,*reg_pwr;int lock;};
struct rtk_taurus_phy {struct rtk_taurus_phy_core *core;unsigned int id;};
static int rtk_taurus_phy_serdes_config(struct rtk_taurus_phy *phy,bool on){(void)phy;(void)on;return 0;}
static int rtk_taurus_phy_sgmii_config(struct rtk_taurus_phy *phy,bool on){(void)phy;(void)on;return 0;}
struct rtl_pcie_controls {void *core_reset,*phy_reset,*phy_control,*power;int lock;};
struct rtl_pcie_lane {struct rtl_pcie_controls *controls;unsigned int id;};
struct phy {struct rtl_pcie_lane *lane;};
static void *phy_get_drvdata(struct phy *phy){return phy->lane;}
struct reset_controller_dev {};
#define container_of(p,t,m) ((t *)(p))
'''
test = r'''
int main(void){
 u32 reference_ctrl,reference_power,native_ctrl,native_power;unsigned int cases=0;
 struct rtk_taurus_phy_core r={.reg_ctrl=&reference_ctrl,.reg_pwr=&reference_power};
 struct rtk_taurus_phy original={.core=&r};
 struct rtl_pcie_controls c={.phy_control=&native_ctrl,.power=&native_power};
 struct rtl_pcie_lane lane={.controls=&c};struct phy phy={.lane=&lane};
 for(unsigned int id=0;id<4;id++)for(unsigned int on=0;on<2;on++)
  for(unsigned int n=0;n<512;n++) {
   reference_ctrl=native_ctrl=0xa55a1234U+n*0x123457U;
   reference_power=native_power=0x7fdc5678U+n*0x87651U;
   original.id=lane.id=id;
   assert(!rtk_taurus_phy_power_config(&original,on));
   assert(!rtl_pcie_lane_power(&phy,on));
   assert(reference_ctrl==native_ctrl && reference_power==native_power);
   cases++;
  }
 printf("Native Taurus PHY:%u register vectors match GPL;four lanes on/off preserve unrelated bits\n",cases);
}
'''
defines = '\n'.join(line for line in stock.splitlines() if line.lstrip().startswith('#define '))
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='pcie-controls-') as directory:
    work = Path(directory)
    source, binary = work / 'controls.c', work / 'controls'
    source.write_text(common + defines + '\n' + function(stock, 'rtk_taurus_phy_power_config') +
                      function(current, 'rtl_pcie_update') + function(current, 'rtl_pcie_lane_power') + test)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/pcie-controls-test.json').write_text(json.dumps({
    'Taurus_GPL_source_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
    'native_source_sha256': hashlib.sha256(native.read_bytes()).hexdigest(),
    'vectors': 4096, 'lane_IDs': [0, 1, 2, 3], 'power_on_and_off_match': True,
    'unrelated_bits_preserved': True, 'power_register': 'f432036c',
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
