#!/usr/bin/env python3
"""Validate native OAM container parsing against the RTL8277C DAL format."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
headers = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
code = r'''
#include <assert.h>
#include <string.h>
#include "rtl9617c-pon-oam-format.h"
int main(void) {
    unsigned char prefix[16];
    /* Literal Ethernet container from dal_rt_rtl8277c_epon_oam_tx. */
    const unsigned char expected[] = {
        0,0x13,0x25,0,0,31, 0,0x13,0x25,0,0,1, 0xff,0xf1,0,0
    };
    rtl_oam_prefix(prefix,31);
    assert(memcmp(prefix,expected,16)==0);
    unsigned char rx[80] = {
        0,0x13,0x25,0,0,1, 0,0x13,0x25,0,0,31, 0xff,0xf1,0,0,
        1,0x80,0xc2,0,0,2, 0x98,0x1e,0x89,0x61,0x68,0xd0, 0x88,9,3,0
    };
    unsigned int llid=99;
    assert(rtl_oam_receive_offset(rx,76,&llid)==16 && llid==31);
    assert(rtl_oam_is_frame(rx+16,60));
    for (unsigned int length=0; length<31; length++) {
        unsigned char packet[80];
        memcpy(packet,rx,length);
        assert(rtl_oam_receive_offset(packet,length,&llid)==0);
    }
    unsigned char vlan[84];
    memcpy(vlan,rx,12);
    vlan[12]=0x81; vlan[13]=0; vlan[14]=0; vlan[15]=100;
    memcpy(vlan+16,rx+12,64);
    assert(rtl_oam_receive_offset(vlan,80,&llid)==20 && llid==31);
    rx[30]=1; /* Slow protocols with another subtype belong elsewhere. */
    assert(rtl_oam_receive_offset(rx,76,&llid)==0);
    rx[30]=3; rx[0]=1;
    assert(rtl_oam_receive_offset(rx,76,&llid)==0);
    return 0;
}
'''
with tempfile.TemporaryDirectory(dir=root / "artifacts") as directory:
    source = Path(directory) / "pon-oam-format.c"
    binary = Path(directory) / "pon-oam-format"
    source.write_text(code)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-I", str(headers), str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("RTL8277C OAM prefix, RX/VLAN decoding and truncated-frame cases passed")
