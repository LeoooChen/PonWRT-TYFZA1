#!/usr/bin/env python3
"""Compare all reachable original optical calibration recipes and device identity checks."""
import argparse
import ctypes as C
import errno
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_fpga_boot import function


class Stock:
    def __init__(self, binary):
        self.o = ElfOracle(binary, self.hook)
        self.values, self.events = {}, []
        self.cal = b''

    def hook(self, name, a):
        u = self.o.uc
        if name == 'fopen64':
            return self.o.scratch + 0x6000
        if name == 'fread':
            data = self.cal[:a[1] * a[2]]
            u.mem_write(a[0], data)
            return len(data) // a[1]
        if name == 'fpga_i2c_write_byte':
            self.events.append(['write', a[2], a[3] & 255])
        elif name == 'fpga_i2c_read_byte':
            value = self.values.get(a[2], 0)
            self.events.append(['read', a[2], value])
            u.mem_write(a[3], bytes([value]))
        elif name not in ('puts', 'printf', 'fseek', 'fclose'):
            raise AssertionError(('unmodeled original call', name))
        return 0

    def call(self, address, calibration, *args):
        self.cal = calibration
        self.o.uc.mem_write(0x426148, calibration[4:5])
        self.o.symbols['target'] = address
        self.events.clear()
        return self.o.call('target', *args)


def fixture(model, size, slave, seed):
    data = bytearray((i * 73 + seed) & 255 for i in range(size))
    data[:5] = struct.pack('>HBBB', size, 0, model, slave)
    checksum = 0
    for value in data:
        checksum ^= value
    data[2] = checksum
    return bytes(data)


