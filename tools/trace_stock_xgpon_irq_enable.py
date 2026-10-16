#!/usr/bin/env python3
"""Execute the board's final XGTC interrupt initialization after ACTP setup."""
import hashlib
import json
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE

root = Path(__file__).resolve().parents[1]
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko'
records = []
for mode in (4, 5):
    writes = []

    def callback(name, args):
        if name in ('ca_spin_lock_init', '__kfifo_alloc', 'tasklet_init', 'ca_printf', 'printk'):
            return 0
        raise RuntimeError((name, args))

    oracle = ModuleOracle(module, ['aal_xgpon_intr_init', 'aal_xgpon_intr_clr'], callback)
    base = 0x6000000
    oracle.uc.mem_map(base, 0x10000)
    oracle.word(oracle.symbols['ca_venus_pon_reg_iobase'][0], base)
    oracle.uc.mem_write(oracle.symbols['aal_pon_mac_mode'][0], struct.pack('<I', mode))
    oracle.uc.mem_write(base + 0x4d2c, struct.pack('<I', 0x07a3dfff))
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE,
        lambda uc, kind, address, size, value, data: writes.append([address - base, value]),
        begin=base, end=base + 65535)
    assert oracle.call('aal_xgpon_intr_init', 0) == 0
    enable = oracle.unpack(base + 0x4d2c, 'I')[0]
    assert enable == 0x0fffffff and enable & (1 << 27)
    records.append(dict(mode=mode, ACTP_initial_enable=0x07a3dfff,
                        final_ACTP_transition_enable=enable, mmio_writes=writes))

result = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), records=records,
              scope='Actual original interrupt initializer and clear function. Lock/queue allocation '
                    'and logging modeled; register writes observed, no physical interrupt delivery.')
(root / 'logs/stock-xgpon-irq-enable.json').write_text(json.dumps(result, indent=2) + '\n')
print('Original final XGTC IRQ setup overrides the ACTP mask with0x0fffffff, including O6 resynchronization bit27')
