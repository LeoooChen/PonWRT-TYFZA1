#!/usr/bin/env python3
"""Execute the original FPGA relay's management IO and baseline OMCI sender."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_fpga_boot import function

HOST = bytes.fromhex('020304050607')


class Stock:
    def __init__(self, asic, relay, backend, values):
        self.backend, self.values, self.events = backend, dict(values), []
        self.asic = ElfOracle(asic, self.io, ('FPGA_reg_read', 'FPGA_reg_write',
                                            'drv_get_miniolt_chiptype'))
        self.relay = ElfOracle(relay, self.io, ('fpga_reg_read', 'fpga_reg_write',
            'fpga_reg_read_check', 'fpga_omci_send_msg', 'plr_pondrv_log_module_id_get', 'plr_log'))
        # Local serial reader in the retained libponrelay1p25g.so.
        self.relay.symbols['serial_read'] = 0x464b0

    def io(self, name, args):
        if name in ('FPGA_reg_read', 'fpga_reg_read', 'fpga_reg_read_check'):
            value = self.values.get(args[0], 0)
            self.events.append(['read', args[0], value])
            if name == 'FPGA_reg_read':
                return value
            self.relay.uc.mem_write(args[1], struct.pack('<I', value))
        elif name in ('FPGA_reg_write', 'fpga_reg_write'):
            self.values[args[0]] = args[1]
            self.events.append(['write', *args[:2]])
        elif name == 'drv_get_miniolt_chiptype':
            return self.backend
        elif name == 'open':
            return 3
        elif name == 'memset':
            self.relay.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
            return args[0]
        elif name == 'memcpy':
            self.relay.uc.mem_write(args[0], bytes(self.relay.uc.mem_read(args[1], args[2])))
            return args[0]
        elif name in ('htons', 'htonl'):
            size = 2 if name.endswith('s') else 4
            return int.from_bytes((args[0] & ((1 << (size * 8)) - 1)).to_bytes(size, 'little'), 'big')
        elif name == 'fpga_omci_send_msg':
            self.frame = bytes(self.relay.uc.mem_read(args[0], args[1]))
        elif name not in ('pthread_mutex_init', 'pthread_mutex_lock', 'pthread_mutex_unlock',
                          'flock', 'close', 'plr_log', 'plr_pondrv_log_module_id_get'):
            raise AssertionError(('unmodeled original call', name))
        return 0

    def initialize(self):
        data = self.asic.scratch + 0x2000
        self.asic.uc.mem_write(data, HOST)
        assert self.asic.call('drv_set_omci_dst_mac', data) == 0
        assert self.relay.call('plr_fpga_init') == 0

    def snapshot(self, id):
        data = self.asic.scratch + 0x2000
        words = []
        for name in ('drv_get_onu_ds_state_whole', 'drv_get_onu_us_state_whole'):
            # These are void getters; x0 retains the output pointer.
            self.asic.call(name, id + 1, data)
            words.append(struct.unpack('<I', self.asic.uc.mem_read(data, 4))[0])
        serial = self.relay.scratch + 0x2000
        assert self.relay.call('serial_read', 0, id, serial, serial + 16) == 0
        self.asic.call('drv_get_onu_eqd', id + 1, data)
        words.append(struct.unpack('<I', self.asic.uc.mem_read(data, 4))[0])
        return struct.pack('<III', *words) + bytes(self.relay.uc.mem_read(serial, 8))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asic', type=Path, required=True)
    parser.add_argument('--relay', type=Path, required=True,
                        help='retained rootfs/lib/libponrelay1p25g.so')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    headers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    read_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))
    write_fn = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint)
    delay_fn = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)

    class IO(C.Structure):
        _fields_ = [('context', C.c_void_p), ('read', read_fn), ('write', write_fn), ('delay', delay_fn)]

    class Link(C.Structure):
        _fields_ = [('host', C.c_ubyte * 6), ('olt', C.c_ubyte * 6),
                    ('ethertype', C.c_ushort), ('downstream_length', C.c_bool)]

    class Onu(C.Structure):
        _fields_ = [('downstream', C.c_uint), ('upstream', C.c_uint), ('eqd', C.c_uint),
                    ('serial', C.c_ubyte * 8)]

    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        source = '''#include <stdbool.h>
#include <errno.h>
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "rtl9617c-fttr-omci.h"
#include "rtl9617c-fttr-fpga.h"
#include "rtl9617c-fttr-onu.h"
int init(const struct rtl_fttr_reg_io *io, int backend, const unsigned char *host) {
 return rtl_fttr_omci_init(io, backend, host);
}
int link_read(const struct rtl_fttr_reg_io *io, int backend, struct rtl_fttr_omci_link *link) {
 return rtl_fttr_omci_link_read(io, backend, link);
}
int tx(const struct rtl_fttr_reg_io *io, int backend, int enabled) {
 return rtl_fttr_tx_enable(io, backend, enabled);
}
int snapshot(const struct rtl_fttr_reg_io *io, unsigned int id, struct rtl_fttr_onu *onu) {
 return rtl_fttr_onu_read(io, id, onu);
}
'''
        # Run the actual sysfs visibility predicates as well as the IO helpers.
        driver = (headers / 'rtl9617c-fttr.c').read_text()
        attrs = ('backend', 'version', 'reset', 'host_serdes', 'pon_serdes', 'omci_link',
                 'onu_table', 'onu_block', 'ploam', 'tx_enable', 'core_init', 'dba',
                 'tcont', 'management_grant', 'fpga_init')
        source += '''typedef unsigned short umode_t;
struct attribute { umode_t mode; };
struct device_attribute { struct attribute attr; };
struct bin_attribute { struct attribute attr; };
struct rtl_fttr { enum rtl_fttr_backend backend; struct rtl_fttr_reg_io reg_io; int control_lock; };
struct kobject { struct rtl_fttr *fttr; };
struct device { struct rtl_fttr *fttr; };
struct mdio_device { struct device dev; };
static int locks, removal_errors;
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
#define dev_err(...) (removal_errors++)
#define kobj_to_dev(k) (k)
#define dev_get_drvdata(d) ((d)->fttr)
'''
        source += '\n'.join('static struct device_attribute dev_attr_' + n + '={{0600}};' for n in attrs)
        source += '\nstatic struct bin_attribute bin_attr_onu_state={{0400}}, bin_attr_tcont_state={{0400}};\n'
        source += function(driver, 'rtl_fttr_attr_visible') + '\n'
        source += function(driver, 'rtl_fttr_bin_visible') + '\n'
        source += function(driver, 'rtl_fttr_remove') + '\n'
        source += '''int visible(int backend, int index) {
 struct rtl_fttr fttr={.backend=backend}; struct kobject k={&fttr};
 struct attribute *attrs[]={''' + ','.join('&dev_attr_' + n + '.attr' for n in attrs) + '''};
 return rtl_fttr_attr_visible(&k, attrs[index], index);
}
int bin_visible(int backend, int index) {
 struct rtl_fttr fttr={.backend=backend}; struct kobject k={&fttr};
 return rtl_fttr_bin_visible(&k, index ? &bin_attr_tcont_state : &bin_attr_onu_state, index);
}
int remove_device(const struct rtl_fttr_reg_io *io, int backend) {
 struct rtl_fttr fttr={.backend=backend,.reg_io=*io}; struct mdio_device mdio={{&fttr}};
 locks=removal_errors=0; rtl_fttr_remove(&mdio);
 return locks ? -1 : removal_errors;
}
'''
        (folder / 'native.c').write_text(source)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-shared', '-fPIC', '-I' + str(headers), str(folder / 'native.c'),
                        str(ROOT / 'port/packages/tyfza1-fttr/src/omci-wire.c'),
                        '-o', str(folder / 'native.so')], check=True)
        native = C.CDLL(str(folder / 'native.so'))
        native.init.argtypes = [C.POINTER(IO), C.c_int, C.c_char_p]
        native.link_read.argtypes = [C.POINTER(IO), C.c_int, C.POINTER(Link)]
        native.tx.argtypes = [C.POINTER(IO), C.c_int, C.c_int]
        native.snapshot.argtypes = [C.POINTER(IO), C.c_uint, C.POINTER(Onu)]
        native.remove_device.argtypes = [C.POINTER(IO), C.c_int]
        native.fttr_omci_pack.argtypes = [C.POINTER(Link), C.c_ushort, C.c_char_p, C.c_size_t,
                                         C.c_void_p, C.c_size_t]
        events, values, fail_at = [], {}, 0

        def rd(ctx, reg, output):
            value = values.get(reg, 0)
            events.append(['read', reg, value]); output[0] = value
            return -errno.EIO if len(events) == fail_at else 0

        def wr(ctx, reg, value):
            events.append(['write', reg, value]); values[reg] = value
            return -errno.EIO if len(events) == fail_at else 0

        io = IO(None, read_fn(rd), write_fn(wr), delay_fn(lambda *_: None))
        cases = []
        for backend in (1, 2):
            for seed in (0, 0x123488a8, 0xffffffff):
                initial = {0x30004: seed, 0x4000c: seed, 0x20: seed,
                           0x30002: 0x89abcdef, 0x30003: 0x4567, 0x30005: 8}
                stock = Stock(args.asic, args.relay, backend, initial)
                stock.initialize()
                events.clear(); values.clear(); values.update(initial)
                assert native.init(C.byref(io), backend, HOST) == 0
                assert events == stock.events and values == stock.values
                expected = list(events)
                for fail_at in range(1, len(expected) + 1):
                    events.clear(); values.clear(); values.update(initial)
                    assert native.init(C.byref(io), backend, HOST) == -errno.EIO
                    assert events == expected[:fail_at]
                fail_at = 0
                values.clear(); values.update(stock.values); events.clear()
                link = Link()
                assert native.link_read(C.byref(io), backend, C.byref(link)) == 0
                assert bytes(link.host) == HOST and bytes(link.olt) == bytes.fromhex('001111111111')
                assert link.ethertype == 0x8888 and not link.downstream_length
                stock.relay.uc.mem_write(stock.relay.symbols['g_pfpacket_info'] + 0x28, HOST)
                assert stock.relay.call('plr_omci_sw_crc32_table_init') == 0
                for id in (0, 5, 63):
                    body = bytes([0x80, id, 0x49, 10]) + bytes(range(40))
                    data = stock.relay.scratch + 0x2000
                    stock.relay.uc.mem_write(data, body)
                    assert stock.relay.call('drv_gponomci_send_packet', 0, id, data, 48) == 0
                    output = C.create_string_buffer(64)
                    assert native.fttr_omci_pack(C.byref(link), id, body, len(body), output, 64) == 64
                    assert output.raw == stock.frame
                for enabled in (0, 1):
                    stock.events.clear(); events.clear()
                    assert stock.relay.call('drv_gpon_trx_set', 0, enabled) == 0
                    assert native.tx(C.byref(io), backend, enabled) == 0
                    assert events == stock.events and values == stock.values
                    expected = list(events)
                    for fail_at in (1, 2):
                        values[0x20] = expected[0][2]; events.clear()
                        assert native.tx(C.byref(io), backend, enabled) == -errno.EIO
                        assert events == expected[:fail_at]
                    fail_at = 0
                cases.append({'backend': backend, 'seed': seed, 'init_tx_and_baseline_frames_match': True})
            for id in (0, 5, 63):
                initial = {0x40401 + id * 2: 0x00fc0000, 0x40803 + id * 4: 0xa5abcdef,
                           0x40801 + id * 4: 0x54455354, 0x40800 + id * 4: 0x01234567,
                           0x40802 + id * 4: 0x9876}
                stock = Stock(args.asic, args.relay, backend, initial)
                expected = stock.snapshot(id)
                values.clear(); values.update(initial); events.clear()
                onu = Onu()
                assert native.snapshot(C.byref(io), id, C.byref(onu)) == 0
                assert bytes(onu) == expected and events == stock.events
                cases.append({'backend': backend, 'onu': id, 'snapshot_matches': True})
            for index, name in enumerate(attrs):
                assert bool(native.visible(backend, index)) == (name in
                    ('backend', 'version', 'fpga_init', 'omci_link', 'onu_table', 'tx_enable', 'tcont'))
            assert native.bin_visible(backend, 0) and native.bin_visible(backend, 1)
            for fail_at in (0, 1, 2):
                events.clear(); values[0x20] = 0xa55a1000
                assert native.remove_device(C.byref(io), backend) == int(bool(fail_at))
                expected = [['read', 0x20, 0xa55a1000], ['write', 0x20, 0xa55a1001]]
                assert events == expected[:fail_at or 2]
            fail_at = 0
        for index, name in enumerate(attrs):
            assert bool(native.visible(3, index)) == (name != 'fpga_init')
        assert native.bin_visible(3, 0) and native.bin_visible(3, 1)
        for backend in (0, 4):
            events.clear()
            assert native.init(C.byref(io), backend, HOST) == -errno.EOPNOTSUPP
            assert native.link_read(C.byref(io), backend, C.byref(Link())) == -errno.EOPNOTSUPP
            assert native.tx(C.byref(io), backend, 1) == -errno.EOPNOTSUPP
            assert not events
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'asic_sha256': hashlib.sha256(args.asic.read_bytes()).hexdigest(),
        'relay_sha256': hashlib.sha256(args.relay.read_bytes()).hexdigest(), 'cases': cases,
        'register_errors_stop': True, 'sysfs_visibility_checked': True,
        'scope': 'Original FPGA relay initialization/TX/serial and ASIC-library getters with FPGA backend; '
                 'baseline sender compared byte-for-byte. MDIO and device state modeled. '
                 'No optical startup, extended FPGA OMCI, DBA/service provisioning or hardware acceptance.'}, indent=2) + '\n')
    print(f'FPGA control: {len(cases)} original/native groups, 18 baseline frames, IO failure cutoffs and sysfs visibility passed')


if __name__ == '__main__':
    main()
