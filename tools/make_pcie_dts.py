#!/usr/bin/env python3
"""Generate the native PCIe test DTS from verified stock board inputs."""
import argparse
import hashlib
import json
from pathlib import Path
from stock_dtb import cells, fdt_nodes

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--stock', type=Path, default=root / 'evidence/wireless-study-20261008/evidence/stock/live-fit.dtb')
parser.add_argument('--inputs', type=Path, default=root / 'manifests/wifi-board-inputs.json')
parser.add_argument('--output', type=Path, default=root / 'port/overlay/target/linux/rtl9617c/dts/rtl9617c-tyfza1-pcie.dts')
args = parser.parse_args()
data = args.stock.read_bytes()
board = json.loads(args.inputs.read_text())
assert hashlib.sha256(data).hexdigest() == board['stock_DTB_sha256']
nodes = fdt_nodes(data)
text = '// SerDes data:stock DTB SHA256 ' + board['stock_DTB_sha256'] + '.\n' + '''// SPDX-License-Identifier: GPL-2.0-only
#include "rtl9617c-tyfza1-pon-status.dts"

/ { model = "Tianyi TYFZA1 (native PON/PCIe bring-up)"; };

&{/soc} {
 pcie_controls: pcie-controls@f4320098 {
  compatible = "realtek,rtl9617c-pcie-controls";
  reg = <0 0xf4320098 0 4>, <0 0xf43200a0 0 4>,
        <0 0xf43200c4 0 4>, <0 0xf432036c 0 4>;
  reg-names = "core-reset", "phy-reset", "phy-control", "power";
  realtek,global-config = <&global_config>;
  #clock-cells = <1>;
  #reset-cells = <1>;
  #phy-cells = <1>;
 };
'''
for host in board['PCIe_hosts']:
    if host['status'] != 'okay':
        continue
    index = host['index']
    assert index in (0, 1) and host['lanes'] == 1
    reset = host['resets']['device_reset']['endpoint_GPIO']
    assert not reset['unused'] and reset['flags'] == 1
    assert host['resets']['core_reset']['arguments'] == [6 + index]
    assert host['resets']['phy_reset']['arguments'] == [index]
    resources = host['resources']
    base = int(resources['rc_dbi']['address'], 16)
    global_address = int(resources['glbl_regs']['address'], 16)
    raw = nodes[host['node']]
    ready_ms = int.from_bytes(raw['ready-time'], 'big')
    text += f'\n pcie{index}: pcie@{global_address:x} {{\n'
    text += '  compatible = "realtek,rtl9617c-pcie";\n  reg = '
    registers = []
    for name in ('glbl_regs', 'rc_dbi', 'config', 'iatu', 'serdes_phy'):
        registers.append(f"<0 {resources[name]['address']} 0 0x{resources[name]['bytes']:x}>")
    registers.append('<' + ' '.join(host['SoC_ATU']['reg_cells']) + '>')
    text += ',\n        '.join(registers) + ';\n'
    text += f'''  reg-names = "global", "dbi", "config", "atu", "serdes", "coherent-atu";
  device_type = "pci";
  #address-cells = <3>;
  #size-cells = <2>;
  bus-range = <0 255>;
  linux,pci-domain = <{index}>;
  ranges = <''' + ' '.join(hex(v) for v in cells(raw['ranges'])) + f'''>;
  dma-ranges = <0x02000000 0 0 0 0 0 0x20000000>;
  dma-coherent;
  interrupts = <''' + ' '.join(str(v) for v in host['interrupts_GIC_cells'][:3]) + f'''>;
  interrupt-names = "wrapper";
  #interrupt-cells = <1>;
  interrupt-map-mask = <0 0 0 7>;
  interrupt-map = <0 0 0 1 &pcie{index}_intc 0>,
                  <0 0 0 2 &pcie{index}_intc 1>,
                  <0 0 0 3 &pcie{index}_intc 2>,
                  <0 0 0 4 &pcie{index}_intc 3>;
  clocks = <&pcie_controls {index}>;
  resets = <&pcie_controls {index}>, <&pcie_controls {index + 2}>;
  reset-names = "core", "phy";
  phys = <&pcie_controls {index * 2}>;
  phy-names = "pcie";
  reset-gpios = <&gpio0 {reset['native_pin']} GPIO_ACTIVE_LOW>; // stock bank{reset['bank']},pin{reset['bank_pin']}
  reset-delay-ms = <{ready_ms}>;
  num-lanes = <1>;
  realtek,controller-index = <{index}>;
  realtek,ddr-bytes = <0x20000000>;
  realtek,coherent-hi-addr = <{host['SoC_ATU']['coherent_hi_addr']}>;
  // Taurus GPL uses the B/default branch; verify cut on first board bring-up.
  realtek,serdes-profile = "B";
'''
    for cut in 'AB':
        values = cells(raw['serdes-cfg-data' + cut])
        text += f'  serdes-cfg-data{cut} = <\n'
        for position in range(0, len(values), 8):
            text += '   ' + ' '.join(hex(v) for v in values[position:position + 8]) + '\n'
        text += '  >;\n'
    text += f'''  pcie{index}_intc: interrupt-controller {{
   interrupt-controller;
   #address-cells = <0>;
   #interrupt-cells = <1>;
  }};
 }};
'''
text += '};\n'
args.output.write_text(text)
print(json.dumps({'output': str(args.output), 'reset_pins': [
    h['resets']['device_reset']['endpoint_GPIO']['native_pin']
    for h in board['PCIe_hosts'] if h['status'] == 'okay']}, indent=2))
