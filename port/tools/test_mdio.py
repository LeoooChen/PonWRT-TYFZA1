#!/usr/bin/env python3
"""Host checks of the ported MDIO/OCP functions with recorded MMIO writes."""
from pathlib import Path
import subprocess
import tempfile
import unittest

DRIVER = Path(__file__).resolve().parents[1] / 'overlay/target/linux/rtl9617c/files-6.18/drivers/net/mdio/mdio-rtl9617c.c'
SHIM = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint16_t u16;
#define __iomem
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((UINT32_MAX >> (31-(h))) & (UINT32_MAX << (l)))
#define FIELD_PREP(mask,val) (((u32)(val) << __builtin_ctz(mask)) & (mask))
struct mii_bus { void *priv; };
static u32 per[18],ocp[0x100000/4],wrap[64],addresses[8],values[8];
static unsigned starts, timeout_at, delays;
static u32 readl(void *p) { return *(u32 *)p; }
static void writel(u32 v,void *p) {
    *(u32 *)p=v;
    if (p==&per[0x10/4] && v==0x80) {
        addresses[starts]=per[4/4];values[starts]=per[8/4];starts++;
        if (starts!=timeout_at) per[0x10/4]=1;
    }
}
static void usleep_range(unsigned a,unsigned b) { (void)b;delays+=a; }
#define readl_poll_timeout(p,v,condition,delay,limit) ((v)=readl(p),(condition)?0:-ETIMEDOUT)
#define CHECK(x) do { if (!(x)) {fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 1;} } while(0)
'''
TEST = r'''
int main(int argc,char **argv)
{
    struct rtl9617c_mdio m={.per=per,.ocp=ocp,.wrap=wrap};
    struct mii_bus bus={.priv=&m};
    CHECK(argc==2);
    if(!strcmp(argv[1],"c22")) {
        per[0xc/4]=0xa5a51234;
        CHECK(rtl_mdio_read(&bus,8,2)==0x1234);
        CHECK(starts==1 && addresses[0]==(8U|(2U<<8)|(3U<<18)|BIT(15)));
        CHECK(rtl_mdio_write(&bus,8,3,0xabcd)==0);
        CHECK(starts==2 && addresses[1]==(8U|(3U<<8)|(1U<<18)) && values[1]==0xabcd);
    } else if(!strcmp(argv[1],"c45")) {
        per[0xc/4]=0x4321;
        CHECK(rtl_mdio_read_c45(&bus,8,7,0x1234)==0x4321);
        CHECK(starts==2 && addresses[0]==(8U|(7U<<8)|BIT(16)) && values[0]==0x1234);
        CHECK(addresses[1]==(8U|(7U<<8)|BIT(16)|BIT(15)|(3U<<18)));
        CHECK(rtl_mdio_write_c45(&bus,8,3,0x8010,0xbeef)==0);
        CHECK(starts==4 && values[2]==0x8010 && values[3]==0xbeef);
        CHECK(addresses[3]==(8U|(3U<<8)|BIT(16)|(1U<<18)));
    } else if(!strcmp(argv[1],"ocp")) {
        CHECK(rtl_mdio_write(&bus,4,31,0xa43)==0);
        CHECK(rtl_mdio_read(&bus,4,31)==0xa43 && starts==0);
        CHECK(rtl_mdio_ocp_offset(&m,4,16)==((3U<<18)|(0xa430U<<2)));
        CHECK(rtl_mdio_ocp_offset(&m,4,2)==((3U<<18)|(0xa404U<<2)));
        CHECK(rtl_mdio_write(&bus,4,16,0xbeef)==0);
        CHECK(rtl_mdio_read(&bus,4,16)==0xbeef && starts==0 && delays==2000);
        CHECK(rtl_mdio_read_c45(&bus,4,7,1)==-EOPNOTSUPP);
    } else if(!strcmp(argv[1],"timeout")) {
        timeout_at=1;
        CHECK(rtl_mdio_read_c45(&bus,8,7,0x1234)==-ETIMEDOUT);
        CHECK(starts==1); /* Never proceed after the failed address phase. */
    } else if(!strcmp(argv[1],"bounds")) {
        CHECK(rtl_mdio_read(&bus,32,2)==-EINVAL);
        CHECK(rtl_mdio_write(&bus,-1,31,0)==-EINVAL);
        CHECK(rtl_mdio_read(&bus,1,32)==-EINVAL);
        CHECK(rtl_mdio_read_c45(&bus,8,32,1)==-EINVAL);
        CHECK(rtl_mdio_write_c45(&bus,8,7,0x10000,0)==-EINVAL);
        CHECK(starts==0);
    } else return 2;
    return 0;
}
'''


class MdioPort(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        source = DRIVER.read_text()
        source = source[source.index('#define MDIO_CFG'):source.index('static int rtl_mdio_reset')]
        test = Path(cls.tmp.name) / 'mdio.c'
        test.write_text(SHIM + source + TEST)
        cls.binary = Path(cls.tmp.name) / 'mdio-test'
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', str(test), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clause22_transactions(self):
        self.run_case('c22')

    def test_clause45_two_phase_transactions(self):
        self.run_case('c45')

    def test_internal_phy_ocp_mapping(self):
        self.run_case('ocp')

    def test_timeout_stops_second_phase(self):
        self.run_case('timeout')

    def test_invalid_addresses_do_not_touch_hardware(self):
        self.run_case('bounds')


if __name__ == '__main__':
    unittest.main()
