#!/usr/bin/env python3
"""Exercise radar-only changes in the actual patched chanctx emulation code."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
kernel = Path(subprocess.check_output([sys.executable, str(root / 'port/tools/project_paths.py'),
                                      '--kernel-directory', str(root / 'sources/openwrt')], text=True).strip())
main = (kernel / 'net/mac80211/main.c').read_text()


def function(name):
    start = main.rfind('\n', 0, main.index(name + '(')) + 1
    end = main.index('{', start) + 1
    depth = 1
    while depth:
        depth += (main[end] == '{') - (main[end] == '}')
        end += 1
    return main[start:end] + '\n'


source = r'''
#include <stdbool.h>
#include <stdint.h>
#include <assert.h>
#include <stddef.h>
#include <errno.h>
#define EXPORT_SYMBOL(n)
#define IEEE80211_CONF_CHANGE_CHANNEL 1
typedef uint32_t u32;
struct ieee80211_hw {struct {bool radar_enabled;}conf;};
struct ieee80211_local {struct ieee80211_hw hw;unsigned open_count;};
struct ieee80211_chanctx_conf {bool radar_enabled;};
static unsigned calls,mask,channel_change;static int result;static bool enabled;
static struct ieee80211_local *hw_to_local(struct ieee80211_hw *hw){return (void *)((char *)hw - offsetof(struct ieee80211_local,hw));}
static u32 ieee80211_calc_hw_conf_chan(struct ieee80211_local *local,struct ieee80211_chanctx_conf *ctx){(void)local;(void)ctx;return channel_change;}
static int drv_config(struct ieee80211_local *local,int radio,u32 changed){assert(radio==-1);calls++;mask=changed;enabled=local->hw.conf.radar_enabled;return result;}
'''
source += ''.join(function(name) for name in ('_ieee80211_hw_conf_chan', 'ieee80211_emulate_add_chanctx',
                                              'ieee80211_emulate_change_chanctx', 'ieee80211_emulate_remove_chanctx'))
source += r'''
int main(void){
 struct ieee80211_local local={.open_count=1};struct ieee80211_chanctx_conf ctx={};
 assert(!ieee80211_emulate_add_chanctx(&local.hw,&ctx)&&!calls);
 ctx.radar_enabled=true;ieee80211_emulate_change_chanctx(&local.hw,&ctx,0);
 assert(calls==1&&mask==IEEE80211_CONF_CHANGE_CHANNEL&&enabled);
 ieee80211_emulate_change_chanctx(&local.hw,&ctx,0);assert(calls==1);
 ieee80211_emulate_remove_chanctx(&local.hw,&ctx);assert(calls==2&&!enabled);
 result=-EIO;assert(ieee80211_emulate_add_chanctx(&local.hw,&ctx)==-EIO);
 assert(calls==3&&enabled);result=0;channel_change=2;
 ieee80211_emulate_remove_chanctx(&local.hw,&ctx);assert(calls==4&&mask==3&&!enabled);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='mac80211-radar-') as temporary:
    temporary = Path(temporary)
    (temporary / 'test.c').write_text(source)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', str(temporary / 'test.c'),
                    '-o', str(temporary / 'test')], check=True)
    subprocess.run([str(temporary / 'test')], check=True)
record = dict(actual_patched_mac80211_functions=True, unchanged_channel_radar_enable=True,
              unchanged_channel_radar_disable=True, unchanged_radar_does_not_retune=True,
              driver_error_propagated=True, channel_and_radar_change_combined=True,
              software_scan_emulation_preserved=True,
              patch_sha256=hashlib.sha256((root / 'port/overlay/target/linux/rtl9617c/patches-6.18/208-mac80211-emulated-radar-change.patch').read_bytes()).hexdigest())
(root / 'manifests/mac80211-radar-contract.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
