#!/usr/bin/env python3
"""Compare native bandwidth/RF/TPU/WD output with the original ELF traces.

Compile the production function bodies unchanged. Only hardware IO and the
power-query boundary are replaced; signed values come from the same contract
used while executing the original constructors in Unicorn.
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'

def function(file, name):
    text = (driver / file).read_text()
    start = text.rfind('\n', 0, text.index(name + '(')) + 1
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'

source = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "power.h"
typedef uint8_t u8; typedef int8_t s8; typedef uint16_t u16; typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
#define RTL_AX_PHY_STAGES 2
#define RTL_AX_PHY_FEM 1
#define strscpy(d,s,n) snprintf(d,n,"%s",s)
struct rtl_ax_channel {bool configured;u8 band,primary,center,bandwidth;s8 external_loss[2];u32 rf18[2][2];};
static u32 registers[32768], rf18[2][2];
static u32 read32(void *ctx, unsigned int reg){return registers[reg/4];}
static void write32(void *ctx,unsigned int reg,u32 value){registers[reg/4]=value;}
static void delay(void *ctx,unsigned int usec){}
static const struct rtl_ax_io io={.read32=read32,.write32=write32,.delay_us=delay};
static int rtl_ax_phy_rf_read(const struct rtl_ax_io *io,unsigned int path,u32 reg,u32 *value){*value=rf18[path][reg==0x10018];return 0;}
static int rtl_ax_phy_rf_write_masked(const struct rtl_ax_io *io,unsigned int path,u32 reg,u32 mask,u32 value){if(reg==0x18||reg==0x10018)rf18[path][reg==0x10018]=value;return 0;}
static void rtl_ax_store32(u8 *out,u32 value){memcpy(out,&value,4);}
static void rtl_ax_tx_shape_zero(const struct rtl_ax_io *io,unsigned int chip){}
'''
for header in ('limits.h', 'power-tables.h'):
    source += '\n'.join(line for line in (driver / header).read_text().splitlines()
                        if not line.startswith('#include')) + '\n'
source += r'''
static int rtl_ax_channel_power(const struct rtl_ax_limits *limits,
 const struct rtl_ax_limit_key *key,const char *regulation,int *value){
 if(key->ru)*value=(key->channel*3+key->bandwidth*11+key->ntx*13)%161-80;
 else *value=(key->channel*3+key->bandwidth*11+key->bf*7+key->ntx*13+(key->rate==1?17:0))%161-80;
 return 0;
}
'''
for file, names in [('power-tables.c', ('rtl_ax_half_dbm', 'rtl_ax_power_apply')),
                    ('channel.h', ('rtl_ax_channel_field', 'rtl_ax_channel_primary_index', 'rtl_ax_c_adc', 'rtl_ax_c_rf_5g')),
                    ('tx.c', ('rtl_ax_tx_wd',))]:
    source += ''.join(function(file, name) for name in names)
source += r'''
int main(int argc,char **argv){
 unsigned int width=atoi(argv[1]),primary=atoi(argv[2]),center=atoi(argv[3]);
 unsigned int bw=width==80?2:width==40;
 if(argc==7){u8 wd[56];rtl_ax_tx_wd(wd,1536,1,atoi(argv[4]),atoi(argv[5]),bw,7,26,false,false,0,0,false,1,atoi(argv[6]),atoi(argv[6])?4:0);fwrite(wd,1,56,stdout);return 0;}
 struct rtl_ax_power_inputs inputs={};struct rtl_ax_power_hardware state={};struct rtl_ax_channel channel={};
 if(rtl_ax_power_apply(&io,RTL_AX_8852C,&inputs,1,primary,center,bw,"WW",100,&state))return 1;
 fwrite(state.readback+11,4,32,stdout);
 rf18[0][0]=0xe5a5a;rf18[0][1]=0xc5a5a;rf18[1][0]=0xa5a5a;rf18[1][1]=0x85a5a;
 if(rtl_ax_c_rf_5g(&io,center,bw,&channel))return 2;
 fwrite(rf18,4,4,stdout);
 memset(registers,0,sizeof(registers));rtl_ax_c_adc(&io,0,bw);rtl_ax_c_adc(&io,1,bw);
 fwrite(registers,1,sizeof(registers),stdout);
 return 0;
}
'''
vectors, wd_vectors = 0, 0
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-wideband-') as directory:
    work = Path(directory)
    program, binary = work / 'native.c', work / 'native'
    program.write_text(source)
    subprocess.run(['cc', '-O2', '-Wall', '-Wno-unused-function', '-Wno-unused-parameter',
                    '-I' + str(driver), str(program), '-o', str(binary)], check=True)
    bandwidth = json.loads((root / 'manifests/radio-stock-bandwidth.json').read_text())['records']
    rf = json.loads((root / 'manifests/radio-stock-RF-bandwidth.json').read_text())['records']
    for width in (20, 40, 80):
        center = {20: 36, 40: 38, 80: 42}[width]
        primary = center if width == 20 else center - width // 10 + 2
        data = subprocess.check_output([str(binary), str(width), str(primary), str(center)])
        expected = json.loads((root / f'manifests/radio-stock-power-contract-{width}.json').read_text())['records']
        power = [w['value'] for r in expected if r['chip'] == '8852c' and r['kind'] in ('limit', 'ru') for w in r['writes']]
        assert list(struct.unpack('<32I', data[:128])) == [int(v, 16) for v in power], width
        expected_rf = next(r for r in rf if r['width_MHz'] == width)['final_RF18']
        assert list(struct.unpack('<4I', data[128:144])) == [int(v, 16) for v in expected_rf.values()], width
        adc = next(r for r in bandwidth if r['chip'] == '8852c' and r['width_MHz'] == width)['writes']
        for w in adc:
            address = int(w['address'], 16)
            if address not in (0x12a0, 0x32a0) and not 0xc000 <= address < 0xc200:
                continue
            mask = int(w['mask'], 16)
            shift = (mask & -mask).bit_length() - 1
            value = struct.unpack_from('<I', data, 144 + (address | 0x10000))[0]
            assert (value & mask) >> shift == int(w['value'], 16), (width, w)
        vectors += 1
    for width in (20, 40, 80):
        rows = json.loads((root / f'manifests/radio-stock-VHT{width}-WD.json').read_text())['records']
        for r in rows:
            data = subprocess.check_output([str(binary), str(width), '36', '42', str(r['rate']),
                                            str(r['GI']), str(r['maximum_aggregation'])])
            assert list(struct.unpack('<14I', data)) == [int(w, 16) for w in r['WD_words']], r
            wd_vectors += 1
report = {'scope': __doc__, 'native_source_hashes': {
    f: hashlib.sha256((driver / f).read_bytes()).hexdigest() for f in ('channel.h', 'power-tables.c', 'tx.c')},
    'C_20_40_80_signed_TPU_RF18_ADC_vectors': vectors,
    'VHT_20_40_80_WD_vectors': wd_vectors,
    'hardware_IO_and_power_query_only_mocked': True, 'hardware_test': False}
(root / 'manifests/radio-wideband-contract-test.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
