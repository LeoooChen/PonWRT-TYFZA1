#!/usr/bin/env python3
"""Check native PCIe shared-analog ownership against the Taurus register map."""
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
struct rtl_pcie_controls {void *core_reset,*phy_reset,*phy_control,*power;int lock;u32 powered_lanes;};
struct rtl_pcie_lane {struct rtl_pcie_controls *controls;unsigned int id;};
struct phy {struct rtl_pcie_lane *lane;};
static void *phy_get_drvdata(struct phy *phy){return phy->lane;}
struct reset_controller_dev {};
#define container_of(p,t,m) ((t *)(p))
'''
test = r'''
int main(void){
 u32 reference_ctrl=BIT(27),reference_power=0x4000400f,native_ctrl=BIT(27),native_power=0x4000400f;
 struct rtk_taurus_phy_core r={.reg_ctrl=&reference_ctrl,.reg_pwr=&reference_power};
 struct rtk_taurus_phy original={.core=&r};
 struct rtl_pcie_controls c={.phy_control=&native_ctrl,.power=&native_power};
 struct rtl_pcie_lane lane={.controls=&c};struct phy phy={.lane=&lane};
 const unsigned int order[]={0,1,1,0};
 /* Preserve the stock sequence where its lane0-first assumption is valid. */
 for(unsigned int step=0;step<4;step++) {
  original.id=lane.id=order[step];
  assert(!rtk_taurus_phy_power_config(&original,step<2));
  assert(!rtl_pcie_lane_power(&phy,step<2));
  assert(reference_ctrl==native_ctrl && reference_power==native_power);
 }
 for(unsigned int pair=0;pair<2;pair++) {
  unsigned int first=pair*2,second=first+1;
  u32 analog=BIT(16+pair),other_pair=native_power;
  /* A late/deferred lane0 must not be a prerequisite for lane1. */
  native_ctrl|=BIT(27);
  lane.id=second;assert(!rtl_pcie_lane_power(&phy,true));
  assert(native_power&analog);
  if(!pair)assert(!(native_ctrl&BIT(27)));
  u32 second_bits=BIT(7+second)|BIT(20+second*2)|BIT(21+second*2);
  assert((native_power&second_bits)==second_bits && !(native_power&BIT(second)));
  lane.id=first;assert(!rtl_pcie_lane_power(&phy,true));
  /* Includes lane0's calibration power cycle and failed-probe unwind. */
  assert(!rtl_pcie_lane_power(&phy,false));
  assert(native_power&analog);
  assert((native_power&second_bits)==second_bits && !(native_power&BIT(second)));
  assert(!rtl_pcie_lane_power(&phy,true));
  lane.id=second;assert(!rtl_pcie_lane_power(&phy,false));
  assert(native_power&analog);
  lane.id=first;assert(!rtl_pcie_lane_power(&phy,false));
  assert(!(native_power&analog));
  assert(native_power==other_pair && !c.powered_lanes);
  }
 puts("PCIe PHY: stock normal sequence unchanged; either lane can start first; sibling survives power cycle/unwind; last lane releases shared analog");
}
'''
defines = '\n'.join(line for line in stock.splitlines() if line.lstrip().startswith('#define '))
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='pcie-controls-') as directory:
    work = Path(directory)
    source, binary = work / 'controls.c', work / 'controls'
    source.write_text(common + defines + '\n' + function(stock, 'rtk_taurus_phy_power_config') +
                      function(current, 'rtl_pcie_lane_power') + test)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/pcie-controls-test.json').write_text(json.dumps({
    'Taurus_GPL_source_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
    'native_source_sha256': hashlib.sha256(native.read_bytes()).hexdigest(),
    'lane_IDs': [0, 1, 2, 3], 'stock_normal_sequence_matches': True,
    'independent_lane_start': True, 'sibling_survives_power_cycle_and_unwind': True,
    'last_lane_releases_shared_analog': True,
    'unrelated_bits_preserved': True, 'power_register': 'f432036c',
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
