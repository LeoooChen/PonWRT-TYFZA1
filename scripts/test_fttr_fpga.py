#!/usr/bin/env python3
"""Check native loading order against stock fw_load, and reject SPI failures."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle


def stock(module):
    events = []
    levels = {136: 1, 137: 0, 138: 1}

    def callback(name, args):
        if name == 'gpio_to_desc':
            return args[0]
        if name == 'gpiod_set_raw_value':
            pin, value = args[:2]
            events.append(['gpio', pin, value])
            levels[pin] = value
            levels[136] = value
        elif name == '__gpio_get_value':
            return levels[args[0]]
        elif name == 'polling_gpio_status.constprop.0':
            return 0 if levels[args[0]] == args[1] else (1 << 64) - 1
        elif name == 'fpga_multi_write':
            data = bytes(oracle.uc.mem_read(args[0], args[1]))
            events.append(['spi', len(data), hashlib.sha256(data).hexdigest()])
            levels[137] = 1
        elif name not in ('printk', '__init_swait_queue_head', 'print_hex_dump', 'release_firmware'):
            raise AssertionError(('unmodeled original call', name))
        return 0

    oracle = ModuleOracle(module, ['fw_load'], callback)
    address = oracle.scratch
    payload = bytes(range(32))
    oracle.uc.mem_write(address, struct.pack('<QQ', len(payload), address + 0x200))
    oracle.uc.mem_write(address + 0x100, struct.pack('<IIII', 138, 136, 137, 105))
    oracle.uc.mem_write(address + 0x200, payload)
    oracle.call('fw_load', address, address + 0x100)
    assert oracle.unpack(oracle.symbols['sloader_status'][0], 'I')[0] == 1
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/fpga/tyfza1-pg2l100h.c').read_text()

    def function(name):
        start = re.search(r'^static int ' + name + r'\(', source, re.M).start()
        end = source.index('{', start) + 1
        depth = 1
        while depth:
            depth += (source[end] == '{') - (source[end] == '}')
            end += 1
        return source[start:end]

    shim = r'''
#include <errno.h>
#include <stddef.h>
#include <string.h>
#define FPGA_MGR_PARTIAL_RECONFIG 1
struct gpio_desc { int pin; };
struct spi_device { int unused; };
struct fpga_manager { void *priv; };
struct fpga_image_info { unsigned int flags; };
struct tyfza1_fpga { struct spi_device *spi; struct gpio_desc *progb, *initb, *done; };
unsigned int events[4][3], count;
unsigned char copied[32];
static int levels[160], spi_error, wait_error;
static int gpiod_set_value_cansleep(struct gpio_desc *gpio, int value) {
    events[count][0]=0; events[count][1]=gpio->pin; events[count++][2]=!value;
    levels[136]=!value; return 0;
}
static int gpiod_get_raw_value_cansleep(struct gpio_desc *gpio) { return levels[gpio->pin]; }
static int tyfza1_fpga_wait(struct gpio_desc *gpio, int level, unsigned int timeout) {
    (void)timeout; return wait_error ? wait_error : levels[gpio->pin]==level ? 0 : -ETIMEDOUT;
}
static int spi_write(struct spi_device *spi, const void *buf, size_t size) {
    (void)spi; events[count][0]=1;events[count++][1]=size;
    memcpy(copied,buf,size); levels[137]=1; return spi_error;
}
'''
    wrapper = r'''
int run(int spi_failure, int wait_failure) {
    struct gpio_desc progb={138},initb={136},done={137};
    struct tyfza1_fpga fpga={0,&progb,&initb,&done};
    struct fpga_manager mgr={&fpga}; struct fpga_image_info info={0};
    char payload[32]; int ret;
    memset(events,0,sizeof(events)); count=0;
    levels[136]=1;levels[137]=0;spi_error=spi_failure;wait_error=wait_failure;
    for (int i=0;i<32;i++)payload[i]=i;
    ret=tyfza1_fpga_write_init(&mgr,&info,payload,sizeof(payload));
    if(!ret)ret=tyfza1_fpga_write(&mgr,payload,sizeof(payload));
    if(!ret)ret=tyfza1_fpga_write_complete(&mgr,&info);
    return ret;
}
'''
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'native.c').write_text(shim + '\n'.join(function(name) for name in (
            'tyfza1_fpga_write_init', 'tyfza1_fpga_write', 'tyfza1_fpga_write_complete')) + wrapper)
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
            '-shared', '-fPIC', str(path / 'native.c'), '-o', str(path / 'native.so')], check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        assert library.run(0, 0) == 0
        events = (ctypes.c_uint * 3 * 4).in_dll(library, 'events')
        copied = bytes((ctypes.c_ubyte * 32).in_dll(library, 'copied'))
        native = [['gpio', events[0][1], events[0][2]],
                  ['gpio', events[1][1], events[1][2]],
                  ['spi', events[2][1], hashlib.sha256(copied).hexdigest()]]
        expected = stock(args.stock)
        assert native == expected, (native, expected)
        assert library.run(-5, 0) == -5
        assert library.run(0, -110) == -110
        assert ctypes.c_uint.in_dll(library, 'count').value == 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'program_gpio_and_spi_payload_match': True, 'spi_errno_propagated': True,
        'handshake_timeout_stops_before_spi': True, 'hardware_verified': False,
        'scope': 'GPIO output order/payload compared to stock; GPIO waits modeled'}, indent=2) + '\n')
    print('Stock/native PROGB sequence and SPI payload match; SPI and handshake failures propagate')


if __name__ == '__main__':
    main()
