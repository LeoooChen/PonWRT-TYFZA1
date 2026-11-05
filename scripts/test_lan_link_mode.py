#!/usr/bin/env python3
"""Exercise the production MAC link callback against the SGMII port contract."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c').read_text()
start = re.search(r'^static void rtl_adjust_link\(', source, re.M).start()
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
callback = source[start:end]
shim = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
#define PORT_STATIC 0xa5c0
#define PORT_GLB 0xa5c4
#define PORT_STRIDE 0x90
#define SPEED_10 10
#define SPEED_1000 1000
#define DUPLEX_HALF 0
struct rtl_xram { unsigned int sgmii_speed; void *sgmii; };
struct rtl_lan { struct rtl_xram *shared; unsigned int port; };
struct phy_device { int link, speed, duplex; };
struct net_device { struct rtl_lan lan; struct phy_device *phydev; };
static uint32_t regs[0x10000/4];
static int s3_speed, calls;
static void *netdev_priv(struct net_device *d) { return &d->lan; }
static void rtl_update(struct rtl_xram *p, unsigned int reg, uint32_t mask, uint32_t value) {
    (void)p; regs[reg/4] = (regs[reg/4] & ~mask) | value;
}
static int phy_set_speed(void *p, int speed) { (void)p; s3_speed=speed; calls++; return 0; }
#define netdev_err(...) ((void)0)
#define phy_error(...) ((void)0)
#define phy_print_status(...) ((void)0)
'''
test = r'''
int main(void) {
    const int speeds[] = {10,100,1000,2500};
    /* The vendor port4 PCS contract keeps GMII even at 10/100 Mbps. */
    const int interface_mode[5][4] = {{1,1,0,0},{1,1,0,0},{1,1,0,0},{1,1,0,0},{0,0,0,0}};
    struct rtl_xram shared = {0};
    struct phy_device phy = {.link=1};
    struct net_device dev = {.lan.shared=&shared,.phydev=&phy};
    for(unsigned int port=0;port<5;port++) for(int i=0;i<4;i++) for(int duplex=0;duplex<2;duplex++) {
        unsigned int cfg=(PORT_STATIC+port*PORT_STRIDE)/4, glb=(PORT_GLB+port*PORT_STRIDE)/4;
        dev.lan.port=port; phy.speed=speeds[i]; phy.duplex=duplex;
        regs[cfg]=0xa5a5a5af; regs[glb]=0x1234567f;
        shared.sgmii_speed=0; calls=0;
        rtl_adjust_link(&dev);
        assert(regs[cfg] == ((0xa5a5a5af & ~15U) | interface_mode[port][i]));
        assert((regs[glb]&~3U) == (0x1234567f&~3U));
        assert((regs[glb]&3U) == ((speeds[i]==10?1U:0U) | (duplex==0?2U:0U)));
        assert(calls == (port==4));
        if(port==4) assert(s3_speed==speeds[i] && shared.sgmii_speed==speeds[i]);
        phy.link=0; uint32_t saved=regs[cfg]; rtl_adjust_link(&dev); assert(regs[cfg]==saved); phy.link=1;
    }
    puts("80 link cases: SGMII port4 retains GMII; internal ports retain MII/GMII speed selection; duplex and unrelated bits preserved");
}
'''
with tempfile.TemporaryDirectory(prefix='tyfza1-link-mode-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(shim + callback + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare', str(work/'test.c'), '-o', str(work/'test')], check=True)
    subprocess.run([str(work/'test')], check=True)
