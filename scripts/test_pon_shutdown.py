#!/usr/bin/env python3
"""Check PON hardware stop independently of a failed FE cleanup."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
epon = (drivers / 'rtl9617c-pon-epon.c').read_text()
platform = (drivers / 'rtl9617c-pon.c').read_text()
assert re.search(r'\.shutdown\s*=\s*rtl9617c_pon_shutdown\b', platform)

def function(source, name):
    start = re.search(r'^(?:static )?(?:int|void) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#define BIT(n) (1U << (n))
enum {RTL_PON_EPON_1G, RTL_PON_EPON_D10G, RTL_PON_EPON_BI10G, RTL_PON_GPON, RTL_PON_XGPON, RTL_PON_XGSPON};
struct rtl9617c_pon {
 int lock, control_lock, mode, cleanup_error, cleanup_calls, carrier_updates;
 int epon_tx_action, epon_tx_error;
 bool fe_enabled, traffic_enabled, pdc_configured, puc_configured;
 void *mac; int *tx_disable;
 void *dev, *top_irq, *optical_irq;
 int mpcp_irq, gpon_stops, xgpon_stops, mpcp_stops, work_stops, errors, removed;
 struct { void *netdev; } oam_endpoint;
};
struct platform_device {struct rtl9617c_pon *pon;};
static struct rtl9617c_pon *shutting_down;
static unsigned int irq_syncs;
static void mutex_lock(int *lock) { assert(!*lock); *lock = 1; }
static void mutex_unlock(int *lock) { assert(*lock); *lock = 0; }
#define lockdep_assert_held(lock) assert(*(lock))
static bool rtl_pon_mode_is_epon(int mode) { return mode < RTL_PON_GPON; }
static void gpiod_set_value_cansleep(int *gpio, int value) { *gpio = value; }
static int gpiod_get_value_cansleep(int *gpio) { return *gpio; }
static int rtl_pon_fe_configure_locked(struct rtl9617c_pon *pon, bool enable)
{
 assert(pon->control_lock && !pon->lock && !enable);
 pon->cleanup_calls++;
 if (!pon->cleanup_error) pon->fe_enabled = false;
 return pon->cleanup_error;
}
static void rtl_pon_update(void *address, unsigned int mask, unsigned int value)
{ unsigned int *reg = address; *reg = (*reg & ~mask) | value; }
static bool netif_running(void *netdev) { (void)netdev; return true; }
static void rtl_pon_oam_carrier(struct rtl9617c_pon *pon) { pon->carrier_updates++; }
static struct rtl9617c_pon *platform_get_drvdata(struct platform_device *pdev) {return pdev->pon;}
static void writel(unsigned int value, void *reg) {*(unsigned int *)reg = value;}
static void rtl_pon_gpon_stop_locked(struct rtl9617c_pon *pon)
{ assert(pon->control_lock && pon->mode==RTL_PON_GPON); pon->gpon_stops++; }
static void rtl_pon_xgpon_stop_locked(struct rtl9617c_pon *pon)
{ assert(pon->control_lock && pon->mode>=RTL_PON_XGPON); pon->xgpon_stops++; }
static int rtl_pon_mpcp_stop_locked(struct rtl9617c_pon *pon)
{ assert(pon->control_lock && *pon->tx_disable); pon->mpcp_stops++; return 0; }
static void synchronize_irq(int irq)
{
 assert(irq == 24 && shutting_down->control_lock);
 assert(!((unsigned int *)shutting_down->top_irq)[1]);
 assert(!((unsigned int *)shutting_down->optical_irq)[1]);
 irq_syncs++;
}
static void rtl_pon_serdes_stop_work(struct rtl9617c_pon *pon)
{ assert(!pon->control_lock && irq_syncs && *pon->tx_disable); pon->work_stops++; }
static void rtl_pon_oam_remove(struct rtl9617c_pon *pon) { assert(pon->work_stops); pon->removed++; }
static void rtl_pon_omci_remove(struct rtl9617c_pon *pon) { assert(pon->work_stops); pon->removed++; }
#define dev_err(...) (shutting_down->errors++)
'''
defines = '\n'.join(re.findall(r'^#define (?:RX_XG|TX_XG|RX_1G|TX_1G)\s+.*$', epon, re.M))
test = r'''
int main(void)
{
 for (int mode = RTL_PON_EPON_1G; mode <= RTL_PON_EPON_BI10G; mode++) {
  for (int fail = 0; fail < 2; fail++) {
   unsigned int registers[0x3000 / 4] = {}; int disabled = 0;
   struct rtl9617c_pon pon = {.control_lock=1, .mode=mode, .mac=registers,
    .tx_disable=&disabled, .fe_enabled=true, .traffic_enabled=true,
    .cleanup_error=fail ? -ETIMEDOUT : 0};
   const unsigned int offsets[] = {RX_XG, TX_XG, RX_1G, TX_1G};
   const unsigned int masks[] = {BIT(7), BIT(3), BIT(0), BIT(0)};
   for (unsigned int i=0; i<4; i++) registers[offsets[i]/4] = 0x80000000U | masks[i];
   assert(rtl_pon_epon_traffic_locked(&pon, false) == pon.cleanup_error);
   assert(disabled && !pon.traffic_enabled && pon.carrier_updates == 1);
   assert(pon.cleanup_calls == 1 && pon.fe_enabled == !!fail);
   for (unsigned int i=0; i<4; i++) assert(registers[offsets[i]/4] == 0x80000000U);
  }
 }
 puts("PON stop: all EPON modes disable optical TX and RX/TX gates even when FE cleanup fails; original error retained");
 for (int mode = RTL_PON_EPON_1G; mode <= RTL_PON_XGSPON; mode++) {
  unsigned int registers[0x3000/4] = {}, top[2] = {0, ~0U}, optical[2] = {0, ~0U};
  int disabled = 0;
  struct rtl9617c_pon pon = {.mode=mode, .mac=registers, .tx_disable=&disabled,
   .top_irq=top, .optical_irq=optical, .mpcp_irq=24, .fe_enabled=true,
   .traffic_enabled=true, .cleanup_error=-ETIMEDOUT};
  struct platform_device pdev = {&pon};
  shutting_down = &pon; irq_syncs = 0;
  rtl9617c_pon_shutdown(&pdev);
  assert(disabled && !top[1] && !optical[1] && irq_syncs == 1);
  assert(pon.gpon_stops == (mode==RTL_PON_GPON));
  assert(pon.xgpon_stops == (mode>=RTL_PON_XGPON));
  assert(pon.mpcp_stops == (mode<RTL_PON_GPON));
  assert(pon.cleanup_calls == 1);
  assert(pon.work_stops == 1 && pon.errors == 1 && !pon.removed && !pon.control_lock);
  if (mode == RTL_PON_EPON_D10G) {
   rtl9617c_pon_remove(&pdev);
   assert(pon.work_stops == 2 && pon.removed == 2);
  }
 }
 puts("PON shutdown: registered callback stops protocol engines, masks/drains IRQs and joins work outside control_lock; netdev release belongs only to remove");
}
'''
with tempfile.TemporaryDirectory(prefix='pon-shutdown-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(fixture + defines + '\n' + function(epon, 'rtl_epon_tx_apply') +
                               function(epon, 'rtl_pon_epon_traffic_locked') +
                               function(platform, 'rtl_pon_stop_locked') +
                               function(platform, 'rtl9617c_pon_shutdown') +
                               function(platform, 'rtl9617c_pon_remove') + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
