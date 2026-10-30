#!/usr/bin/env python3
"""A stopped AP must release its NI netdev reference while RX can still drain."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h').read_text()
start=source.index('static void rtl_ax_monitor_stop(')
end=source.index('\n}\n',start)+3
common=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define BIT(n) (1U<<(n))
#define WRITE_ONCE(p,v) ((p)=(v))
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
#define wiphy_delayed_work_cancel(a,b) ((void)(a),(void)(b))
#define wiphy_work_cancel(a,b) ((void)(a),(void)(b))
#define mutex_lock(a) do {assert(!*(a));*(a)=1;} while(0)
#define mutex_unlock(a) do {assert(*(a));*(a)=0;} while(0)
#define tasklet_kill(a) ((void)(a))
#define dev_err(...) ((void)0)
struct rx {bool running;};struct tx {bool netdev_reference;struct rx *rx;};
struct io {void (*write32)(void*,unsigned,unsigned);};
struct rtl_ax_ap {int cleanup_work,ra_work,dm_work,ra_update_work,command_mutex,command_lock,tx_tasklet;
 struct {bool enabled,in_cac;int error;}dfs;bool monitor_started,beacon_enabled,radar_required;
 struct io io;struct tx *tx;struct rx *rx;};
struct ieee80211_hw {struct rtl_ax_ap *radio;void *wiphy;};
static struct rtl_ax_ap *rtl_ax_monitor_radio(struct ieee80211_hw *hw){return hw->radio;}
static void rtl_ax_update32(struct io *io,unsigned reg,unsigned mask,unsigned value){(void)io;(void)reg;(void)mask;(void)value;}
static void write32(void *ctx,unsigned reg,unsigned value){(void)ctx;(void)reg;(void)value;}
static int *command_mutex;
static int rtl_ax_tx_ni_route(struct tx *tx,void *netdev,unsigned id){
 mutex_lock(command_mutex);mutex_unlock(command_mutex);
 (void)netdev;assert(!id);assert(tx->rx->running);tx->netdev_reference=false;return 0;
}
static int rtl_ax_tx_pause(struct tx *tx){assert(!tx->netdev_reference);return 0;}
static void rtl_ax_rx_close(struct rx *rx){rx->running=false;}
static int rtl_ax_dfs_stop_locked(struct rtl_ax_ap *radio){(void)radio;return 0;}
'''
common += source[start:end]
common += r'''
int main(void){struct rx rx={true};struct tx tx={true,&rx};
 struct rtl_ax_ap radio={.rx=&rx,.tx=&tx,.io={write32},.monitor_started=true};
 struct ieee80211_hw hw={&radio,NULL};command_mutex=&radio.command_mutex;rtl_ax_monitor_stop(&hw,false);
 assert(!rx.running&&!tx.netdev_reference&&!radio.monitor_started);return 0;}
'''
with tempfile.TemporaryDirectory(prefix='tyf-stop-route-') as temporary:
    p=Path(temporary);(p/'test.c').write_text(common)
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Wno-unused-parameter',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('AP stop: NI netdev reference withdrawn before TX pause and RX teardown')
