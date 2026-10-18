#!/usr/bin/env python3
"""Check native P6 CF ownership/publication against actual original writers."""
import argparse
import ctypes
import errno
import json
from pathlib import Path
import subprocess
import tempfile

from trace_stock_fttr_cf import FactoryRules, CfTables


HARNESS = r'''
#include <stdbool.h>
#include <errno.h>
#include <string.h>
#include "rtl9617c-fttr-cf.h"
static struct rtl_fttr_cf cf;
static struct rtl_dq_io io;
int claim(unsigned int (*read)(void *, unsigned int),
          void (*write)(void *, unsigned int, unsigned int),
          int (*indirect)(void *, unsigned int, unsigned int))
{
    memset(&cf, 0, sizeof(cf));
    io = (struct rtl_dq_io){0, read, write, indirect};
    return rtl_fttr_cf_claim(&io, &cf);
}
int destination(unsigned int target) { return rtl_fttr_cf_destination(&io, &cf, target); }
int release(void) { return rtl_fttr_cf_release(&io, &cf); }
void state(int *out)
{
    out[0]=cf.owned; out[1]=cf.active; out[2]=cf.error;
    out[3]=cf.cleanup_error; out[4]=cf.index; out[5]=cf.destination;
}
'''


class Hardware:
    def __init__(self, initial):
        self.tables = {key: list(value) for key, value in initial.items()}
        self.registers, self.events = {}, []
        self.fail, self.skip = None, None
        self.read = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)(
            lambda context, reg: self.registers.get(reg, 0))
        self.write = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)(
            lambda context, reg, value: self.registers.__setitem__(reg, value))
        self.indirect = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint)(self.access)

    def access(self, context, reg, command):
        index = command & (1023 if reg == 0x1e04 else 511)
        write = bool(command & (1 << 30))
        data = 0x1e10 if reg == 0x1e04 else 0x1e20
        key = (reg, index)
        words = [self.registers.get(data-i*4, 0) for i in range(3)]
        self.events.append((reg, index, write, list(words) if write else None))
        if write:
            if key == self.skip:
                self.skip = None
                return 0
            self.tables[key] = words
            if key == self.fail:
                self.fail = None
                return -errno.ETIMEDOUT  # Commit happened, completion was not confirmed.
        else:
            empty = [0xffffffff, 0xffffffff, 0xfffeffff] if reg == 0x1e04 else [0, 0, 0]
            for i, value in enumerate(self.tables.get(key, empty)):
                self.registers[data-i*4] = value
        return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fc', type=Path, required=True)
    p.add_argument('--ni', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    factory = FactoryRules(args.fc)
    assert factory.oracle.call('rtk_8277c_cls_init') == 0
    stock = CfTables(args.ni)
    wan = stock.add(*factory.rules[0])
    recipe = bytearray(factory.rules[1][1])
    recipe[72:76] = (6).to_bytes(4, 'little')
    recipe[152:156] = (63).to_bytes(4, 'little')
    p6 = stock.add(1, bytes(recipe))
    root = Path(__file__).resolve().parents[1]
    headers = root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    initial = {(0x1e04, 0): wan['tcam_x'], (0x1e04, 512): wan['tcam_y'], (0x1e14, 0): wan['action'],
        (0x1e04, 1): [0x12345678, 0x87654321, 0x13572468],
        (0x1e04, 513): [0x01020304, 0xaabbccdd, 0x24682468],
        (0x1e14, 1): [0x11223344, 0x55667788, 0x99aabbcc]}
    with tempfile.TemporaryDirectory(dir=root/'artifacts/fttr') as directory:
        directory = Path(directory)
        (directory/'cf.c').write_text(HARNESS)
        subprocess.run(['cc','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(headers),
            str(directory/'cf.c'),'-o',str(directory/'cf.so')], check=True)
        native = ctypes.CDLL(str(directory/'cf.so'))
        def state():
            values = (ctypes.c_int*6)(); native.state(values); return list(values)
        hw = Hardware(initial)
        assert native.claim(hw.read, hw.write, hw.indirect) == 0 and state()[4] == 1
        assert native.destination(25) == 0
        assert hw.tables[0x1e04, 1] == p6['tcam_x'] and hw.tables[0x1e04, 513] == p6['tcam_y']
        assert hw.tables[0x1e14, 1] == p6['action']
        commits = [(reg,index) for reg,index,write,_ in hw.events if write]
        assert commits.index((0x1e14,1)) < commits.index((0x1e04,513))
        assert native.destination(16) == 0 and hw.tables[0x1e14,1] == [0,0x1060,0]
        assert native.release() == 0 and hw.tables == initial and state()[:2] == [0,0]
        # A write ACK without a changed action must not be accepted as success.
        assert native.claim(hw.read,hw.write,hw.indirect) == 0
        hw.skip = (0x1e14,1)
        assert native.destination(25) == -errno.EUCLEAN and state()[2] == -errno.EUCLEAN
        assert not state()[1] and native.release() == 0 and hw.tables == initial
        # A failed retirement retains ownership, both errors and the saved slot.
        assert native.claim(hw.read,hw.write,hw.indirect) == 0
        hw.fail = (0x1e14,1)
        assert native.destination(25) == -errno.ETIMEDOUT
        hw.fail = (0x1e04,513)
        assert native.release() == -errno.ETIMEDOUT
        assert state()[0] and state()[2:4] == [-errno.ETIMEDOUT,-errno.ETIMEDOUT]
        assert native.release() == 0 and hw.tables == initial
        # A pre-existing wildcard would shadow a later source6 rule.
        wildcard = stock.add(*factory.rules[1])
        hw.tables[0x1e04,0] = wildcard['tcam_x']; hw.tables[0x1e04,512] = wildcard['tcam_y']
        assert native.claim(hw.read,hw.write,hw.indirect) == -errno.EBUSY and not state()[0]
    report = {'checks': ['native source6 TCAM/action bytes match original FC/NI execution',
        'action is verified before the valid key is published; CPU16 changes only the owned action',
        'unrelated source7 rule and the exact prior unused slot survive activation/restoration',
        'unchanged false-success write is exposed; timeout/failed retirement retain ownership and errors',
        'a higher-priority conflicting rule fails explicitly'],
        'scope': 'actual production header with modeled indirect hardware; original ARM64 producer/writers; no physical CF rule or offload enabled'}
    args.output.write_text(json.dumps(report,indent=2)+'\n'); print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
