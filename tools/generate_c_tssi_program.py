#!/usr/bin/env python3
"""Generate native C TSSI constant tables from original ELF trace output."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'manifests/radio-stock-C-TSSI-program.json'
report = json.loads(source.read_text())
assert report['original_host_sha256'] == '745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e'
assert report['inputs']['RFE'] == 54 and report['inputs']['band'] == 1
lines = ['/* SPDX-License-Identifier: GPL-2.0-only */',
         '/* Actual original C normal RFE54/5G TSSI MMIO operands. */',
         '#ifndef RTL_AX_AP_C_TSSI_PROGRAM_H', '#define RTL_AX_AP_C_TSSI_PROGRAM_H',
         'struct rtl_ax_c_tssi_op { u32 address, mask, value; u8 rf_path; };',
         'struct rtl_ax_c_tssi_step { const struct rtl_ax_c_tssi_op *ops; u16 count; s8 thermal_path, alignment_path; };']
steps = []
for index, program in enumerate(report['programs']):
    if 'tmeter' in program['function']:
        steps.append('{ NULL, 0, %s, -1 }' % program['path'])
        continue
    if program.get('hardware_measurement_required'):
        steps.append('{ NULL, 0, -1, %s }' % program['path'])
        continue
    name = 'rtl_ax_c_tssi_ops_' + str(index)
    lines.append('/* ' + program['function'] + ',path=' + str(program['path']) + ' */')
    lines.append('static const struct rtl_ax_c_tssi_op ' + name + '[] = {')
    for operation in program['operations']:
        lines.append('\t{ 0x%x, 0x%08x, 0x%08x, %d },' %
                     (operation['address'], operation['mask'], operation['value'], operation.get('RF_path', 255)))
    lines.append('};')
    steps.append('{ %s, ARRAY_SIZE(%s), -1, -1 }' % (name, name))
lines.append('static const struct rtl_ax_c_tssi_step rtl_ax_c_tssi_steps[] = {')
lines.extend('\t' + step + ',' for step in steps)
lines.extend(['};', '#endif', ''])
output = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/c-tssi-program.h'
output.write_text('\n'.join(lines))
print(output)
