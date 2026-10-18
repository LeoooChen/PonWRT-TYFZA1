#!/usr/bin/env python3
"""Compare native TYFZA1 PHY initialization with its actual OEM ARM64 module."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'tools'), str(root / 'analysis/python-deps')]
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X4

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--stock-module', required=True, type=Path)
args = parser.parse_args()
header = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/phy/realtek/rtl8221b-tyfza1.h'

mdio = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/mdio/mdio-rtl9617c.c').read_text()
reset_source = mdio[mdio.index('static int rtl_mdio_reset('):mdio.index('static int rtl_mdio_probe(')]


def stock_enable():
    events = []

    def io(name, values):
        if name in ('printk', 'rtk_rtl8221b_proc_init'):
            return 0
        if name == 'gpio_to_desc':
            assert values[0] == 14
            return 14
        if name in ('gpio_request', 'gpio_free'):
            events.append([name, values[0]])
            return 0
        if name == 'gpiod_direction_output_raw':
            events.append([name, values[0], values[1]])
            return 0
        if name == '__const_udelay':
            # ARM64 __udelay(1000): 1000 * 0x10c7.
            assert values[0] == 1000 * 0x10c7
            events.append(['delay_ms', 1])
            return 0
        raise AssertionError((name, values))

    oracle = ModuleOracle(args.stock_module, ['init_module'], io)
    assert oracle.call('init_module', instruction_budget=100000) == 0
    assert events[:3] == [['gpio_request', 14], ['gpiod_direction_output_raw', 14, 1], ['gpio_free', 14]]
    return sum(event[1] for event in events if event[0] == 'delay_ms')


def stock(revision, version, seed, ack=4):
    registers, events = {}, []

    def io(name, values):
        if name == 'MmdPhyRead':
            mmd, reg = values[2], values[3] & 0xffff
            value = registers.get((mmd, reg), seed)
            if (mmd, reg) == (31, 0xa420):
                value = 3
            if (mmd, reg) == (1, 3):
                value = 0xc840 | revision
            if (mmd, reg) == (31, 0xa438) and registers.get((31, 0xa436)) == 0x801e:
                value = version
            if (mmd, reg) == (31, 0xb800):
                value = 0x40 if registers.get((31, 0xb820), 0) & 0x10 else 0
            if (mmd, reg) == (31, 0xa434):
                value = ack
            oracle.uc.mem_write(oracle.uc.reg_read(UC_ARM64_REG_X4), struct.pack('<H', value))
            events.append(['r', mmd, reg, value])
            return 1
        if name == 'MmdPhyWrite':
            mmd, reg = values[2], values[3] & 0xffff
            value = oracle.uc.reg_read(UC_ARM64_REG_X4) & 0xffff
            registers[mmd, reg] = value
            events.append(['w', mmd, reg, value])
            return 1
        if name in ('printk', 'osal_time_udelay'):
            return 0
        raise AssertionError((name, values))

    oracle = ModuleOracle(args.stock_module, ['Rtl8221_phy_init', 'Rtl8221_enable_set',
        'Rtl8221_wait_for_bit.constprop.0', 'Rtl8221_serdes_option_set'], io)
    assert oracle.call('Rtl8221_phy_init', 0, 7, instruction_budget=500000) == 1
    assert oracle.call('Rtl8221_serdes_option_set', 0, 7, 1) == 1
    return events


shim = r'''
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint16_t u16;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define MDIO_MMD_PMAPMD 1
#define MDIO_MMD_VEND1 30
#define MDIO_MMD_VEND2 31
#define MII_PHYSID2 3
#define MDIO_CTRL1 0
#define MDIO_CTRL1_LPOWER BIT(11)
struct phy_driver {};
struct phy_device {
 bool is_c45;
 struct {struct {void *of_node;} dev;} mdio;
 struct {unsigned int device_ids[32];} c45_ids;
};
static unsigned int revision,version,seed,accesses,fail_at,timeout_reg;
static bool board;
static u16 registers[32][65536];
unsigned int count,errors,warnings;
unsigned int events[8192][4];
#define phydev_err(p,...) ((void)(p),errors++)
#define phydev_warn(p,...) ((void)(p),warnings++)
static int event(unsigned int kind,unsigned int mmd,unsigned int reg,unsigned int value) {
 events[count][0]=kind;events[count][1]=mmd;events[count][2]=reg;events[count++][3]=value;
 return ++accesses == fail_at ? -EIO : 0;
}
static int phy_read_mmd(struct phy_device *p,int mmd,u16 reg) {
 (void)p;
 unsigned int value=registers[mmd][reg];
 if(mmd==31 && reg==0xa420)value=timeout_reg==reg?0:3;
 if(mmd==1 && reg==3)value=0xc840|revision;
 if(mmd==31 && reg==0xa438 && registers[31][0xa436]==0x801e)value=version;
 if(mmd==31 && reg==0xb800) {
  value=registers[31][0xb820]&0x10?0x40:0;
  if(timeout_reg==reg)value^=0x40;
 }
 if(mmd==31 && reg==0xa434)value=timeout_reg==reg?0x5600:4;
 return event('r',mmd,reg,value)<0?-EIO:(int)value;
}
static int phy_write_mmd(struct phy_device *p,int mmd,u16 reg,u16 value) {
 (void)p;
 int ret=event('w',mmd,reg,value);
 if(!ret)registers[mmd][reg]=value;
 return ret;
}
#define phy_read_mmd_poll_timeout(p,m,r,v,condition,d,t,s) ({ \
 int result=-ETIMEDOUT; \
 for(int attempt=0;attempt<3;attempt++) { \
  v=phy_read_mmd(p,m,r); \
  if(v<0){result=v;break;} \
  if(condition){result=0;break;} \
 } result; \
})
static bool of_property_read_bool(void *node,const char *name){(void)node;(void)name;return board;}
static int rtl822x_c45_get_features(struct phy_device *p){(void)p;return 0;}

struct mii_bus {void *parent;};
struct gpio_desc {};
static struct gpio_desc gpio;
static int gpio_present,gpio_error;
unsigned int gpio_puts,gpio_delay,gpio_order;
#define GPIOD_OUT_HIGH 1
#define IS_ERR(p) ((intptr_t)(p)<0)
#define PTR_ERR(p) ((intptr_t)(p))
static struct gpio_desc *gpiod_get_optional(void *dev,const char *name,int flag) {
 (void)dev;
 if(strcmp(name,"external-phy-enable") || flag!=GPIOD_OUT_HIGH)return (void *)(intptr_t)-EINVAL;
 gpio_order=1;
 return gpio_error?(void *)(intptr_t)gpio_error:gpio_present?&gpio:NULL;
}
static void gpiod_put(struct gpio_desc *desc) {
 if(desc==&gpio && gpio_order==1){gpio_puts++;gpio_order=2;}
}
static void msleep(unsigned int ms) {
 if(gpio_order==2){gpio_delay+=ms;gpio_order=3;}
}
'''
wrapper = r'''
int reset(int present,int error) {
 struct mii_bus bus={0};gpio_present=present;gpio_error=error;
 gpio_puts=gpio_delay=gpio_order=0;
 return rtl_mdio_reset(&bus);
}
int run(unsigned int rev,unsigned int ver,unsigned int initial,unsigned int fail,unsigned int timeout) {
 struct phy_device phy={.is_c45=true};
 revision=rev;version=ver;seed=initial;accesses=count=errors=warnings=0;fail_at=fail;timeout_reg=timeout;
 for(unsigned int m=0;m<32;m++)for(unsigned int r=0;r<65536;r++)registers[m][r]=seed;
 return rtl8221b_tyfza1_config_init(&phy);
}
int match(unsigned int id,int is_c45,int native_board) {
 struct phy_device phy={.is_c45=is_c45};
 phy.c45_ids.device_ids[1]=id;board=native_board;
 return rtl8221b_tyfza1_match(&phy,NULL);
}
'''
with tempfile.TemporaryDirectory(prefix='rtl8221b-oem-') as directory:
    directory = Path(directory)
    source = directory / 'native.c'
    library = directory / 'native.so'
    source.write_text(shim + '\n#include "' + str(header) + '"\n' + reset_source + wrapper)
    subprocess.run(['cc', '-shared', '-fPIC', '-O1', '-Wall', '-Wextra', '-Werror',
        '-Wno-unused-function', '-Wno-unused-parameter', str(source), '-o', str(library)], check=True)
    native = ctypes.CDLL(str(library))
    assert native.reset(1, 0) == 0
    assert ctypes.c_uint.in_dll(native, 'gpio_puts').value == 1
    assert ctypes.c_uint.in_dll(native, 'gpio_delay').value == stock_enable() == 300
    assert ctypes.c_uint.in_dll(native, 'gpio_order').value == 3
    assert native.reset(0, 0) == 0
    assert ctypes.c_uint.in_dll(native, 'gpio_puts').value == 0
    assert ctypes.c_uint.in_dll(native, 'gpio_delay').value == 0
    assert native.reset(1, -517) == -517
    rows = []
    for revision, version, seed in ((8, 0, 0), (8, 0, 0xa5a5), (8, 0x20, 0xa5a5),
                                    (9, 0, 0xa5a5), (10, 0, 0xa5a5)):
        expected = stock(revision, version, seed)
        assert native.run(revision, version, seed, 0, 0) == 0
        count = ctypes.c_uint.in_dll(native, 'count').value
        values = (ctypes.c_uint * (8192 * 4)).in_dll(native, 'events')
        actual = [[chr(values[i*4]), *values[i*4+1:i*4+4]] for i in range(count)]
        assert actual == expected, next(((i, a, b) for i, (a, b) in enumerate(zip(actual, expected))
                                         if a != b), (len(actual), len(expected)))
        rows.append({'revision': revision, 'patch_version': version,
                     'seed': hex(seed), 'events_match_OEM': count,
                     'writes': sum(event[0] == 'w' for event in actual)})
    for register in (0xa420, 0xb800):
        assert native.run(8, 0, 0, 0, register) == -110
        assert ctypes.c_uint.in_dll(native, 'errors').value == 1
    # The live 2.5G board has a434=0x5600 (ACK clear). Execute the OEM's
    # timeout branch too: it releases force-down and returns success.
    timed_out = stock(9, 0, 0, ack=0x5600)
    assert sum(e[0] == 'r' and e[2] == 0xa434 for e in timed_out) == 100
    assert timed_out[-1] == ['w', 31, 0xa400, 0]
    assert native.run(9, 0, 0, 0, 0xa434) == 0
    assert ctypes.c_uint.in_dll(native, 'warnings').value == 1
    assert ctypes.c_uint.in_dll(native, 'errors').value == 0
    # A real MDIO failure while releasing a timed-out request remains fatal.
    events = ctypes.c_uint.in_dll(native, 'count').value
    assert native.run(9, 0, 0, events, 0xa434) == -5
    # Bus failures before upload, during patch data, at version commit and at mode latch.
    expected = stock(8, 0, 0)
    for index in (1, 4, 120, len(expected) - 17, len(expected) - 2, len(expected)):
        assert native.run(8, 0, 0, index, 0) == -5, index
        assert ctypes.c_uint.in_dll(native, 'errors').value == (2 if index == len(expected) else 1)
    for ident in (0x001cc840, 0x001cc848, 0x001cc849, 0x001cc84a):
        assert native.match(ident, 1, 1) == 1
        assert native.match(ident, 1, 0) == 0
        assert native.match(ident, 0, 1) == 0
    assert native.match(0x001cc916, 1, 1) == 0

print(json.dumps({'OEM_module_sha256': hashlib.sha256(args.stock_module.read_bytes()).hexdigest(),
    'native_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
    'patch_data_sha256': hashlib.sha256(header.with_name('rtl8221b-tyfza1-patch.h').read_bytes()).hexdigest(),
    'cases': rows,
    'real_poll_conditions_and_MDIO_errors': 'passed', 'board_and_model_match': 'passed',
    'GPIO14_OEM_lifetime_and_settling': 'passed',
    'OEM_missing_ACK_releases_request_and_succeeds': True,
    'hardware_validation': False}, indent=2))
