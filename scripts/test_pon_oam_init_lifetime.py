#!/usr/bin/env python3
"""Exercise OAM probe failure with an optical IRQ reader at RCU withdrawal."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-pon-oam.c').read_text()
start = re.search(r'^int rtl_pon_oam_init\(', source, re.M).start()
end = source.index('\nvoid rtl_pon_oam_remove(', start)

fixture = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
struct net_device { char name[16]; };
struct endpoint { struct net_device *netdev; void (*receive)(void), (*wake)(void); };
struct rtl9617c_pon { struct endpoint oam_endpoint; struct net_device *oam_devices[32]; void *ni_dev, *dev; };
static int fault, reader, grace_periods;
static bool attached, registered;
static struct net_device *rtl_oam_allocate(struct rtl9617c_pon *p, unsigned int n, bool control)
{ (void)p; assert(n == 0 && control); return fault == 1 ? NULL : calloc(1, sizeof(struct net_device)); }
static void rtl_oam_receive(void) {}
static void rtl_oam_wake(void) {}
static void publish(struct net_device **slot, struct net_device *dev)
{
 /* The optical IRQ can enter after NI detach's grace period, before withdrawal. */
 if (!dev && *slot) reader = 1;
 *slot = dev;
}
#define rcu_assign_pointer(slot, dev) publish(&(slot), dev)
static void synchronize_net(void) { reader = 0; grace_periods++; }
static int rtl9617c_ni_pon_attach(void *ni, struct endpoint *ep)
{ (void)ni; assert(ep->netdev); attached = true; return 0; }
static void rtl9617c_ni_pon_detach(void *ni)
{ (void)ni; assert(attached); attached = false; synchronize_net(); }
static int register_netdev(struct net_device *dev)
{ assert(dev); if (fault == 3) return -EIO; registered = true; return 0; }
static void unregister_netdev(struct net_device *dev)
{ assert(dev && registered); registered = false; }
static int rtl_pon_oam_resize(struct rtl9617c_pon *p, unsigned int n)
{ assert(n == 1 && p->oam_devices[0]); return fault == 4 ? -ENOMEM : 0; }
static void rtl_oam_free(struct net_device *dev)
{ assert(!attached && !registered); assert(!reader && "optical IRQ still holds the OAM device"); free(dev); }
#define dev_info(...) ((void)0)
'''
test = r'''
int main(void)
{
 const int faults[] = {0, 1, 3, 4}, errors[] = {0, -ENOMEM, -EIO, -ENOMEM};
 for (unsigned int i = 0; i < 4; i++) {
  struct rtl9617c_pon pon = {};
  fault = faults[i];
  reader = grace_periods = 0;
  attached = registered = false;
  assert(rtl_pon_oam_init(&pon) == errors[i]);
  if (!fault) {
   assert(attached && registered && pon.oam_devices[0] && !grace_periods);
   free(pon.oam_devices[0]);
  } else {
   assert(!attached && !registered && !pon.oam_devices[0] && !reader);
  }
 }
 puts("PON OAM init: success unchanged; allocation/register/resize errors preserved; optical reader retired before free");
}
'''
with tempfile.TemporaryDirectory(prefix='pon-oam-init-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(fixture + source[start:end] + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
