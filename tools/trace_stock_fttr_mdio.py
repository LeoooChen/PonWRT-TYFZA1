#!/usr/bin/env python3
"""Execute stock MiniOLT MDIO functions offline; never access a live device."""
import struct

from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X5


class StockMdio:
    def __init__(self, module, backend, values):
        self.events = []
        self.values = {reg: list(data) for reg, data in values.items()}
        self.address = 0
        self.low = 0
        self.read_value = 0
        self.oracle = ModuleOracle(module,
            ['rtk_taurus_misc_ioctl', 'rtk_get_miniolt_chiptype'], self.callback,
            external_symbols={'gic_nonsecure_priorities': 0x5007000})
        address = self.oracle.symbols['MiniOltChipType'][0]
        self.oracle.uc.mem_write(address, struct.pack('<i', backend))

    def callback(self, name, args):
        if name.startswith('_copy_from_user') or name.startswith('_copy_to_user'):
            self.oracle.uc.mem_write(args[0], bytes(self.oracle.uc.mem_read(args[1], 12)))
        elif name in ('aal_mdio_set', 'aal_mdio_read'):
            phy, reg = args[2], args[3]
            fifth = self.oracle.uc.reg_read(UC_ARM64_REG_X5)
            if name == 'aal_mdio_set':
                value = fifth & 0xffff
                self.events.append(['write', reg, value])
                if reg == 16:
                    self.address = value
                elif reg == 17:
                    self.address |= (value & 0x7fff) << 16
                    if value & 0x8000:
                        values = self.values.get(self.address, [0])
                        self.read_value = values[0]
                        if len(values) > 1:
                            values.pop(0)
                elif reg == 18:
                    self.low = value
                elif reg == 19:
                    self.values[self.address] = [value << 16 | self.low]
            else:
                value = (self.read_value >> (16 if reg == 21 else 0)) & 0xffff
                self.events.append(['read', reg, value])
                self.oracle.uc.mem_write(fifth, struct.pack('<H', value))
            assert phy == 15
        elif name in ('mutex_lock', 'mutex_unlock', '__const_udelay', 'usleep_range'):
            pass
        else:
            raise AssertionError(('unmodeled stock call', name, args))
        return 0

    def transfer(self, reg, value=0, write=False):
        address = self.oracle.scratch + 0x1000
        self.oracle.uc.mem_write(address, struct.pack('<III', 15, reg, value))
        result = self.oracle.call('rtk_taurus_misc_ioctl', 0,
                                 0xc0045203 if write else 0xc0045202, address)
        value = self.oracle.unpack(address + 8, 'I')[0]
        return (result if result < 1 << 63 else result - (1 << 64)), value
