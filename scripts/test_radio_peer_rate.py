#!/usr/bin/env python3
"""Check operating-mode RA masks and native RTS WD against the target field contract."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'tx.c').read_text()
start = source.index('static void rtl_ax_tx_wd(')
end = source.index('\nstatic void rtl_ax_tx_checksum', start)
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define BIT_ULL(n) (1ULL<<(n))
#define IEEE80211_HE_PHY_CAP1_LDPC_CODING_IN_PAYLOAD 0x20
#define IEEE80211_HE_PHY_CAP1_HE_LTF_AND_GI_FOR_HE_PPDUS_0_8US 0x40
#define IEEE80211_HE_PHY_CAP7_HE_SU_MU_PPDU_4XLTF_AND_08_US_GI 4
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static u64 get_unaligned_le64(const void *p){u64 v;memcpy(&v,p,8);return v;}
static void put_unaligned_le64(u64 v,void *p){memcpy(p,&v,8);}
static void rtl_ax_store32(void *p,u32 v){memcpy(p,&v,4);}
#include "rate.h"
'''
checks = r'''
int main(void) {
 u8 c[16],full[16],wd[56],rts[56];
 rtl_ax_ra_he(full,1,0xff0,0xfffa,0x60,0,true,2);
 memcpy(c,full,16);rtl_ax_ra_limit(c,0,1,true);
 assert(c[0]==0x24 && c[2]==0x40 && c[3]==0x50);
 assert(get_unaligned_le64(c+4)==0x8000000000fff010ULL);
 memcpy(c,full,16);rtl_ax_ra_limit(c,2,2,true);
 assert(c[0]==0xa4 && c[3]==0x51 && !memcmp(c+4,full+4,8));
 rtl_ax_ra_vht80(c,1,0xff0,0xfffa,true);rtl_ax_ra_limit(c,0,1,true);
 assert(c[0]==0x14 && c[3]==0x40 && !(get_unaligned_le64(c+4)&(BIT_ULL(21)|BIT_ULL(33))));
 assert((get_unaligned_le64(c+4)&0xffffff000000ULL)==0);
 rtl_ax_ra_ht20(c,1,0xfff,0xffff,true);rtl_ax_ra_limit(c,1,2,true);
 assert(c[0]==0x4e && c[2]==0x20 && c[3]==0x41);
 assert(get_unaligned_le64(c+4)==0x80000000ff0ff015ULL);
 rtl_ax_tx_wd(wd,1500,1,0x180,1,2,7,26,false,false,0,0,false,1,64,0);
 rtl_ax_tx_wd(rts,1500,1,0x180|BIT(22),1,2,7,26,false,false,0,0,false,1,64,0);
 assert(get_unaligned_le32(rts+32)==(get_unaligned_le32(wd+32)|BIT(12)));
 rts[33]&=~0x10;assert(!memcmp(wd,rts,56));
 puts("Peer RA: HE20/1SS, HE80/2SS restoration, VHT20 MCS limit, HT40 and native HW RTS descriptor passed");
}
'''
with tempfile.TemporaryDirectory(prefix='radio-peer-rate-') as temp:
    work = Path(temp)
    (work / 'test.c').write_text(fixture + source[start:end] + checks)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-I' + str(driver), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
