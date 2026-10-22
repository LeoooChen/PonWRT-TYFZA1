#!/usr/bin/env python3
"""Check authorization publication before a failed FE route withdrawal."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
def function(file, name):
    source = (driver / file).read_text()
    at = source.index(name + '(')
    start = source.rfind('\n', 0, at) + 1
    end = source.index('{', at) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]
common = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
typedef uint8_t u8;
#define BIT(n) (1U<<(n))
#define WRITE_ONCE(p,v) ((p)=(v))
#define READ_ONCE(p) (p)
#define dma_wmb() ((void)0)
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
struct rtl_ax_tx {bool peer_authorized[128],pe_active;struct {void *memory;} pe;struct {bool route_peers[128];} ni;};
struct rtl_ax_ap {struct rtl_ax_tx *tx; bool peer_authorized[128]; int offload_result;};
struct rtl_ax_station { unsigned macid; };
struct ieee80211_sta {void *drv_priv;};
struct ieee80211_vif {int unused;};
struct ieee80211_hw {struct rtl_ax_ap *radio;};
enum ieee80211_sta_state { IEEE80211_STA_AUTH,IEEE80211_STA_ASSOC,IEEE80211_STA_AUTHORIZED };
static void put_unaligned_le32(uint32_t v,void *p) {memcpy(p,&v,4);}
static uint32_t get(const void *p) {uint32_t v;memcpy(&v,p,4);return v;}
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw) { return hw->radio; }
static int rtl_ax_station_add(struct ieee80211_hw *hw,struct ieee80211_vif *vif,struct ieee80211_sta *sta) {(void)hw;(void)vif;(void)sta;return 0;}
static int rtl_ax_station_remove(struct ieee80211_hw *hw,struct ieee80211_vif *vif,struct ieee80211_sta *sta) {(void)hw;(void)vif;(void)sta;return 0;}
static void rtl_ax_offload_ready(void *context) {(void)context;}
static int rtl_ax_tx_ni_retire_peer(struct rtl_ax_tx *tx,unsigned macid) {
 assert(!tx->peer_authorized[macid]);
 assert(!get(tx->pe.memory+0x11a00+macid*4));
 return -ETIMEDOUT;
}
'''
test = r'''
int main(void) {
 unsigned char memory[131072]={};
 struct rtl_ax_tx tx={.pe_active=true,.pe={memory}};
 tx.peer_authorized[1]=tx.ni.route_peers[1]=true;
 struct rtl_ax_ap radio={.tx=&tx};radio.peer_authorized[1]=true;
 struct ieee80211_hw hw={&radio};struct rtl_ax_station station={.macid=1};struct ieee80211_sta sta={&station};struct ieee80211_vif vif={};
 put_unaligned_le32(1,memory+0x11a04);put_unaligned_le32(1,memory+0x11a08);
 assert(!rtl_ax_sta_state(&hw,&vif,&sta,IEEE80211_STA_AUTHORIZED,IEEE80211_STA_ASSOC));
 assert(radio.offload_result==-ETIMEDOUT && !radio.peer_authorized[1]);
 assert(!get(memory+0x11a04));
 assert(get(memory+0x11a08)==1);
 assert(!rtl_ax_sta_state(&hw,&vif,&sta,IEEE80211_STA_ASSOC,IEEE80211_STA_AUTHORIZED));
 assert(get(memory+0x11a04)==1);
 tx.pe_active=false;tx.pe.memory=NULL;
 rtl_ax_tx_peer_authorized(&tx,1,false);assert(!tx.peer_authorized[1]);
 puts("actual ARM source: authorization gates close before failed FE withdrawal; other peer unchanged; reauthorization restored");
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-authorization-') as folder:
    folder = Path(folder)
    body = common + '\n'.join(function(f, n) for f, n in [
        ('tx.c', 'rtl_ax_tx_peer_authorized'), ('monitor.h', 'rtl_ax_sta_state')]) + test
    source, binary = folder / 'authorization.c', folder / 'authorization'
    source.write_text(body)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
