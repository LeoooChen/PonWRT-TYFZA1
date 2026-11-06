#!/usr/bin/env python3
"""Check C/XB CCTL aggregation packing against the shipped encoder contract."""
from pathlib import Path
import json
import re
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'role.h').read_text()
monitor = (driver / 'monitor.h').read_text()
start = source.index('static inline void rtl_ax_station_cmac_connected(')
end = source.index('\nstatic inline ', start + 1)
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
typedef uint32_t u32;
typedef uint8_t u8;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static void rtl_ax_store32(unsigned char *out, u32 value) { memcpy(out, &value, 4); }
static u32 rtl_ax_ap_read32(const void *p) { u32 value; memcpy(&value,p,4); return value; }
'''
operational = monitor[monitor.index('\tcase IEEE80211_AMPDU_TX_OPERATIONAL:'):monitor.index('\tcase IEEE80211_AMPDU_TX_STOP_CONT:')]
operational = operational[operational.index('\n')+1:]
limit = re.search(r'hw->max_tx_aggregation_subframes = [^;]+;', monitor)[0]
client_start = source.index('static inline void rtl_ax_station_cmac_client(')
client_end = source.index('\nstatic inline ', client_start + 1)
common += source[start:end] + source[client_start:client_end] + r'''
#define min_t(type,a,b) ((type)(a)<(type)(b)?(type)(a):(type)(b))
#define WRITE_ONCE(a,b) ((a)=(b))
#define dev_info(...) ((void)0)
#define RTL_AX_8852C 0x52
#define RTL_AX_H2C_DATA 1
struct ieee80211_hw { unsigned max_tx_aggregation_subframes; };
struct rtl_ax_ap { unsigned chip; char io; void *tx,*sta_vif; bool ra_registered[128];
 struct { unsigned aggregation[16]; } tx_ba[128]; };
