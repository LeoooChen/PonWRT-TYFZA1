#!/usr/bin/env python3
"""Exercise ported watchdog MMIO sequences on the host, not a simulated SoC."""
from pathlib import Path
import subprocess
import tempfile
import unittest

DRIVER = Path(__file__).resolve().parents[1] / 'overlay/target/linux/rtl9617c/files-6.18/drivers/watchdog/rtl9617c-wdt.c'
SHIM = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
typedef int spinlock_t;
typedef int irqreturn_t;
#define IRQ_NONE 0
#define IRQ_HANDLED 1
#define __iomem
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((UINT32_MAX >> (31-(h))) & (UINT32_MAX << (l)))
#define FIELD_PREP(mask,val) (((u32)(val) << __builtin_ctz(mask)) & (mask))
#define spin_lock(lock) ((void)(lock))
#define spin_unlock(lock) ((void)(lock))
#define spin_lock_irqsave(lock,flags) do { (void)(lock); (flags)=0; } while(0)
#define spin_unlock_irqrestore(lock,flags) do { (void)(lock); (void)(flags); } while(0)
struct watchdog_device { unsigned timeout; bool active, hw_running; void *driver_data; };
static void *watchdog_get_drvdata(struct watchdog_device *w) { return w->driver_data; }
static bool watchdog_active(struct watchdog_device *w) { return w->active; }
static bool watchdog_hw_running(struct watchdog_device *w) { return w->hw_running; }
static unsigned writes;
static u32 readl(void *p) { return *(u32 *)p; }
static void writel(u32 v,void *p) { *(u32 *)p=v; writes++; }
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"failed line %d: %s\n",__LINE__,#x); return 1; } } while(0)
'''
TEST = r'''
int main(int argc,char **argv)
{
    u32 regs[12]={0},reset=0xa5000005;
    struct rtl9617c_wdt wdt={.base=regs,.reset=&reset,.prescaler=124999,.delay_cycles=1046808};
    struct watchdog_device *w=&wdt.wdd;
    w->timeout=60; w->driver_data=&wdt;
    CHECK(argc==2);
    if (!strcmp(argv[1],"start")) {
        regs[WDT_CTRL/4]=0x12345004;
        CHECK(rtl_wdt_start(w)==0);
        CHECK(regs[WDT_PS/4]==124999 && regs[WDT_DIV/4]==1000);
        CHECK(regs[WDT_LOAD/4]==60 && regs[WDT_RELOAD/4]==3);
        CHECK((regs[WDT_CTRL/4] & (WDT_ENABLE|WDT_RESET|WDT_CLKSEL))==3);
        CHECK((regs[WDT_CTRL/4] & WDT_DELAY)==(1046808U<<12));
        CHECK(reset==(0xa5000005|WDT_RESET_ROUTE));
    } else if (!strcmp(argv[1],"ping-stop")) {
        regs[WDT_CTRL/4]=0xff018003; regs[WDT_LOAD/4]=60;
        CHECK(rtl_wdt_ping(w)==0 && writes==1);
        CHECK(regs[WDT_RELOAD/4]==3 && regs[WDT_LOAD/4]==60);
        CHECK(rtl_wdt_stop(w)==0);
        CHECK(regs[WDT_CTRL/4]==0xff018002 && regs[WDT_IRQ_ENABLE/4]==0);
    } else if (!strcmp(argv[1],"timeout")) {
        CHECK(rtl_wdt_set_timeout(w,90)==0 && writes==0);
        CHECK(w->timeout==90);
        w->hw_running=true;
        CHECK(rtl_wdt_set_timeout(w,65536)==0);
        CHECK(regs[WDT_LOAD/4]==65536); /* No vendor u16 truncation. */
        regs[WDT_COUNT/4]=53;
        CHECK(rtl_wdt_get_timeleft(w)==53);
    } else if (!strcmp(argv[1],"irq")) {
        CHECK(rtl_wdt_irq(1,&wdt)==IRQ_NONE && writes==0);
        regs[WDT_IRQ_PENDING/4]=1; regs[WDT_IRQ_ENABLE/4]=1;
        CHECK(rtl_wdt_irq(1,&wdt)==IRQ_HANDLED);
        CHECK(regs[WDT_IRQ_ENABLE/4]==0 && regs[WDT_RELOAD/4]==0);
    } else return 2;
    return 0;
}
'''


class WatchdogPort(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        source = DRIVER.read_text()
        source = source[source.index('#define WDT_CTRL'):source.index('static const struct watchdog_info')]
        test = Path(cls.tmp.name) / 'watchdog.c'
        test.write_text(SHIM + source + TEST)
        cls.binary = Path(cls.tmp.name) / 'watchdog-test'
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fsanitize=undefined', str(test),
                        '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_vendor_start_sequence(self):
        self.run_case('start')

    def test_ping_and_stop_preserve_other_fields(self):
        self.run_case('ping-stop')

    def test_boot_enabled_timeout_and_full_width_counter(self):
        self.run_case('timeout')

    def test_interrupt_does_not_mask_watchdog_failure(self):
        self.run_case('irq')


if __name__ == '__main__':
    unittest.main()
