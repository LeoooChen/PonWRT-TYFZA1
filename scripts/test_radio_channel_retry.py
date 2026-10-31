#!/usr/bin/env python3
"""Run the real channel transaction with RFK and firmware-notification failures."""
import argparse
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/pci.c"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--revision")
args = parser.parse_args()
text = (subprocess.check_output(["git", "show", f"{args.revision}:{relative}"], cwd=root, text=True)
        if args.revision else (root / relative).read_text())
start = text.index("static int rtl_ax_receive_configure(")
callback = text[start:text.index("static int rtl_ax_receive_set_channel(", start)]
source = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define RTL_AX_8852C 0x52
#define RTL_AX_8192XB 0x50
#define BIT(n) (1U<<(n))
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define pci_clear_master(p) ((void)(p))
#define pci_set_master(p) ((void)(p))
#define rtl_ax_rx_close(...) ((void)0)
#define rtl_ax_scan_complete_frame(...) ((void)0)
#define rtl_ax_rx_open(...) ((void)0)
#define rtl_ax_hci_pause(...) ((void)0)
#define rtl_ax_h2c_start(...) ((void)0)
#define rtl_ax_rx_dfs_epoch(...) ((void)0)
#define rtl_ax_channel_field(...) ((void)0)
#define rtl_ax_xb_tssi_de(...) ((void)0)
#define rtl_ax_c_tssi_de20(...) ((void)0)
#define rtl_ax_dig_bandwidth(...) ((void)0)
struct channel {bool configured;unsigned primary,center,bandwidth,band;};
struct rtl_ax_ap {
 struct {int (*poll32)(void *,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned *);}io;
 struct channel channel;struct {bool fahm_triggered,fahm_configured;}dig;
 struct {int error;bool enabled;unsigned region;}dfs;
 struct {bool rek_enabled;}rxdck;struct {bool ready;}xb_tssi,c_tssi;
 unsigned chip,rxdck_channel,dfs_domain;int command_lock,pdev,tx;
 bool monitor_started,radar_required,dpk_bypassed;
};
static unsigned dfs_region,failed,visits[16],opens;
static bool stopped,blocked;
static int step(unsigned n){visits[n]++;return failed==n?-ETIMEDOUT:0;}
static int pause_tx(void){stopped=true;return step(1);}
#define rtl_ax_tx_pause(...) pause_tx()
#define rtl_ax_tx_open(...) do {assert(stopped);stopped=false;opens++;}while(0)
#define rtl_ax_tx_dfs_block(tx,value) ((void)(tx),blocked=(value))
#define rtl_ax_dfs_stop_locked(...) step(2)
#define rtl_ax_power_apply(...) step(5)
#define rtl_ax_xb_rxdck_start(...) step(6)
#define rtl_ax_c_rxdck_start(...) step(6)
#define rtl_ax_gapk_channel(...) step(7)
#define rtl_ax_c_gapk_start(...) step(8)
#define rtl_ax_xb_iqk_start(...) step(9)
#define rtl_ax_c_iqk_start(...) step(9)
#define rtl_ax_xb_tssi_start(...) step(10)
#define rtl_ax_c_tssi_start20(...) step(10)
#define rtl_ax_bb_channel_notify_locked(...) step(12)
#define rtl_ax_rfk_notify_locked(...) step(13)
static int dfs_start(struct rtl_ax_ap *r){int ret=step(11);if(!ret)r->dfs.enabled=r->radar_required;return ret;}
#define rtl_ax_dfs_start_locked(r) dfs_start(r)
static int set_channel(struct channel *ch,unsigned primary,unsigned center,unsigned bw){
 int ret=step(4);if(ret)return ret;
 ch->configured=true;ch->primary=primary;ch->center=center;ch->bandwidth=bw;return 0;
}
#define rtl_ax_c_receive_channel(io,p,d,pri,ch,bw,out) set_channel(out,pri,ch,bw)
#define rtl_ax_xb_receive_channel(io,p,d,pri,ch,bw,out) set_channel(out,pri,ch,bw)
static int poll32(void *p,unsigned r,unsigned mask,unsigned want,unsigned delay,unsigned timeout,unsigned *last){
 (void)p;(void)r;(void)mask;(void)delay;(void)timeout;*last=want;return step(3);
}
"""
source += callback
source += r"""
int main(void){
 for(unsigned chip=0;chip<2;chip++)for(unsigned running=0;running<2;running++)for(unsigned fault=1;fault<=13;fault++){
  if(chip&&(fault==8||fault==13))continue;
  struct rtl_ax_ap r={.chip=chip?RTL_AX_8192XB:RTL_AX_8852C,.monitor_started=running,.io={poll32}};
  unsigned primary=chip?1:36;
  /* Begin on a valid old channel; a failed new transaction cannot reuse it. */
  failed=0;stopped=true;assert(!rtl_ax_receive_configure(&r,primary,primary,0));
  memset(visits,0,sizeof(visits));opens=0;failed=fault;
  assert(rtl_ax_receive_configure(&r,primary,primary+2,1)==-ETIMEDOUT);
  assert(r.dfs.error==-ETIMEDOUT&&stopped&&!opens);
  if(fault>1)assert(!r.channel.configured);
  failed=0;unsigned calibrated=visits[10],notified=visits[12];
  assert(!rtl_ax_receive_configure(&r,primary,primary+2,1));
  assert(r.channel.configured&&!r.dfs.error&&opens==running);
  assert(visits[10]==calibrated+1&&visits[12]==notified+1);
  /* No fault: identical requests must still avoid a second RFK. */
  calibrated=visits[10];notified=visits[12];stopped=true;
  assert(!rtl_ax_receive_configure(&r,primary,primary+2,1));
  assert(visits[10]==calibrated&&visits[12]==notified);
 }
 /* Detector-only failure does not invalidate an already calibrated channel. */
 struct rtl_ax_ap r={.chip=RTL_AX_8852C,.monitor_started=true,.io={poll32}};
 failed=0;stopped=true;assert(!rtl_ax_receive_configure(&r,36,36,0));
 unsigned calibrated=visits[10];r.radar_required=true;failed=11;
 assert(rtl_ax_receive_configure(&r,36,36,0)==-ETIMEDOUT&&r.channel.configured&&stopped);
 failed=0;assert(!rtl_ax_receive_configure(&r,36,36,0));
 assert(visits[10]==calibrated&&r.dfs.enabled&&!r.dfs.error);
 puts("PASS: C/XB channel failures retry calibration/notifications; unchanged and DFS-only requests avoid RFK");
}
"""
with tempfile.TemporaryDirectory(prefix="radio-channel-retry-") as temp:
    p = Path(temp)
    (p / "test.c").write_text(source)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", str(p / "test.c"), "-o", str(p / "test")], check=True)
    subprocess.run([str(p / "test")], check=True)
