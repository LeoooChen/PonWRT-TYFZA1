#!/usr/bin/env python3
"""Execute original UX3326 temperature/reset/power code and native maintenance lifecycle."""
import argparse
import ctypes as C
import errno
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_fpga_boot import function
from test_fttr_optical_models import fixture
from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_PC, UC_ARM64_REG_D0


class Stock:
    def __init__(self, binary):
        self.o = ElfOracle(binary, self.io)
        self.o.symbols['power'] = 0x404d08
        self.values, self.events, self.cal, self.offset = {}, [], b'', 0
        self.failure = 0

    def io(self, name, a):
        u = self.o.uc
        if name == 'fopen64': self.offset = 0; return self.o.scratch + 0x6000
        if name == 'fseek': self.offset = a[1]
        elif name == 'fread':
            data = self.cal[self.offset:self.offset + a[1] * a[2]]
            u.mem_write(a[0], data); return len(data) // a[1]
        elif name in ('fpga_i2c_read_word', 'fpga_i2c_read_byte'):
            value = self.values.get(a[2], 0)
            self.events.append(['word' if name.endswith('word') else 'byte', a[2], value])
            if len(self.events) == self.failure: return 0xffffffff
            u.mem_write(a[3], struct.pack('<H', value) if name.endswith('word') else bytes([value])); return 0
        elif name == 'fpga_i2c_write_byte':
            self.events.append(['write', a[2], a[3] & 255])
        elif name == 'log10':
            value = struct.unpack('<d', struct.pack('<Q', u.reg_read(UC_ARM64_REG_D0)))[0]
            result = math.log10(value) if value > 0 else -math.inf if value == 0 else math.nan
            u.reg_write(UC_ARM64_REG_D0, struct.unpack('<Q', struct.pack('<d', result))[0])
        elif name == 'system': pass # Original log file writes only in this reset prefix.
        elif name not in ('printf', 'fclose', 'puts', 'memcmp'):
            raise AssertionError(name)
        if name == 'memcmp':
            left, right = bytes(u.mem_read(a[0], a[2])), bytes(u.mem_read(a[1], a[2]))
            return (left > right) - (left < right)
        return 0

    def run_block(self, start, end, cal):
        self.cal, self.events = cal, []
        self.o.uc.mem_write(0x426148, cal[4:5])
        stack = self.o.scratch + 0x10000
        self.o.uc.mem_write(stack, bytes(0x1000))
        self.o.uc.mem_write(stack + 0x30, cal)
        self.o.uc.reg_write(UC_ARM64_REG_SP, stack)
        self.o.uc.emu_start(start, end, count=10000)
        assert self.o.uc.reg_read(UC_ARM64_REG_PC) == end
        return list(self.events)


class LoopComplete(Exception):
    pass


