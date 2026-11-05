#!/usr/bin/env python3
"""Compare four original BOSA diagnostic paths, native driver and shipped CLI."""
import argparse
import ctypes as C
import errno
import hashlib
import json
import math
from pathlib import Path
import random
import struct
import subprocess
import tempfile

from test_fttr_optical_monitor import Stock
from test_fttr_optical_models import ROOT, fixture
from test_fttr_fpga_boot import function
from unicorn.arm64_const import UC_ARM64_REG_D0, UC_ARM64_REG_SP, UC_ARM64_REG_PC


MODELS = [
    # Model, size, slave, diagnostic tail, end, registers in original order.
    (1, 588, 0xc0, 0x4057c4, 0x40586c, [0x30, 0x32, 0x34, 0x2e]),
    (2, 901, 0xc0, 0x40a7a0, 0x40a860, [0x6f, 0x71, 0x73, 0x75, 0x6d]),
    (10, 661, 0xd0, 0x40c914, 0x40c9bc, [0x70, 0x6a, 0x6c, 0x66]),
    (20, 284, 0xd0, 0x40e70c, 0x40e7b4, [0x70, 0x6a, 0x6c, 0x66]),
]


def checksum(cal):
    cal[2] = 0
    result = 0
    for value in cal:
        result ^= value
    cal[2] = result
    return bytes(cal)


class DiagnosticStock(Stock):
    def __init__(self, binary):
        self.outputs = []
        super().__init__(binary)

    def io(self, name, args):
        if name == 'printf':
            fmt = bytes(self.o.uc.mem_read(args[0], 64)).split(b'\0')[0]
            assert b'%lf' in fmt, fmt
            self.outputs.append(struct.unpack('<d', struct.pack('<Q', self.o.uc.reg_read(UC_ARM64_REG_D0)))[0])
            return 0
        if name.startswith('fpga_i2c_'):
            assert args[0] == 0 and args[1] == self.cal[4], args
        return super().io(name, args)

    def status(self, model, cal, values):
        self.cal, self.values, self.events, self.outputs = cal, values, [], []
        self.o.uc.mem_write(0x426148, cal[4:5])
        self.o.uc.reg_write(UC_ARM64_REG_SP, self.o.scratch + 0x30000)
        self.o.uc.emu_start(model[3], model[4], count=10000)
        assert self.o.uc.reg_read(UC_ARM64_REG_PC) == model[4]
        return list(self.outputs), list(self.events)