def check_init_script(folder):
    """Run the shipped rc script with only filesystem roots/procd modeled."""
    directory = folder / 'rc'; directory.mkdir()
    device = directory / 'mdio/miniolt'; device.mkdir(parents=True)
    factory = directory / 'factory'; factory.mkdir()
    (factory / 'fttr_startup').write_text('1 9 0 2\n')
    (factory / 'fttr-private').mkdir()
    (factory / 'fttr-private/loid.bin').write_bytes(b'TFL1' + bytes(38))
    optical = device / 'i2c-0/0-0060'; optical.mkdir(parents=True)
    log = directory / 'command'
    script = (ROOT / 'port/packages/tyfza1-fttr/files/tyfza1-fttr.init').read_text()
    script = script.replace('/sys/bus/mdio_bus/devices', str(directory / 'mdio'))
    script = script.replace('/sys/bus/spi/devices', str(directory / 'spi'))
    script = script.replace('/tmp/tyfza1-factory', str(factory))
    script = script.replace('/var/run/tyfza1-fttr', str(directory / 'run'))
    prefix = '''
procd_open_instance() { :; }
procd_close_instance() { :; }
procd_set_param() { [ "$1" != command ] || printf '%%s\\n' "$@" > '%s'; }
attempts=0
sleep() { attempts=$((attempts+1)); [ "$attempts" != 3 ] || [ "$late" != 1 ] || touch '%s'; }
''' % (log, optical / 'calibration')
    for backend in ('asic9690', 'fpga1', 'fpga2'):
        for present in (True, False):
            (device / 'backend').write_text(backend + '\n')
            (device / 'optical_init').write_text('')
            (optical / 'calibration').unlink(missing_ok=True)
            if present: (optical / 'calibration').touch()
            log.unlink(missing_ok=True)
            result = subprocess.run(['sh', '-c', prefix + script + '\nstart_service'], capture_output=True)
            assert (result.returncode == 0) == present, (backend, result.stderr)
            assert (device / 'optical_init').read_text() == ('' if backend == 'asic9690' else '1\n')
            if present:
                assert log.read_text().splitlines() == ['command', '/usr/sbin/fttrd', str(device),
                    '/sys/class/net/fttr0/device', str(directory / 'run'), 'fttr0', str(optical),
                    '9', '0', '2', '--services', '/etc/tyfza1-fttr.services', '--wifi', '/etc/tyfza1-fttr.wifi',
                    '--loid-auth', str(factory / 'fttr-private/loid.bin')]
            else: assert not log.exists()
    # Delayed I2C driver binding and an unsupported backend must be distinct.
    subprocess.run(['sh', '-c', 'late=1\n' + prefix + script + '\nstart_service'], check=True)
    assert log.exists()
    (device / 'backend').write_text('unknown\n'); log.unlink()
    result = subprocess.run(['sh', '-c', prefix + script + '\nstart_service'], capture_output=True)
    assert result.returncode != 0 and not log.exists()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    stock = Stock(args.stock)
    headers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    write_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_ubyte)
    read_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint)
    models = [(1, 588, 0xc0, 0x40222c, None, 56),
              (2, 901, 0xc0, 0x405ad4, 0x405878, 514),
              (10, 661, 0xd0, 0x40aa24, 0x40a8d8, 128),
              (20, 284, 0xd0, 0x40cb68, 0x40ca30, 256)]
    cases = []
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        check_init_script(folder)
        source = '''#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-fttr-bosa.h"
int configure(int (*wr)(void *, unsigned, unsigned char), const unsigned char *cal, unsigned size) {
 int ret=rtl_fttr_optical_validate(cal,size);
 return ret ? ret : rtl_fttr_optical_calibrate(0,wr,cal);
}
int identity(int (*rd)(void *, unsigned), int (*wr)(void *, unsigned, unsigned char),
 unsigned model, unsigned *marker) {
 int ret=0;
 if(model==2) { ret=rtl_fttr_ux3326s_unlock(0,wr); if(!ret) ret=wr(0,0x7f,0x80); }
 return ret ? ret : rtl_fttr_optical_identity(0,rd,model,marker);
}
'''
        # Execute the production driver's configure/show paths, including
        # firmware ownership, silicon mismatch and I2C error propagation.
        source += '''
struct device { void *of_node, *data; };
struct device_attribute { int unused; };
struct i2c_client { struct device dev; unsigned int addr; };
struct firmware { const unsigned char *data; unsigned int size; };
struct rtl_fttr_bosa {
 struct i2c_client *client; int lock; unsigned int writes,model; int error; bool configured;
 unsigned char ux_calibration[588];
};
static struct firmware firmware;
static int references,locks,fw_error;
static int (*reader)(void *,unsigned);
static int (*writer)(void *,unsigned,unsigned char);
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
#define sysfs_emit snprintf_wrapper
#define dev_get_drvdata(d) ((d)->data)
static int snprintf_wrapper(char *buf,const char *fmt,...);
#include <stdarg.h>
static int snprintf_wrapper(char *buf,const char *fmt,...) {
 va_list args;va_start(args,fmt);int n=vsnprintf(buf,256,fmt,args);va_end(args);return n;
}
static int i2c_smbus_read_byte_data(struct i2c_client *c,unsigned reg) { (void)c;return reader(0,reg); }
static int i2c_smbus_write_byte_data(struct i2c_client *c,unsigned reg,unsigned char v) { (void)c;return writer(0,reg,v); }
static int of_device_is_compatible(void *node,const char *name) { (void)name;return node!=0; }
static int request_firmware_direct(const struct firmware **fw,const char *name,struct device *dev) {
 (void)name;(void)dev;if(fw_error)return fw_error;*fw=&firmware;references++;return 0;
}
static void release_firmware(const struct firmware *fw) { (void)fw;references--; }
'''
        driver = (headers / 'rtl9617c-fttr-bosa.c').read_text()
        for name in ('rtl_fttr_bosa_read', 'rtl_fttr_bosa_write', 'rtl_fttr_bosa_configure', 'calibration_show'):
            source += function(driver, name) + '\n'
        source += '''
int configure_device(int (*rd)(void *,unsigned),int (*wr)(void *,unsigned,unsigned char),
 const unsigned char *cal,unsigned size,unsigned addr,int fixed_gn,int failure,char *status) {
 struct i2c_client c={.addr=addr,.dev={.of_node=fixed_gn?(void *)1:0}};
 struct rtl_fttr_bosa b={.client=&c};c.dev.data=&b;
 reader=rd;writer=wr;fw_error=failure;firmware=(struct firmware){cal,size};references=locks=0;
 int ret=rtl_fttr_bosa_configure(&b);
 if(references || locks)return -999;
 if(ret) { if(b.configured || b.error!=ret)return -998;return ret; }
 ret=calibration_show(&c.dev,0,status);
 return ret<0?ret:0;
}
'''
        source += '''
struct i2c_adapter { int unused; };
struct i2c_board_info { char type[32]; unsigned addr; };
struct rtl_fttr { struct i2c_adapter optical; struct i2c_client *optical_client; int control_lock; };
#define I2C_BOARD_INFO(n,a) .type=n,.addr=a
#define PTR_ERR_OR_ZERO(p) ((uintptr_t)(p)>=(uintptr_t)-4095 ? (int)(intptr_t)(p) : 0)
#define sysfs_streq(a,b) (!strcmp(a,b))
static int client_error,creates,removes;
static struct i2c_client dynamic_client;
static struct i2c_client *i2c_new_client_device(struct i2c_adapter *a,const struct i2c_board_info *info) {
 (void)a;creates++;
 if(strcmp(info->type,"tyfza1-fttr-bosa"))return (void *)(intptr_t)-EINVAL;
 if(client_error)return (void *)(intptr_t)client_error;
 dynamic_client.addr=info->addr;return &dynamic_client;
}
static void i2c_unregister_device(struct i2c_client *c) { (void)c;removes++; }
'''
        miniolt = (headers / 'rtl9617c-fttr.c').read_text()
        source += function(miniolt, 'optical_init_store') + '\n'
        source += function(miniolt, 'rtl_fttr_optical_unregister') + '\n'
        source += '''
int bind_device(const unsigned char *cal,unsigned size,unsigned existing,int failure,int firmware_failure) {
 struct i2c_client previous={.addr=existing};
 struct rtl_fttr fttr={.optical_client=existing?&previous:0};struct device dev={.data=&fttr};
 firmware=(struct firmware){cal,size};fw_error=firmware_failure;client_error=failure;
 references=locks=creates=removes=0;
 int ret=optical_init_store(&dev,0,"1",1);
 if(references || locks)return -999;
 if(ret==1) {
  if(!fttr.optical_client || fttr.optical_client->addr!=cal[4]/2)return -998;
  if(optical_init_store(&dev,0,"1",1)!=1 || creates!=!existing)return -997;
  rtl_fttr_optical_unregister(&fttr);if(removes!=1)return -996;
 }
 if(optical_init_store(&dev,0,"invalid",7)!=-EINVAL || references || locks)return -995;
 return ret;
}
'''
        probe = function(miniolt, 'rtl_fttr_probe')
        assert probe.index('devm_i2c_add_adapter') < probe.index('devm_add_action_or_reset(dev, rtl_fttr_optical_unregister')
        (folder / 'native.c').write_text(source)
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-sign-compare',
                        '-I' + str(headers), str(folder / 'native.c'),
                        str(ROOT / 'port/packages/tyfza1-fttr/src/miniolt.c'),
                        '-o', str(folder / 'native.so')], check=True)
        native = C.CDLL(str(folder / 'native.so'))
        native.configure.argtypes = [write_fn, C.c_char_p, C.c_uint]
        native.identity.argtypes = [read_fn, write_fn, C.c_uint, C.POINTER(C.c_uint)]
        native.configure_device.argtypes = [read_fn, write_fn, C.c_char_p, C.c_uint,
                                           C.c_uint, C.c_int, C.c_int, C.c_char_p]
        native.bind_device.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_int, C.c_int]
        events, fail_at = [], 0

        def wr(ctx, reg, value):
            events.append(['write', reg, value])
            return -errno.EIO if len(events) == fail_at else 0

        def rd(ctx, reg):
            value = stock.values.get(reg, 0)
            events.append(['read', reg, value])
            return -errno.EIO if len(events) == fail_at else value

        write, read = write_fn(wr), read_fn(rd)
        for model, size, slave, init, identify, count in models:
            for seed in (0, 11, 255):
                cal = fixture(model, size, slave, seed)
                stock.o.uc.mem_write(stock.o.scratch + 0x2000, b'calibration.bin\0')
                assert stock.call(init, cal, stock.o.scratch + 0x2000, 0) == 0
                events.clear()
                assert native.configure(write, cal, size) == 0
                assert events == stock.events and len(events) == count
                expected = list(events)
                for fail_at in range(1, len(expected) + 1):
                    events.clear()
                    assert native.configure(write, cal, size) == -errno.EIO
                    assert events == expected[:fail_at]
                fail_at = 0
            # Every corruption is rejected before the first optical write.
            for offset in (2, 3, 4, size - 1):
                broken = bytearray(cal); broken[offset] ^= 1
                events.clear()
                assert native.configure(write, bytes(broken), size) < 0 and not events
            events.clear()
            assert native.configure(write, cal[:-1], size - 1) == -errno.EMSGSIZE and not events
            stock.values = dict(enumerate(b'UX3326S1', 0xc4)) if model == 2 else {0x60: 0xb0 if model == 10 else 0xa1}
            marker = C.c_uint()
            if identify:
                assert stock.call(identify, cal) == 1
                events.clear()
                assert native.identity(read, write, model, C.byref(marker)) == 0
                assert events == stock.events
                expected = list(events)
                for fail_at in range(1, len(expected) + 1):
                    events.clear()
                    assert native.identity(read, write, model, C.byref(marker)) == -errno.EIO
                    assert events == expected[:fail_at]
                fail_at = 0
                stock.values = {}
                events.clear()
                assert native.identity(read, write, model, C.byref(marker)) == -errno.ENODEV
                assert stock.call(identify, cal) == 0
            cases.append({'model': model, 'file_size': size, 'writes': count,
                          'original_init': hex(init), 'original_identity': hex(identify) if identify else None})
            stock.values = dict(enumerate(b'UX3326S1', 0xc4)) if model == 2 else {0x60: 0xb0 if model == 10 else 0xa1}
            status = C.create_string_buffer(256)
            events.clear()
            assert native.configure_device(read, write, cal, size, slave >> 1, 0, 0, status) == 0
            assert f'writes={count} error=0'.encode() in status.value
            (folder / 'calibration').write_bytes(status.value)
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            assert native.fttr_miniolt_calibrated(fd) == 0
            os.close(fd)
            expected = list(events)
            for fail_at in range(1, len(expected) + 1):
                events.clear()
                assert native.configure_device(read, write, cal, size, slave >> 1, 0, 0, status) == -errno.EIO
                assert events == expected[:fail_at]
            fail_at = 0
            events.clear()
            assert native.configure_device(read, write, cal, size, slave >> 1, 0, -errno.ENOENT, status) == -errno.ENOENT
            assert not events
            assert native.configure_device(read, write, cal, size, (slave >> 1) + 1, 0, 0, status) == -errno.EINVAL
            assert not events
            if model != 10:
                assert native.configure_device(read, write, cal, size, slave >> 1, 1, 0, status) == -errno.EINVAL
                assert not events
            assert native.bind_device(cal, size, 0, 0, 0) == 1
            assert native.bind_device(cal, size, slave >> 1, 0, 0) == 1
            assert native.bind_device(cal, size, (slave >> 1) + 1, 0, 0) == -errno.EBUSY
            assert native.bind_device(cal, size, 0, -errno.ENOMEM, 0) == -errno.ENOMEM
            assert native.bind_device(cal, size, 0, 0, -errno.ENOENT) == -errno.ENOENT
            assert native.bind_device(cal[:-1], size - 1, 0, 0, 0) == -errno.EMSGSIZE
            # Original factory address selection accepts responding addresses
            # outside the fallback Cx/Dx scanning ranges too.
            alternate = fixture(model, size, 0xa2, 11)
            assert native.bind_device(alternate, size, 0, 0, 0) == 1
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'cases': cases, 'scope': 'original ARM64 initializers and chip-ID predicates; patterned calibration bytes and I2C are modeled; no optical traffic or particular FPGA board model claim'}, indent=2) + '\n')
    print('PASS: four original optical recipes, all write/identity error cutoffs, corrupt-file rejection')


if __name__ == '__main__':
    main()
