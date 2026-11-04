#!/usr/bin/env python3
"""Check operating-mode RA masks and native RTS WD against the target field contract."""
from pathlib import Path
import struct
import subprocess
import sys
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
#include <stdlib.h>
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
int main(int argc,char **argv) {
 u8 c[16],full[16],wd[56],rts[56];
 if(argc==3) {
  rtl_ax_tx_wd(wd,1500,1,0x180,1,2,atoi(argv[2]),26,false,false,0,0,false,1,atoi(argv[1]),0);
  fwrite(wd,1,sizeof(wd),stdout);return 0;
 }
 rtl_ax_ra_legacy(c,1,0xfff,false);assert(c[0]==6 && c[15]==0x80 && get_unaligned_le64(c+4)==(BIT_ULL(63)|0xff5));
 rtl_ax_ra_legacy(c,1,0xff0,true);assert(c[0]==4 && c[15]==0x20 && get_unaligned_le64(c+4)==(BIT_ULL(63)|0xff0));
 rtl_ax_ra_he(full,1,0xff0,0xfffa,0x60,0,true,2);
 memcpy(c,full,16);rtl_ax_ra_limit(c,0,1,true);
 assert(c[0]==0x24 && c[2]==0x40 && c[3]==0x50);
 assert(get_unaligned_le64(c+4)==0x8000000000fff010ULL);
 memcpy(c,full,16);rtl_ax_ra_limit(c,2,2,true);
 assert(c[0]==0xa4 && c[3]==0x51 && !memcmp(c+4,full+4,8));
 rtl_ax_ra_vht80(c,1,0xff0,0xfffa,true);rtl_ax_ra_limit(c,0,1,true);
 assert(c[0]==0x14 && c[3]==0x40 && !(get_unaligned_le64(c+4)&(BIT_ULL(21)|BIT_ULL(33))));
 assert((get_unaligned_le64(c+4)&0xffffff000000ULL)==0);
 rtl_ax_ra_ht20(c,1,0xfff,0xffff,true,false);rtl_ax_ra_limit(c,1,2,true);
 assert(c[0]==0x4e && c[2]==0x20 && c[3]==0x41);
 assert(get_unaligned_le64(c+4)==0x80000000ff0ff015ULL);
 rtl_ax_ra_ht20(c,1,0xff0,0xffff,true,true);rtl_ax_ra_limit(c,1,2,true);
 assert(c[0]==0x4c && c[2]==0x20 && c[3]==0x41 && c[15]==0x20);
 assert(get_unaligned_le64(c+4)==0x80000000ff0ff010ULL);
 rtl_ax_tx_wd(wd,1500,1,0x180,1,2,7,26,false,false,0,0,false,1,64,0);
 rtl_ax_tx_wd(rts,1500,1,0x180|BIT(22),1,2,7,26,false,false,0,0,false,1,64,0);
 assert(get_unaligned_le32(rts+32)==get_unaligned_le32(wd+32));
 assert(get_unaligned_le32(rts+48)==(BIT(27)|BIT(31)));
 memset(rts+48,0,4);assert(!memcmp(wd,rts,56));
 const unsigned rates[]={4,0x80,0xff,0x100,0x11b,0x180,0x19b};
 for(unsigned i=0;i<sizeof(rates)/sizeof(rates[0]);i++)
  for(unsigned bytes=2300;bytes<=2301;bytes++) {
   rtl_ax_tx_wd(wd,bytes,1,rates[i],1,2,7,26,false,true,0,0,false,1,64,0);
   assert(get_unaligned_le32(wd+36)==(BIT(11)|63|(bytes>2300?(rates[i]&0x180U)<<16:0)));
  }
 for(unsigned aggregate=0;aggregate<=64;aggregate+=64)
  for(unsigned sequence=7;sequence<=8;sequence++) {
   rtl_ax_tx_wd(wd,1500,1,0x180,1,2,sequence,26,false,false,0,0,false,1,aggregate,0);
   assert(get_unaligned_le32(wd+12)==(sequence|(aggregate?BIT(12):BIT(13))));
  }
 puts("Peer RA: HE20/1SS, HE80/2SS restoration, VHT20 MCS limit, HT40 and native HW RTS descriptor passed");
}
'''
with tempfile.TemporaryDirectory(prefix='radio-peer-rate-') as temp:
    work = Path(temp)
    (work / 'test.c').write_text(fixture + source[start:end] + checks)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-I' + str(driver), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
    sys.path.insert(0, str(root / 'tools'))
    from arm64_module_oracle import ModuleOracle
    from unicorn.arm64_const import UC_ARM64_REG_X19, UC_ARM64_REG_X20

    def callback(name, args):
        if name == 'platform_memcpy':
            oracle.uc.mem_write(args[1], bytes(oracle.uc.mem_read(args[2], args[3])))
            return 0
        assert name == 'printk', name
        return 0

    names = ['fill_txreq_mdata'] + [name+'_'+chip for chip in ('8852c', '8192xb')
                                  for name in ('txdes_proc_data', 'mac_txdesc_len')]
    oracle = ModuleOracle(root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko', names, callback)
    adapter, platform, hwinfo, metadata, output, frame = [0x6000000+n*0x10000 for n in range(6)]
    for address in (adapter, platform, hwinfo, metadata, output, frame):
        oracle.uc.mem_map(address, 0x10000)
    dbcc, wmm = 0x6060000, 0x6070000
    for address in (dbcc, wmm):
        oracle.uc.mem_map(address, 0x10000)
    oracle.word(adapter+1464, dbcc)
    oracle.word(dbcc+64, wmm)
    oracle.uc.mem_write(wmm, bytes([4])*32)
    oracle.word(adapter+24, platform)
    oracle.word(adapter+88, hwinfo)
    oracle.word(platform+88, oracle.callback('platform_memcpy'))
    oracle.uc.mem_write(hwinfo+8, struct.pack('<I', 2))
    for aggregate in (0, 64):
        for sequence in (7, 8):
            data = bytearray(96)
            struct.pack_into('<H', data, 8, 1500)
            struct.pack_into('<H', data, 14, 1)
            struct.pack_into('<H', data, 18, sequence)
            data[20], data[33] = 13, 0x20
            struct.pack_into('<Q', data, 32, int.from_bytes(data[32:40], 'little') | aggregate<<32)
            oracle.uc.mem_write(metadata, bytes(data))
            oracle.uc.mem_write(frame+0x64, bytes([bool(aggregate)]))
            # Execute the actual packet-capability branch, including the
            # frame's AMPDU dispatch. Other fill_txreq_mdata fields are input.
            oracle.uc.reg_write(UC_ARM64_REG_X19, frame)
            oracle.uc.reg_write(UC_ARM64_REG_X20, metadata-32)
            entry = oracle.functions['fill_txreq_mdata']-0xe5760
            oracle.uc.emu_start(entry+0xe589c, entry+(0xe5b24 if aggregate else 0xe58b8), count=20)
            for chip in ('8852c', '8192xb'):
                oracle.uc.mem_write(output, bytes(56))
                assert oracle.call('txdes_proc_data_'+chip, adapter, metadata, output, 56) == 0
                original = oracle.unpack(output+12, 'I')[0]
                native = subprocess.check_output([str(work/'test'), str(aggregate), str(sequence)])
                assert struct.unpack_from('<I', native, 12)[0] == original, (chip,aggregate,sequence)
    print('ARM WD: eight original C/XB AMPDU/non-AMPDU packet-capability and encoder matches passed')