class Result(C.Structure):
    _fields_ = [('temperature', C.c_int), ('voltage', C.c_uint), ('bias', C.c_uint),
               ('tx', C.c_longlong), ('rx', C.c_longlong), ('rx_supported', C.c_bool)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stock = DiagnosticStock(args.stock)
    headers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    cases, valid, invalid, io_failures = 0, 0, 0, 0
    rnd = random.Random(3326)
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        source = '''#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <sys/types.h>
#include "rtl9617c-fttr-bosa.h"
int sample(int (*rd)(void *,unsigned),unsigned model,const unsigned char *cal,
 struct rtl_fttr_optical_diagnostics *result) {
 return rtl_fttr_optical_diagnostics_read(0,rd,model,cal,result);
}
struct device {void *data;}; struct device_attribute {int unused;};
struct i2c_client {struct device dev;unsigned addr;};
struct rtl_fttr_bosa {struct i2c_client *client;int lock;unsigned model;bool configured;};
struct firmware {const unsigned char *data;unsigned size;};
static struct firmware firmware;static int locks,references,fw_error,fw_requests;
static int (*reader)(void *,unsigned);
#define dev_get_drvdata(d) ((d)->data)
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
#define sysfs_emit(b,f,...) snprintf(b,256,f,##__VA_ARGS__)
static int i2c_smbus_read_word_swapped(struct i2c_client *c,unsigned reg) {
 (void)c; if(locks!=1)return -999;return reader(0,reg);
}
static int request_firmware_direct(const struct firmware **f,const char *name,struct device *d) {
 (void)name;(void)d;fw_requests++;if(locks!=1)return -999;
 if(fw_error)return fw_error;*f=&firmware;references++;return 0;
}
static void release_firmware(const struct firmware *f){(void)f;references--;}
'''
        driver = (headers / 'rtl9617c-fttr-bosa.c').read_text()
        for name in ('rtl_fttr_bosa_read_word', 'diagnostics_show'):
            source += function(driver, name) + '\n'
        source += '''
int show(int (*rd)(void *,unsigned),unsigned model,unsigned addr,const unsigned char *cal,
 unsigned size,int configured,int failure,char *out) {
 struct i2c_client client={.addr=addr};
 struct rtl_fttr_bosa b={.client=&client,.model=model,.configured=configured};
 client.dev.data=&b;firmware=(struct firmware){cal,size};fw_error=failure;
 locks=references=fw_requests=0;reader=rd;
 int ret=diagnostics_show(&client.dev,0,out);
 if(locks || references || fw_requests!=(configured && model!=2))return -999;
 return ret;
}
'''
        (folder / 'native.c').write_text(source)
        subprocess.run(['cc', '-std=c11', '-O2', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-parameter', '-Wno-misleading-indentation', '-I' + str(headers),
            str(folder / 'native.c'), '-o', str(folder / 'native.so')], check=True)
        native = C.CDLL(str(folder / 'native.so'))
        read_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint)
        native.sample.argtypes = [read_fn, C.c_uint, C.c_char_p, C.POINTER(Result)]
        native.show.argtypes = [read_fn, C.c_uint, C.c_uint, C.c_char_p, C.c_uint, C.c_int, C.c_int, C.c_char_p]
        events, failure, values = [], 0, {}

        def read(ctx, reg):
            value = values[reg]
            events.append(['word', reg, value])
            return -errno.EREMOTEIO if len(events) == failure else value

        reader = read_fn(read)
        status_file = folder / 'diagnostics'

        def cli(text, success=True):
            status_file.write_text(text)
            proc = subprocess.run([str(args.control.resolve()), str(folder), 'optical'], capture_output=True, text=True)
            assert (proc.returncode == 0) == success, (text, proc.stdout, proc.stderr)
            if not success:
                assert not proc.stdout
                return
            return json.loads(proc.stdout, parse_constant=lambda text: (_ for _ in ()).throw(AssertionError(text)))

        for model in MODELS:
            kind, size, slave, start, end, registers = model
            vectors = [(gain, offset, raw) for gain, offset in
                       [(1, 0), (256, 0), (512, -100), (65535, 32767), (65535, -32768)]
                       for raw in (0, 1, 255, 256, 32767, 32768, 65534, 65535)]
            vectors += [(rnd.randrange(65536), rnd.randrange(-32768, 32768), rnd.randrange(65536)) for _ in range(60)]
            for gain, offset, raw in vectors:
                cal = bytearray(fixture(kind, size, slave, 11))
                for at in (8, 16, 20, 24):
                    struct.pack_into('>Hh', cal, at, gain, offset)
                cal = checksum(cal)
                values = dict.fromkeys(registers, raw)
                original, expected = stock.status(model, cal, values)
                assert [event[1] for event in expected] == registers
                events.clear()
                result = Result()
                C.memset(C.byref(result), 0xa5, C.sizeof(result))
                before = bytes(result)
                ret = native.sample(reader, kind, cal, C.byref(result))
                cases += 1
                if math.isnan(original[2]):
                    assert ret == -errno.ERANGE and bytes(result) == before
                    assert events == expected[:3]
                    invalid += 1
                    continue
                assert ret == 0 and events == expected, (kind, gain, offset, raw, ret)
                converted = [result.voltage * 0.0001, result.bias * 0.002,
                    10 * math.log10(result.tx / 256) - 40 if result.tx else -math.inf]
                if kind == 2:
                    converted.append(10 * math.log10(result.rx / 256) - 40 if result.rx else -math.inf)
                converted.append(result.temperature / 256)
                assert original == converted, (kind, gain, offset, raw, original, converted)
                valid += 1
                out = C.create_string_buffer(256)
                events.clear()
                assert native.show(reader, kind, slave // 2, cal, size, 1, 0, out) > 0
                assert events == expected
                reply = cli(out.value.decode())
                assert reply['temperature_c'] == result.temperature / 256
                assert math.isclose(reply['voltage_v'], original[0], abs_tol=1e-8)
                assert math.isclose(reply['bias_ma'], original[1], abs_tol=1e-8)
                assert reply['tx_power_uw'] == result.tx / 2560
                assert reply['tx_power_dbm'] is None if result.tx == 0 else math.isclose(reply['tx_power_dbm'], original[2], abs_tol=1e-8)
                assert reply['rx_supported'] == (kind == 2)
                if kind != 2:
                    assert reply['rx_power_uw'] is None and reply['rx_power_dbm'] is None
                else:
                    assert reply['rx_power_uw'] == result.rx / 2560
                    assert reply['rx_power_dbm'] is None if result.rx == 0 else math.isclose(reply['rx_power_dbm'], original[3], abs_tol=1e-8)

            cal = bytearray(fixture(kind, size, slave, 11))
            for at in (8, 16, 20, 24):
                struct.pack_into('>Hh', cal, at, 256, 0)
            cal = checksum(cal)
            # Distinct words catch swapped addresses and ordering hidden by equal inputs.
            values = {reg: (index + 1) * 0x1234 for index, reg in enumerate(registers)}
            original, expected = stock.status(model, cal, values)
            events.clear(); out = C.create_string_buffer(256)
            assert native.show(reader, kind, slave // 2, cal, size, 1, 0, out) > 0
            reply = cli(out.value.decode())
            assert math.isclose(reply['voltage_v'], original[0], abs_tol=1e-8)
            assert math.isclose(reply['bias_ma'], original[1], abs_tol=1e-8)
            assert math.isclose(reply['tx_power_dbm'], original[2], abs_tol=1e-8)
            assert reply['temperature_c'] == original[-1] and events == expected
            for failure in range(1, len(registers) + 1):
                events.clear(); out.value = b'unchanged'
                assert native.show(reader, kind, slave // 2, cal, size, 1, 0, out) == -errno.EREMOTEIO
                assert out.value == b'unchanged' and events == expected[:failure]
                io_failures += 1
            failure = 0
            events.clear()
            assert native.show(reader, kind, slave // 2, cal, size, 0, 0, out) == -errno.EIO and not events
            if kind == 2:
                assert native.show(reader, kind, slave // 2, None, 0, 1, -errno.ENOENT, out) > 0
            else:
                for badcal, badsize, fw_error, want in [(cal, size, -errno.ENOENT, -errno.ENOENT),
                        (cal, size - 1, 0, -errno.EMSGSIZE),
                        (cal[:-1] + bytes([cal[-1] ^ 1]), size, 0, -errno.EBADMSG)]:
                    events.clear()
                    assert native.show(reader, kind, slave // 2, badcal, badsize, 1, fw_error, out) == want
                    assert not events
                events.clear()
                assert native.show(reader, kind, slave // 2 + 1, cal, size, 1, 0, out) == -errno.EINVAL and not events
                # A valid file for another model must not recalibrate readings.
                other = fixture(2, 901, slave, 1)
                assert native.show(reader, kind, slave // 2, other, len(other), 1, 0, out) == -errno.EINVAL and not events

        events.clear(); result = Result()
        assert native.sample(reader, 255, None, C.byref(result)) == -errno.EOPNOTSUPP and not events
        assert native.sample(reader, 1, None, C.byref(result)) == -errno.EINVAL and not events
        good = 'model=1 temperature_q8=-1 voltage_100uv=33000 bias_2ua=15000 tx_power_q8=2560 rx_power_q8=0 rx_supported=0\n'
        cli(good)
        for broken in [good + 'junk', good.replace('model=1', 'model=99'), good.replace('temperature_q8=-1', 'temperature_q8=-32769'),
                good.replace('voltage_100uv=33000', 'voltage_100uv=65536'), good.replace('bias_2ua=15000', 'bias_2ua=-1'),
                good.replace('tx_power_q8=2560', 'tx_power_q8=-1'), good.replace('tx_power_q8=2560', 'tx_power_q8=2155872000'),
                good.replace('rx_supported=0', 'rx_supported=1'), good.replace('rx_power_q8=0', 'rx_power_q8=1'),
                good.replace('model=1', 'model=2'), '', 'model=1\n']:
            cli(broken, False)
        status_file.unlink()
        proc = subprocess.run([str(args.control.resolve()), str(folder), 'optical'], capture_output=True)
        assert proc.returncode == 1 and not proc.stdout
        assert 'I2C_FUNC_SMBUS_BYTE_DATA | I2C_FUNC_SMBUS_WORD_DATA' in function(driver, 'rtl_fttr_bosa_probe')
    report = dict(stock_sha256=hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        original_cases=cases, exact_matches=valid, invalid_calibrated_power=invalid,
        injected_i2c_failures=io_failures, models=[m[0] for m in MODELS],
        driver_firmware_lock_errors='passed', production_cli='passed', hardware_tested=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
