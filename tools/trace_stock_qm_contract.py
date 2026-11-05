#!/usr/bin/env python3
"""Execute TYFZA1 CPU QM/CB initialization with explicit modeled MMIO."""
from pathlib import Path
import json
import re
import struct
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_CODE
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

config = (root / 'analysis/private/live-files/etc/default_scfg.txt').read_text()
values = {}
for name, value in re.findall(r'\b(CFG_ID_\w+)\s*=\s*(\{[^}]*\}|[^;\n]+)', config):
    try:
        values[name] = ([int(x.strip(), 0) for x in value.strip('{}').split(',')]
                        if value.startswith('{') else int(value.strip(), 0))
    except ValueError:
        pass
calls, writes = [], []
voq_profiles, port_profiles = {}, {}
with (root / 'analysis/private/live-files/lib/modules/5.10.70/extra/libscfg.ko').open('rb') as stream:
    elf = ELFFile(stream)
    symbols = elf.get_section_by_name('.symtab')
    table = symbols.get_symbol_by_name('ca_cfg_id')[0]
    relocations = {r['r_offset']: r for section in elf.iter_sections()
                   if isinstance(section, RelocationSection) and section['sh_info'] == table['st_shndx']
                   for r in section.iter_relocations()}
    config_ids = {}
    for index in range(table['st_size'] // 32):
        relocation = relocations[table['st_value'] + index * 32]
        symbol = symbols.get_symbol(relocation['r_info_sym'])
        data = elf.get_section(symbol['st_shndx']).data()
        offset = symbol['st_value'] + relocation['r_addend']
        config_ids[str(index)] = 'CFG_ID_' + data[offset:data.index(0, offset)].decode()


def cstring(address):
    out = bytearray()
    while True:
        value = oracle.uc.mem_read(address + len(out), 1)[0]
        if not value:
            return out.decode()
        out.append(value)


def boundary(name, args):
    if name == 'aal_scfg_read':
        key = cstring(args[0])
        key = config_ids.get(key, key)
        if key not in values:
            raise AssertionError(('missing factory configuration', key, args[1]))
        value = values[key]
        if isinstance(value, list):
            width = args[1] // len(value)
            data = b''.join(v.to_bytes(width, 'little') for v in value)
        else:
            data = value.to_bytes(args[1], 'little')
        oracle.uc.mem_write(args[2], data)
        calls.append({'name': name, 'key': key, 'bytes': args[1], 'value': value})
        return 0
    if name == 'ca_soc_data_get':
        oracle.uc.mem_write(args[0], bytes(64))
        return 0
    if name in ('printk', 'aal_l3qm_insert_magic_number'):
        return 0
    if name == 'aal_l3qm_get_buffer_size_index':
        assert args[0] == 2048
        return 5
    if name == 'memset':
        oracle.uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
        return args[0]
    if name.startswith('aal_l3_te_cb_'):
        if name == 'aal_l3_te_cb_voq_profile_sel_set.part.0':
            voq_profiles[args[0]] = args[1]
        if name == 'aal_l3_te_cb_port_profile_sel_set.part.0':
            port_profiles[args[0]] = args[1]
        if name == 'aal_l3_te_cb_voq_profile_sel_get':
            oracle.uc.mem_write(args[2], bytes([voq_profiles[args[1]]]))
        if name == 'aal_l3_te_cb_port_profile_sel_get':
            oracle.uc.mem_write(args[2], bytes([port_profiles[args[1]]]))
        row = {'name': name, 'args': args[:3]}
        if name in ('aal_l3_te_cb_port_free_buf_cnt_set',
                    'aal_l3_te_cb_voq_threshold_profile_set',
                    'aal_l3_te_cb_port_threshold_profile_set',
                    'aal_l3_te_cb_port_group_threshold_profile_set'):
            row['argument_bytes'] = bytes(oracle.uc.mem_read(args[3], 16)).hex()
        calls.append(row)
        return 0
    raise AssertionError((name, args))


names = ['aal_l3qm_init_startup_config', 'aal_l3qm_init_cpu_epp', 'aal_l3qm_init_empty_buffer',
         'aal_l3_te_cb_init_startup_config', 'aal_l3_te_cb_init']
setters = ['aal_l3_te_cb_port_free_buf_cnt_set', 'aal_l3_te_cb_voq_threshold_profile_set',
           'aal_l3_te_cb_port_threshold_profile_set', 'aal_l3_te_cb_port_profile_sel_set.part.0',
           'aal_l3_te_cb_voq_profile_sel_set.part.0', 'aal_l3_te_cb_port_profile_sel_get',
           'aal_l3_te_cb_voq_profile_sel_get', 'aal_l3_te_cb_dqsch_voq_thrsh_profile_set',
           'aal_l3_te_cb_dqsch_port_thrsh_profile_set',
           'aal_l3_te_cb_pool_profile_set.part.0']
register_mode = '--registers' in sys.argv
if register_mode:
    names += setters
oracle = ModuleOracle(root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko',
                      names, boundary, external_symbols={'vabits_actual': 0x5007000,
                      'memstart_addr': 0x5007010, 'kimage_voffset': 0x5007020})
ni = 0x6000000
oracle.uc.mem_map(ni, 0x10000)
oracle.word(oracle.symbols['g_ne_reg_iobase'][0], ni)
register_header = (root / 'evidence/wireless-study-20261009-104746/evidence/reference/registers/rtl8277c_registers.h').read_text()
defines = {name: int(value, 0) for name, value in re.findall(
    r'^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|[0-9]+)\s*$', register_header, re.M)}
for name, address in defines.items():
    if 0xf4300000 <= address < 0xf4310000 and name + '_dft' in defines:
        for index in range(defines.get(name + '_COUNT', 1)):
            offset = address - 0xf4300000 + index * defines.get(name + '_STRIDE', 0)
            oracle.uc.mem_write(ni + offset, struct.pack('<I', defines[name + '_dft']))
tables = {0x95a8: {}, 0x95b0: {}, 0x95c0: {}, 0x9654: {}}
table_data = {0x95a8: [0x95ac], 0x95b0: [0x95bc, 0x95b8, 0x95b4],
              0x95c0: [0x95c4], 0x9654: [0x9658]}
pending = []


def mmio_write(uc, access, address, size, value, unused):
    writes.append({'register': hex(0xf4300000 + address - ni), 'value': hex(value), 'bytes': size})
    if register_mode and address - ni in tables:
        pending.append((address - ni, value))


def complete_table_access(uc, address, size, unused):
    # Complete the modeled indirect request before the following instruction.
    while pending:
        register, value = pending.pop(0)
        index = value & 255
        data = table_data[register]
        if value & (1 << 30):
            tables[register][index] = [oracle.unpack(ni + reg, 'I')[0] for reg in data]
        else:
            for reg, word in zip(data, tables[register].get(index, [0] * len(data))):
                uc.mem_write(ni + reg, struct.pack('<I', word))
        uc.mem_write(ni + register, struct.pack('<I', value & 0x7fffffff))


oracle.uc.hook_add(UC_HOOK_MEM_WRITE, mmio_write, begin=ni, end=ni + 0xffff)
if register_mode:
    oracle.uc.hook_add(UC_HOOK_CODE, complete_table_access)
oracle.call('aal_l3qm_init_startup_config', instruction_budget=100000)
resolved = {}
for name in ('l3qm_cpu_epp_per_voq', 'l3qm_desc_size', 'l3qm_desc_per_epp',
             'ca_ni_cpu_cpu_port_count', 'l3qm_cpu_voq_per_port', 'l3qm_epp_profile_cpu_id',
             'l3qm_epp_profile_cpu_map_mode', 'l3qm_eq_profile_cpu_pool0_buf_count',
             'ca_ni_napi_budget'):
    address, size = oracle.symbols[name]
    resolved[name] = int.from_bytes(oracle.uc.mem_read(address, min(size, 4)), 'little')
# Relocate reserved RAM only; keep factory descriptor counts and settings.
oracle.word(oracle.symbols['l3qm_epp_profile_cpu_rx_fifo_start_addr'][0], 0x7000000)
oracle.uc.mem_map(0x7000000, 0x100000)
oracle.uc.mem_write(oracle.symbols['l3qm_epp_profile_cpu_rx_fifo_phy_addr'][0], struct.pack('<I', 0x7000000))
oracle.uc.mem_write(oracle.symbols['ca_ne_ddr_coherent_reserved_size'][0], struct.pack('<I', 0x100000))
oracle.call('aal_l3_te_cb_init', 0, instruction_budget=200000)
before_cpu_calls = len(calls)
before_cpu_writes = len(writes)
# The board initializes CB before ca_ni_init_l3qm overrides CPU pool credits.
# Stop the real function at the first DQ-only instruction; no optical/DQ setup.
from unicorn.arm64_const import UC_ARM64_REG_SP
oracle.uc.reg_write(UC_ARM64_REG_SP, oracle.scratch + 0xff00)
start = oracle.functions['aal_l3qm_init_empty_buffer']
oracle.uc.emu_start(start, start + 0x354, count=200000)
cpu_writes = writes[before_cpu_writes:]
before_epp_writes = len(writes)
oracle.call('aal_l3qm_init_cpu_epp', instruction_budget=200000)
epp_writes = writes[before_epp_writes:]
result = {'resolved_factory_values': resolved, 'EPP_init_writes': epp_writes,
          'configuration_reads_and_CB_calls': calls,
          'CPU_pool_reinit_calls': calls[before_cpu_calls:],
          'CPU_pool_reinit_writes': cpu_writes,
          'scope': 'Actual board ARM64 startup, CPU EPP init, CB init and CPU-pool prefix of empty-buffer init; reserved memory relocated; CB setter boundaries recorded, not executed; reset register defaults modeled. DQ-specific tail not executed.'}
if register_mode:
    result['scope'] = ('Actual board startup, CPU EPP init, CB init and CPU empty-buffer prefix '
                       'including actual CB setters/getters; indirect table transactions modeled; '
                       'reserved memory relocated. DQ tail and later radio changes not executed.')
    result['indirect_tables'] = {hex(k): {str(i): [hex(v) for v in row] for i, row in t.items()}
                                 for k, t in tables.items()}
    result['final_registers'] = {hex(reg): hex(oracle.unpack(ni+reg, 'I')[0]) for reg in
                                 ([0x61c8, 0x658c, 0x950c, 0x95a0, 0x95e0, 0x963c] + list(range(0x9560, 0x95a0, 4)))}
name = 'factory-register-contract.json' if register_mode else 'factory-contract.json'
output = root / 'artifacts/wifi-factory-epp-contract-20261023'
output.mkdir(parents=True, exist_ok=True)
(output / name).write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'resolved': resolved, 'final_registers': result.get('final_registers'),
                  'calls': len(calls)}, indent=2))
