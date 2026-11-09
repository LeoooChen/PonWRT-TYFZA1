#!/usr/bin/env python3
"""Validate the driver's actual interface combinations with Linux's verifier."""
from pathlib import Path
import subprocess,tempfile
r=Path(__file__).resolve().parents[1];k=next((r/'sources/openwrt/build_dir').glob('target-*/linux-rtl9617c_generic/linux-6.18.55'))
s=(r/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h').read_text()
a=s.index(' static const struct ieee80211_iface_limit limits');b=s.index('\tstatic const u16 rates[]',a)
combinations=s[a:b]
s=(k/'net/wireless/core.c').read_text();a=s.index('int wiphy_verify_iface_combinations(');b=s.index('\n}\n',a)+3
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
typedef uint32_t u32;typedef uint16_t u16;
#define WARN_ON(x) (x)
#define BIT(n) (1U<<(n))
enum {NL80211_IFTYPE_AP,NL80211_IFTYPE_STATION,NL80211_IFTYPE_MESH_POINT,NL80211_IFTYPE_P2P_DEVICE,NL80211_IFTYPE_NAN,NL80211_IFTYPE_ADHOC,NL80211_IFTYPE_WDS};
enum {NL80211_CHAN_WIDTH_20_NOHT,NL80211_CHAN_WIDTH_20,NL80211_CHAN_WIDTH_40,NL80211_CHAN_WIDTH_80,NL80211_CHAN_WIDTH_160};
enum {NL80211_DFS_FCC,NL80211_DFS_ETSI,NL80211_DFS_JP};
struct ieee80211_iface_limit {unsigned max,types;};
struct ieee80211_iface_combination {const struct ieee80211_iface_limit *limits;unsigned n_limits,max_interfaces,num_different_channels,radar_detect_widths,radar_detect_regions,beacon_int_min_gcd;bool beacon_int_infra_match;};
struct wiphy {unsigned interface_modes,software_iftypes;};
'''+s[a:b]+'\nint main(void){\n'+combinations+r'''
 struct wiphy w={.interface_modes=BIT(NL80211_IFTYPE_AP)|BIT(NL80211_IFTYPE_STATION)|BIT(NL80211_IFTYPE_MESH_POINT)};
 assert(!wiphy_verify_iface_combinations(&w,combinations,sizeof(combinations)/sizeof(*combinations),false));
 assert(!wiphy_verify_iface_combinations(&w,dfs_combinations,sizeof(dfs_combinations)/sizeof(*dfs_combinations),false));
 assert(combinations[0].max_interfaces==8&&combinations[0].num_different_channels==1&&combinations[0].limits->types==BIT(NL80211_IFTYPE_AP));
 assert(dfs_combinations[1].max_interfaces==1&&dfs_combinations[1].radar_detect_widths);
 puts("Actual Linux combination verifier accepts 8 AP/one channel and separate single STA/mesh DFS declarations PASS");
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.c').write_text(code);subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-sign-compare',str(p/'test.c'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)
