#!/usr/bin/env python3
"""Compile production NAND callbacks; verify datasheet status/OOB/register contracts.

Usage: test_nand_batch.py --kernel <patched Linux tree> [--uboot <prepared tree>]
"""
import argparse
import ctypes as C
from pathlib import Path
import re
import subprocess
import tempfile


def function(text, name):
    start = re.search(r'static (?:int|void) ' + name + r'\(', text).start()
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def library(text, directory, name):
    source = directory / (name + '.c')
    output = directory / (name + '.so')
    source.write_text(text)
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror',
                    '-Wno-unused-function', '-o', str(output), str(source)], check=True)
    return C.CDLL(str(output))


def check_status(callback, expected):
    for status in range(256):
        actual = callback(status)
        assert actual == expected[(status >> 4) & 7], (hex(status), actual, expected)


def check_registers(lib):
    for on in (0, 1):
        for initial in range(256):
            assert lib.regtest(on, initial, 0) == (initial & ~16) | (16 if on else 0)
        assert lib.regtest(on, 0, -5) == -5
        assert lib.regtest(on, 0, -30) == -30


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--uboot', type=Path)
    args = parser.parse_args()
    ds = (args.kernel / 'drivers/mtd/nand/spi/dosilicon.c').read_text()
    fm = (args.kernel / 'drivers/mtd/nand/spi/fmsh.c').read_text()
    core = (args.kernel / 'drivers/mtd/nand/spi/core.c').read_text()
    header = (args.kernel / 'include/linux/mtd/spinand.h').read_text()
    assert '&dosilicon_spinand_manufacturer,' in core
    assert 'dosilicon.o' in (args.kernel / 'drivers/mtd/nand/spi/Makefile').read_text()
    assert re.search(r'\.id\s*=\s*0xe5\b', ds)
    assert re.search(r'#define SPINAND_MFR_FMSH\s+0xA1\b', fm)
    common = '''#include <stdint.h>
#include <stdbool.h>
#include <errno.h>
typedef uint8_t u8;
#define GENMASK(h,l) (((1U << ((h)-(l)+1))-1) << (l))
struct mtd_info { int unused; };
struct mtd_oob_region { unsigned int offset, length; };
struct spinand_device { unsigned int flags; };
'''
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name, source, expected, ecc_regions, free_regions, org in (
            ('ds35q2gbs', ds, [0, 3, -74, 6, -22, 8, -22, -22],
             [(64, 64)], [(2, 62)], '1, 2048, 128, 64, 2048, 40, 1, 1, 1'),
            ('fm25g02d', fm, [0, 1, 2, 3, 4, -22, -22, -74],
             [(8 + 16*i, 8) for i in range(4)], [(2 + 16*i, 6) for i in range(4)],
             '1, 2048, 64, 64, 2048, 41, 1, 1, 1'),
        ):
            chip = 'DS35Q2GBS' if name.startswith('ds') else 'FM25G02D'
            entry = source[source.index('SPINAND_INFO("' + chip + '"'):].split(')),', 1)[0]
            assert 'NAND_MEMORG(' + org + ')' in entry
            assert 'OPCODE_DUMMY, 0x' + ('b2' if name.startswith('ds') else 'f2') in entry
            assert 'SPINAND_HAS_QE_BIT' in entry
            assert f'NAND_ECCREQ({8 if name.startswith("ds") else 4}, 512)' in entry
            if name.startswith('fm'):
                assert 'SPINAND_ECC_REG_90' in entry
            code = common + ''.join(function(source, name + suffix) for suffix in
                                    ('_ecc_get_status', '_ooblayout_ecc', '_ooblayout_free'))
            code += f'''int status(unsigned int s) {{ return {name}_ecc_get_status(0,s); }}
int layout(int ecc, int section, struct mtd_oob_region *r) {{
 return ecc ? {name}_ooblayout_ecc(0,section,r) : {name}_ooblayout_free(0,section,r);
}}
'''
            lib = library(code, directory, name)
            check_status(lib.status, expected)
            for kind, regions in ((1, ecc_regions), (0, free_regions)):
                for section, region in enumerate(regions):
                    actual = (C.c_uint * 2)()
                    assert lib.layout(kind, section, actual) == 0
                    assert tuple(actual) == region
                assert lib.layout(kind, len(regions), actual) == -34
        flag = re.search(r'#define SPINAND_ECC_REG_90[^\n]+', header).group()
        code = common + '\n#define BIT(n) (1U << (n))\n' + flag + '''
#define CFG_ECC_ENABLE 16
static int value, failure, reads, writes, read_reg, write_reg, generic_calls;
static int spinand_read_reg_op(void *s,int r,u8 *v) { reads++; read_reg=r; if(failure == -5) return failure; *v=value; return 0; }
static int spinand_write_reg_op(void *s,int r,u8 v) { writes++; write_reg=r; if(failure == -30) return failure; value=v; return 0; }
static int spinand_upd_cfg(void *s,int mask,int v) { generic_calls++; return 123; }
''' + function(core, 'spinand_ecc_enable') + '''
int regtest(int on,int initial,int fail) {
 struct spinand_device s={SPINAND_ECC_REG_90};
 value=initial; failure=fail; reads=writes=read_reg=write_reg=generic_calls=0;
 int ret=spinand_ecc_enable(&s,on);
 if(reads!=1 || read_reg!=0x90 || generic_calls) return -999;
 if(writes!=(fail==-5?0:1) || (writes && write_reg!=0x90)) return -998;
 if(fail && value!=initial) return -997;
 return ret ? ret : value;
}
int legacy(void) {struct spinand_device s={0}; return spinand_ecc_enable(&s,1);}
'''
        lib = library(code, directory, 'registers')
        check_registers(lib)
        assert lib.legacy() == 123
        if args.uboot:
            source = (args.uboot / 'board/realtek/tyfza1/vendor/spi_nand/cortina_spi_nand_base.c').read_text()
            inc = (args.uboot / 'board/realtek/tyfza1/nand-batch.inc').read_text()
            for chip, mid, did, oob, strength in [('DS35Q2GBS-IB', 'E5', 'B2', '128', 8),
                                                ('FM25G02D', 'A1', 'F2', '64', 4)]:
                entry = next(line for line in source.splitlines() if 'SPI_NAND_INFO("' + chip + '"' in line)
                assert f'0x{mid}, 0x{did}, PAGE_2KB, OOB_{oob}B, PAGES_PER_BLK, 2048, 1, {strength}, SPI_NAND_NEED_QE_ENABLE)' in entry
            code = common + '''
struct nand_ecclayout {int eccbytes; int eccpos[128]; int oobavail; struct {int offset,length;} oobfree[8];};
struct spi_nand_info {int mfr_id,dev_id;};
#define SPI_NAND_MFR_FMSH 0xa1
#define CFG_ECC_ENABLE 16
#define CFG_ECC_MASK 16
static int value,failure,reads,writes,read_reg,write_reg;
static int spi_nand_get_feature_register(void *s,int r,u8 *v) {reads++;read_reg=r;if(failure == -5) return failure;*v=value;return 0;}
static int spi_nand_set_feature_register(void *s,int r,u8 *v) {writes++;write_reg=r;if(failure == -30) return failure;value=*v;return 0;}
static int spi_nand_get_cfg(void *s,u8 *v) {return spi_nand_get_feature_register(s,0xb0,v);}
static int spi_nand_set_cfg(void *s,u8 *v) {return spi_nand_set_feature_register(s,0xb0,v);}
'''
            dispatch = ''.join(function(source, 'spi_nand_' + name + '_ecc_status') for name in ('dosilicon', 'fmsh'))
            dependencies = set(re.findall(r'(spi_nand_\w+)\(info,', dispatch))
            for dep in sorted(dependencies):
                if dep not in inc:
                    code += f'static void {dep}(void *i,uint32_t s,uint32_t *c,uint32_t *e) {{*c=99;}}\n'
            code += inc + dispatch
            code += function(source, 'spi_nand_enable_ecc') + function(source, 'spi_nand_disable_ecc')
            code += '''
int status(unsigned int s,int ds,int id) {
 struct spi_nand_info info={ds?0xe5:0xa1,id}; uint32_t c=0,e=0;
 if(ds) spi_nand_dosilicon_ecc_status(&info,s,&c,&e);
 else spi_nand_fmsh_ecc_status(&info,s,&c,&e);
 return e ? -74 : c;
}
int regtest(int on,int initial,int fail) {
 struct spi_nand_info info={0xa1,0xf2}; value=initial;failure=fail;reads=writes=read_reg=write_reg=0;
 int ret=on?spi_nand_enable_ecc(&info):spi_nand_disable_ecc(&info);
 if(reads!=1 || read_reg!=0x90) return -999;
 if(writes!=(fail==-5?0:1) || (writes && write_reg!=0x90)) return -998;
 if(fail && value!=initial) return -997;
 return ret ? ret : value;
}
int layout(void) {
 if (fm25g02d_ecc_layout.eccbytes!=32 || fm25g02d_ecc_layout.oobavail!=24) return 1;
 for(int i=0;i<32;i++) if(fm25g02d_ecc_layout.eccpos[i]!=8+(i/8)*16+i%8) return 2;
 for(int i=0;i<4;i++) if(fm25g02d_ecc_layout.oobfree[i].offset!=2+16*i || fm25g02d_ecc_layout.oobfree[i].length!=6) return 3;
 return 0;
}
'''
            lib = library(code, directory, 'uboot')
            check_status(lambda s: lib.status(s, 1, 0xb2), [0, 3, -74, 6, -74, 8, -74, -74])
            check_status(lambda s: lib.status(s, 0, 0xf2), [0, 1, 2, 3, 4, -74, -74, -74])
            # Existing parts retain their original callbacks.
            assert lib.status(0, 1, 0xf2) == 99
            assert lib.status(0, 0, 0xd2) == 99
            check_registers(lib)
            assert lib.layout() == 0
    print('PASS: NAND IDs/geometry, 256 status values per chip, OOB boundaries, ECC register/error paths')


if __name__ == '__main__':
    main()