struct rtl_ax_station { unsigned macid; };
struct ieee80211_ampdu_params { unsigned buf_size,tid; };
static u32 bk=0x80, global=0x12340000, peer[128];
static u8 command[68];
static void rtl_ax_update8(void *io,unsigned reg,unsigned mask,unsigned value) {
 (void)io;assert(reg==0xc604);bk=(bk&~mask)|value;
}
static void rtl_ax_update32(void *io,unsigned reg,unsigned mask,unsigned value) {
 (void)io;assert(reg==0xc610);global=(global&~mask)|value;
}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned b,unsigned c,unsigned cl,unsigned f,
 const u8 *data,unsigned n,bool ack) {
 (void)r;assert(b==1&&c==1&&cl==5&&f==2&&n==68&&ack);memcpy(command,data,n);return 0;
}
static void rtl_ax_tx_peer_ba(void *tx,unsigned id,unsigned tid,unsigned n) {
 (void)tx;assert((id==1||id==2)&&tid==3);peer[id]=n;
}
static void rtl_ax_offload_ready(struct rtl_ax_ap *r) { (void)r; }
static int operational(struct rtl_ax_ap *radio,struct ieee80211_hw *hw,unsigned requested,unsigned id) {
 (void)hw;
 struct ieee80211_ampdu_params p={requested,3},*params=&p;
 struct rtl_ax_station s={id},*station=&s;
 unsigned aggregate=0;int ret;u8 cmac[68];
''' + operational + '\n}\n'
test = r'''
int main(int argc,char **argv)
{
    if(argc==3) {
        u8 output[68];rtl_ax_station_cmac_connected(output,1,atoi(argv[1]),atoi(argv[2]));
        fwrite(output,1,68,stdout);return 0;
    }
    if(argc==5) {
        struct rtl_ax_ap r={.chip=atoi(argv[2])},*radio=&r;
        struct ieee80211_hw h={},*hw=&h;
        LIMIT
        assert(!operational(radio,hw,atoi(argv[3]),1));
        assert(global==0xa034007f && peer[1]==(unsigned)atoi(argv[3]));
        u32 first=rtl_ax_ap_read32(command+16);
        assert(!operational(radio,hw,atoi(argv[4]),2));
        assert(global==0xa034007f && bk==0x80);
        assert(peer[1]==(unsigned)atoi(argv[3]) && r.tx_ba[1].aggregation[3]==peer[1]);
        assert(peer[2]==(unsigned)atoi(argv[4]) && r.tx_ba[2].aggregation[3]==peer[2]);
        assert(first==(peer[1]==128?0x17e:0x3e));
        assert(rtl_ax_ap_read32(command+16)==(peer[2]==128?0x17e:0x3e));
        return 0;
    }
    if(argc==4) {
        struct rtl_ax_ap r={.chip=atoi(argv[2])},*radio=&r;
        struct ieee80211_hw h={},*hw=&h;
        LIMIT
        assert(hw->max_tx_aggregation_subframes==128);
        assert(!operational(radio,hw,atoi(argv[3]),1));
        u32 values[]={bk,global,peer[1],r.tx_ba[1].aggregation[3]};
        fwrite(values,4,4,stdout);fwrite(command,1,68,stdout);return 0;
    }
    for (unsigned int band = 0; band < 2; band++) {
        unsigned char plain[68], output[68];
        rtl_ax_station_cmac_connected(plain, 7, band, 0);
        for (unsigned int count = 1; count <= 128; count++) {
            u32 value, mask;
            rtl_ax_station_cmac_connected(output, 7, band, count);
            memcpy(&value, output + 16, 4);
            memcpy(&mask, output + 48, 4);
            /* Board HAL count-1, then C/XB V1 encoder decrements if nonzero. */
            unsigned int expected = count - 1;
            if (expected) expected--;
            if (count>64) expected |= BIT(8);
            assert(value == expected && mask == 0x3ff);
            memcpy(output + 16, plain + 16, 4);
            memcpy(output + 48, plain + 48, 4);
            assert(!memcmp(output, plain, sizeof(output)));
        }
    }
    return 0;
}
'''.replace('LIMIT', limit)
with tempfile.TemporaryDirectory(prefix='cmac-aggregation-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
    stock = json.loads((root/'manifests/radio-stock-station-tables.json').read_text())['HT_aggregation_updates']
    ampdu_stock = json.loads((root/'manifests/radio-stock-AMPDU-config.json').read_text())
    for chip,band,ic in (('8852c',1,0x52),('8192xb',0,0x92)):
        initialization = next(r for r in ampdu_stock['initialization_callers'] if r['chip']==chip)
        hardware_count = initialization['maximum_aggregation']
        assert hardware_count == 128
        assert int(initialization['writes'][1]['value'],16) & 255 == hardware_count-1
        for count in (32,64,128):
            row = next(r for r in stock if r['chip']==chip and r['maximum_aggregation']==count)
            expected = bytes.fromhex(next(o['payload'] for o in row['operations'] if 'payload' in o))
            assert subprocess.check_output([str(path/'test'),str(band),str(count)]) == expected, (chip,count)
        for first,second in ((128,64),(64,128)):
            subprocess.run([str(path/'test'),'mixed',str(ic),str(first),str(second)],check=True)
        for requested,count in ((64,64),(128,128),(256,128)):
            output = subprocess.check_output([str(path/'test'),'action',str(ic),str(requested)])
            writes = {int(w['address'],16):int(w['value'],16) for w in initialization['writes']}
            assert struct.unpack('<4I',output[:16]) == (writes[0xc604],writes[0xc610],count,count), (chip,requested)
            original = next(r for r in stock if r['chip']==chip and r['maximum_aggregation']==count)
            cmac = bytes.fromhex(next(o['payload'] for o in original['operations'] if 'payload' in o))
            assert output[16+16:16+20] == cmac[16:20] and output[16+48:16+52] == cmac[48:52]
print('C/XB TX BA: counts1..128; six complete original CMAC payloads; original init global128/time160 with peer64/128; mixed128->64 and64->128 retain independent peer/CMAC limits passed')