def stock_loop(binary, cal, low_probes):
    """Run original initializer and complete monitor through one recovery."""
    stock = Stock(binary)
    check_at = 1600 if cal[6] else 2000
    time, probes, cycles = 0, 0, []
    def io(name, a):
        nonlocal time, probes
        if name == 'pid-create': return 0
        if name == 'fopen64':
            path = bytes(stock.o.uc.mem_read(a[0], 128)).split(b'\0')[0]
            if path == b'/var/run/rtkoltbosa.pid': return 0
        if name == 'printf':
            return 0
        if name == 'system':
            command = bytes(stock.o.uc.mem_read(a[0], 160)).split(b'\0')[0]
            if command.startswith(b'rtkoltbosa -c '): cycles.append([time, 'reload'])
            elif command.startswith(b'ubus call onuctrl OpticSwitch '):
                cycles.append([time, 'enable' if b'"enable":1' in command else 'disable'])
            else: assert command.startswith(b'echo '), command
            return 0
        if name in ('sleep', 'usleep'):
            time += a[0] * 1000 if name == 'sleep' else a[0] // 1000
            if probes and time > check_at + (7000 if low_probes >= 2 else 5000): raise LoopComplete()
            return 0
        if name == 'fpga_i2c_read_byte' and a[2] == 0:
            cycles.append([time, 'reset-check'])
        if name == 'fpga_i2c_read_word' and a[2] == 0x34:
            probes += 1
            stock.values[0x34] = 9 if probes <= low_probes else 10
            cycles.append([time, 'power-low' if probes <= low_probes else 'power-ok'])
        return stock.io(name, a)
    stock.o = ElfOracle(binary, io)
    # PID-file creation is process bookkeeping; the complete monitor still runs.
    stock.o.hooks[0x4020c0] = 'pid-create'
    stock.cal = cal
    stock.values = {0: 0, 1: 128, 2: 0, 3: 0, 0x2e: 20000, 6: 0}
    stock.o.uc.mem_write(0x426148, cal[4:5])
    path = stock.o.scratch + 0x2000
    stock.o.uc.mem_write(path, b'calibration.bin\0')
    stock.o.symbols['initialize'] = 0x40222c
    try: stock.o.call('initialize', path, 1)
    except LoopComplete: pass
    else: raise AssertionError('Original monitor did not run')
    expected = [[check_at, 'reset-check'], [check_at, 'power-low'], [check_at, 'reload'],
        [check_at + 5000, 'power-low' if low_probes >= 2 else 'power-ok']]
    if low_probes >= 2:
        expected += [[check_at + 5000, 'disable'], [check_at + 6000, 'enable'],
            [check_at + 7000, 'power-low' if low_probes >= 3 else 'power-ok']]
    assert cycles == expected, (cycles, expected)
    return cycles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stock = Stock(args.stock)
    headers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    read_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint)
    write_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_ubyte)
    with tempfile.TemporaryDirectory() as name:
        work = Path(name)
        source = '''#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <sys/types.h>
#include "rtl9617c-fttr-bosa.h"
int temperature(int (*read)(void *,unsigned),int (*write)(void *,unsigned,unsigned char),const unsigned char *cal) {
 return rtl_fttr_ux3326_temperature(0,read,write,cal);
}
int reset(int (*read)(void *,unsigned),int (*word)(void *,unsigned),const unsigned char *cal) {
 return rtl_fttr_ux3326_check_reset(0,read,word,cal);
}
int power(int (*word)(void *,unsigned),const unsigned char *cal) {
 return rtl_fttr_ux3326_low_power(0,word,cal);
}
'''
        # Exercise the real driver callbacks without duplicating their policy.
        source += '''
struct device {void *data;}; struct device_attribute {int unused;};
struct i2c_client {struct device dev;unsigned int addr;};
struct rtl_fttr_bosa {struct i2c_client *client;int lock;unsigned writes,model;unsigned char ux_calibration[588];int error;bool configured;};
struct firmware {const unsigned char *data;unsigned size;};
static struct firmware firmware;static int references,locks,fw_error;
static int (*reader)(void *,unsigned),(*word_reader)(void *,unsigned);
static int (*writer)(void *,unsigned,unsigned char);
#define dev_get_drvdata(d) ((d)->data)
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
#define sysfs_streq(a,b) (!strcmp(a,b))
#define sysfs_emit(b,f,...) snprintf(b,256,f,##__VA_ARGS__)
static int i2c_smbus_read_byte_data(struct i2c_client *c,unsigned reg){(void)c;return reader(0,reg);}
static int i2c_smbus_read_word_swapped(struct i2c_client *c,unsigned reg){(void)c;return word_reader(0,reg);}
static int i2c_smbus_write_byte_data(struct i2c_client *c,unsigned reg,unsigned char value){(void)c;return writer(0,reg,value);}
static int request_firmware_direct(const struct firmware **f,const char *name,struct device *dev){
 (void)name;(void)dev;if(fw_error)return fw_error;*f=&firmware;references++;return 0;
}
static void release_firmware(const struct firmware *f){(void)f;references--;}
'''
        driver = (headers / 'rtl9617c-fttr-bosa.c').read_text()
        for name in ('rtl_fttr_bosa_read', 'rtl_fttr_bosa_read_word', 'rtl_fttr_bosa_maintain_write',
                     'maintenance_show', 'maintenance_store'):
            source += function(driver, name) + '\n'
        source += '''
int maintain(int (*rd)(void *,unsigned),int (*word)(void *,unsigned),int (*wr)(void *,unsigned,unsigned char),
 const unsigned char *cal,unsigned size,unsigned model,int configured,int failure,const char *command,char *status) {
 struct i2c_client c={.addr=0x60};struct rtl_fttr_bosa b={.client=&c,.model=model,.configured=configured,.writes=56};
 memcpy(b.ux_calibration,cal,588);c.dev.data=&b;firmware=(struct firmware){cal,size};fw_error=failure;
 reader=rd;word_reader=word;writer=wr;locks=references=0;
 int ret=maintenance_store(&c.dev,0,command,strlen(command));
 if(references || locks || b.writes!=56)return -999;
 if(ret<0)return ret;
 ret=maintenance_show(&c.dev,0,status);return ret<0?ret:0;
}
'''
        (work / 'native.c').write_text(source)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
            '-Wno-sign-compare', '-I' + str(headers), str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
        native = C.CDLL(str(work / 'native.so'))
        native.temperature.argtypes = [read_fn, write_fn, C.c_char_p]
        native.reset.argtypes = [read_fn, read_fn, C.c_char_p]
        native.power.argtypes = [read_fn, C.c_char_p]
        native.maintain.argtypes = [read_fn, read_fn, write_fn, C.c_char_p, C.c_uint, C.c_uint,
            C.c_int, C.c_int, C.c_char_p, C.c_char_p]
        events, failure = [], 0
        def read(kind, reg):
            value = stock.values.get(reg, 0); events.append([kind, reg, value])
            return -errno.EIO if len(events) == failure else value
        def write(ctx, reg, value):
            events.append(['write', reg, value]); return -errno.EIO if len(events) == failure else 0
        byte, word, writer = read_fn(lambda _, r: read('byte', r)), read_fn(lambda _, r: read('word', r)), write_fn(write)
        cal = bytearray(fixture(1, 588, 0xc0, 11)); cal[6] = 1
        for index in range(84): struct.pack_into('>H', cal, 0xfc + index * 2, index * 11 + 3)
        temp_cases = 0
        for raw in sorted({0, 65535, *[max(0, min(65535, (65 + index * 2) * 128 - 64 + d))
                for index in range(85) for d in (-1, 0, 1)]}):
            index = max(0, min(83, (((raw + 64) >> 7) - 65) // 2))
            wanted = struct.unpack_from('>H', cal, 0xfc + index * 2)[0]
            for observed in (0, (wanted >> 2) << 8 | wanted & 3):
                stock.values = {0x2e: raw, 6: observed}
                expected = stock.run_block(0x402940, 0x402aec, bytes(cal))
                events.clear(); assert native.temperature(word, writer, bytes(cal)) == 0
                assert events == expected, (raw, observed, events, expected)
                temp_cases += 1
        stock.values = {0x2e: 20000, 6: 0}
        events.clear(); native.temperature(word, writer, bytes(cal)); expected = list(events)
        for failure in range(1, len(expected) + 1):
            events.clear(); assert native.temperature(word, writer, bytes(cal)) == -errno.EIO
            assert events == expected[:failure]
        failure = 0
        cal[6] = 0; events.clear(); assert native.temperature(word, writer, bytes(cal)) == 0 and not events
        cal[6] = 1
        power_cases = 0
        for gain, offset in ((1, 0), (256, 0), (1, -1), (512, -10), (1234, 15), (65535, 0), (65535, 32767)):
            struct.pack_into('>Hh', cal, 8, gain, offset)
            for raw in (0, 1, 9, 10, 11, 2559, 2560, 2561, 65535):
                stock.values = {0x34: raw}; stock.cal = bytes(cal); stock.events.clear()
                stock.o.call('power')
                value = struct.unpack('<d', struct.pack('<Q', stock.o.uc.reg_read(UC_ARM64_REG_D0)))[0]
                events.clear(); ret = native.power(word, bytes(cal))
                expected = -errno.ERANGE if math.isnan(value) else -errno.EUCLEAN if value < -30 else 0
                assert ret == expected and events == stock.events, (gain, offset, raw, value, ret)
                power_cases += 1
        struct.pack_into('>Hh', cal, 8, 256, 0)
        loops = [stock_loop(args.stock, bytes(cal), n) for n in (1, 2, 3)]
        cal[6] = 0; loops.append(stock_loop(args.stock, bytes(cal), 2)); cal[6] = 1
        for initial in ({0: 1}, {0: 0, 1: 1}, {0: 0, 1: 128, 2: 1}, {0: 0, 1: 128, 2: 0, 3: 1}, {0: 0, 1: 128, 2: 0, 3: 0}):
            for raw in (9, 10):
                # Separate emulator keeps this block's stop boundary out of
                # translation blocks cached by the overlapping temperature run.
                stock = Stock(args.stock)
                stock.values = dict(initial); stock.values[0x34] = raw
                # Stop before original recalibration branch, retaining exact
                # reset predicate and first power probe. Non-trigger paths exit940.
                stock.cal = bytes(cal)
                expected = stock.run_block(0x4026ac, 0x402940 if raw == 10 or initial != {0:0,1:128,2:0,3:0} else 0x402858, bytes(cal))
                events.clear(); ret = native.reset(byte, word, bytes(cal))
                assert events == expected and ret == (-errno.EUCLEAN if raw == 9 and initial == {0:0,1:128,2:0,3:0} else 0)
        # Production command handler: firmware validation, no calibration-count
        # mutation, and no IO for unsupported/unconfigured devices.
        cal[2] = 0; checksum = 0
        for v in cal: checksum ^= v
        cal[2] = checksum
        stock.values = {0: 1, 0x2e: 20000, 6: 0, 0x34: 10}; status = C.create_string_buffer(256)
        for command in (b'temperature', b'reset', b'power'):
            events.clear(); assert native.maintain(byte, word, writer, bytes(cal), 588, 1, 1, 0, command, status) == 0
            assert status.value == b'supported=1 interval_ms=200\n'
        for command, size, model, configured, failure_code, expected in (
                (b'reset', 587, 1, 1, 0, -errno.EMSGSIZE), (b'reset', 588, 1, 1, -errno.ENOENT, -errno.ENOENT),
                (b'power', 588, 1, 1, -errno.ENOENT, -errno.ENOENT),
                (b'temperature', 588, 10, 1, 0, -errno.EOPNOTSUPP),
                (b'temperature', 588, 1, 0, 0, -errno.EIO), (b'invalid', 588, 1, 1, 0, -errno.EINVAL)):
            events.clear(); assert native.maintain(byte, word, writer, bytes(cal), size, model, configured, failure_code, command, status) == expected
            assert not events
        lifecycle(work)
        smbus(work, headers)
    args.output.write_text(json.dumps({'temperature_cases': temp_cases, 'power_cases': power_cases, 'original_recovery_loops': loops,
        'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'scope': 'Actual original ARM64 temperature/reset/linear-power predicates; production driver commands and nonblocking controller state machine. I2C/time are modeled; no physical temperature, laser or recovery acceptance.'}, indent=2) + '\n')
    print(f'PASS: {temp_cases} temperature, {power_cases} power, reset and lifecycle/error cases')


def lifecycle(work):
    source = '''#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "miniolt.h"
static int supported=1,interval=200,commands,failure,reset_fault,power_faults,calibrations,tx;
static char events[32][32];
static FILE *attribute(int directory,const char *name) {
 (void)directory;assert(!strcmp(name,"maintenance"));FILE *f=tmpfile();assert(f);
 fprintf(f,"supported=%u interval_ms=%u",supported,interval);rewind(f);return f;
}
int fttr_miniolt_command(int directory,const char *name,const char *value) {
 assert(directory==1 || directory==2);assert(commands<32);snprintf(events[commands],32,"%s:%s",name,value);commands++;
 if(commands==failure)return -EIO;
 if(!strcmp(name,"calibration")){calibrations++;return 0;}
 if(!strcmp(name,"tx_enable")){tx=value[0]=='1';return 0;}
 assert(!strcmp(name,"maintenance"));
 if(!strcmp(value,"reset\\n"))return reset_fault;
 if(!strcmp(value,"power\\n") && power_faults){power_faults--;return -EUCLEAN;}
 return 0;
}
int fttr_miniolt_tx_disable(int directory){return fttr_miniolt_command(directory,"tx_enable","0\\n");}
'''
    text = (ROOT / 'port/packages/tyfza1-fttr/src/miniolt.c').read_text()
    # Reuse the balanced function reader, retaining the actual two functions.
    from test_fttr_cpu_deepq import function as extract
    source += extract(text, 'fttr_miniolt_optical_start') + extract(text, 'fttr_miniolt_optical_tick')
    source += r'''
int main(void) {
 for(unsigned recover=0;recover<3;recover++) {
  struct fttr_optical_monitor m={.directory=1};commands=failure=calibrations=0;reset_fault=-EUCLEAN;power_faults=recover;tx=1;
  assert(!fttr_miniolt_optical_start(&m,0));
  for(int time=0;time<1600;time+=200){assert(!fttr_miniolt_optical_tick(&m,2,time));assert(m.phase==0);}
  assert(commands==8 && m.elapsed==1600);
  assert(!fttr_miniolt_optical_tick(&m,2,1600));assert(m.phase==FTTR_OPTICAL_RELOADED && m.due==6600 && calibrations==1);
  assert(!fttr_miniolt_optical_tick(&m,2,6599));assert(commands==10);
  int ret=fttr_miniolt_optical_tick(&m,2,6600);assert(!ret);
  if(!recover){assert(m.phase==0 && tx==1);continue;}
  assert(m.phase==FTTR_OPTICAL_DISABLED && !tx && m.due==7600);
  assert(!fttr_miniolt_optical_tick(&m,2,7600));assert(tx && m.phase==FTTR_OPTICAL_ENABLED && m.due==8600);
  ret=fttr_miniolt_optical_tick(&m,2,8600);
  assert(ret==(recover==2?-ENOLINK:0));
 }
 for(unsigned fail=1;fail<=6;fail++) {
  struct fttr_optical_monitor m={.directory=1};commands=calibrations=0;failure=fail;reset_fault=-EUCLEAN;power_faults=2;tx=1;
  assert(!fttr_miniolt_optical_start(&m,0));m.elapsed=1600;
  int ret=0;for(unsigned i=0;i<5 && !ret;i++)ret=fttr_miniolt_optical_tick(&m,2,m.due);
  assert(ret==-EIO && commands==failure);
 }
 supported=0;interval=0;commands=0;failure=0;
 struct fttr_optical_monitor m={.directory=1};assert(!fttr_miniolt_optical_start(&m,0));
 assert(!fttr_miniolt_optical_tick(&m,2,100000) && !commands);
 supported=1;interval=1000;reset_fault=-EUCLEAN;
 assert(!fttr_miniolt_optical_start(&m,0));
 assert(!fttr_miniolt_optical_tick(&m,2,0) && m.due==1000);
 assert(!fttr_miniolt_optical_tick(&m,2,1000) && m.due==2000);
 assert(!fttr_miniolt_optical_tick(&m,2,2000) && m.phase==FTTR_OPTICAL_RELOADED);
 puts("Native monitor: original 200ms/1.6s, 5s reload and 1s TX-cycle timing; errors stop, other models idle");
}
'''
    (work / 'lifecycle.c').write_text(source)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I' + str(ROOT / 'port/packages/tyfza1-fttr/src'),
        str(work / 'lifecycle.c'), '-o', str(work / 'lifecycle')], check=True)
    subprocess.run([str(work / 'lifecycle')], check=True)


def smbus(work, headers):
    source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;typedef int32_t s32;
#define I2C_SMBUS_READ 1
#define I2C_SMBUS_BYTE_DATA 2
#define I2C_SMBUS_WORD_DATA 3
#define I2C_CLIENT_TEN 0x10
#define I2C_FUNC_SMBUS_BYTE_DATA 0x180000
#define I2C_FUNC_SMBUS_WORD_DATA 0x600000
#define swab16(v) ((u16)(((u16)(v)>>8)|((u16)(v)<<8)))
struct rtl_fttr {int reg_io,control_lock;};struct i2c_adapter {struct rtl_fttr *data;};
union i2c_smbus_data {u8 byte;u16 word;};
#define i2c_get_adapdata(a) ((a)->data)
static int locks,calls,fail,word_mode,reading;
static unsigned int input,output;
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
static int rtl_fttr_i2c_xfer(int *io,unsigned int addr,unsigned int reg,unsigned int *value,int word,int read) {
 (void)io;assert(locks==1 && addr==0x60 && reg==0x2e);calls++;
 assert(word==word_mode && read==reading);input=*value;*value=output;return fail;
}
'''
    source += function((headers / 'rtl9617c-fttr.c').read_text(), 'rtl_fttr_smbus_xfer')
    source += function((headers / 'rtl9617c-fttr.c').read_text(), 'rtl_fttr_i2c_functionality')
    source += r'''
int main(void) {
 struct rtl_fttr f={0};struct i2c_adapter a={&f};union i2c_smbus_data d;
 assert(rtl_fttr_i2c_functionality(&a)==(I2C_FUNC_SMBUS_BYTE_DATA|I2C_FUNC_SMBUS_WORD_DATA));
 for(word_mode=0;word_mode<2;word_mode++)for(reading=0;reading<2;reading++)for(unsigned failure=0;failure<2;failure++) {
  d.word=0x1234;calls=locks=0;fail=failure?-EIO:0;output=word_mode?0xabcd:0xcd;
  int ret=rtl_fttr_smbus_xfer(&a,0x60,0,reading,0x2e,word_mode?I2C_SMBUS_WORD_DATA:I2C_SMBUS_BYTE_DATA,&d);
  assert(ret==fail && !locks && calls==1);
  assert(input==(reading?0:word_mode?0x3412:0x34));
  if(!failure && reading)assert(word_mode?d.word==0xcdab:d.byte==0xcd);
  else assert(d.word==0x1234);
 }
 calls=0;assert(rtl_fttr_smbus_xfer(&a,0x60,I2C_CLIENT_TEN,1,0x2e,I2C_SMBUS_WORD_DATA,&d)==-EOPNOTSUPP);
 assert(rtl_fttr_smbus_xfer(&a,0x60,0,1,0x2e,4,&d)==-EOPNOTSUPP && !calls);
 puts("Native SMBus: byte/word order, errors and supported transfer modes passed");
}
'''
    (work / 'smbus.c').write_text(source)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
        str(work / 'smbus.c'), '-o', str(work / 'smbus')], check=True)
    subprocess.run([str(work / 'smbus')], check=True)


if __name__ == '__main__': main()
