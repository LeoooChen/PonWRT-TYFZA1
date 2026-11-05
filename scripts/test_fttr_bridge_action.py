#!/usr/bin/env python3
"""Compare FTTR bridge PE actions to the actual stock FC producer and NI packer."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fc', type=Path, required=True)
parser.add_argument('--ni', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'


def boundary(name, values):
    # Match the native parser's programmed TPID table; the producer itself runs.
    if name == 'aal_l3fe_pp_top_tpid_get':
        fc.uc.mem_write(values[1], bytes([{0x8100: 1, 0x88a8: 2}[values[0]]]))
        return 0
    raise AssertionError(('unmodeled original call', name, values))


fc = ModuleOracle(args.fc, ['_rtk_8277c_asic_flow_action_gen'], boundary)
ni = ModuleOracle(args.ni, ['convert_act_flow_nomal_mode'], boundary)
key, action, result = [fc.scratch + offset for offset in (0x100, 0x400, 0x800)]
source = r'''
#include "rtl9617c-fe-format.h"
void pack(unsigned int protocol,unsigned int count,unsigned int destination,unsigned int *words,unsigned int *mask) {
 struct rtl_fe_ipv4_action action={.bridge=1,.vlan_only=rtl_fe_bridge_vlan_only(protocol),
  .vlan_set=1,.vlan_count=count,.destination=destination,.cos=5,
  .vlan={{.vid=263,.tpid=1,.priority=5,.dei=1},{.vid=100,.tpid=2,.priority=3,.dei=0}}};
 /* Execute the production final P6 action selection, not a second policy. */
 struct { void *netdev; } output={destination==6?(void *)6:(void *)1};
 struct {void *fttr_netdev;} state={(void *)6},*fe=&state;
 struct {typeof(output) *output;struct rtl_fe_ipv4_action action;} data={&output,action};
P6_ACTION
 action=data.action;
 const enum rtl_fe_action_field fields[]={RTL_FE_DPID_VLD,RTL_FE_DPID_PRI,RTL_FE_PERMIT,
  RTL_FE_MCGID,RTL_FE_MC,RTL_FE_SMAC_TRANS,RTL_FE_IP_TTL_DEC1,
  RTL_FE_IP_TTL_ZERO_DISCARD_EN1,RTL_FE_MAC_DA_IDX_VLD,RTL_FE_COS_UPDATE_EN,RTL_FE_COS1,
  RTL_FE_VLAN_VLD1,RTL_FE_VLAN_CNT1,RTL_FE_MODIFY_VLAN_ONLY_VLD,RTL_FE_MODIFY_VLAN_ONLY};
 rtl_fe_ipv4_action(words,&action);
 for(unsigned int i=0;i<RTL_FE_ACTION_WORDS;i++)mask[i]=0;
 for(unsigned int i=0;i<sizeof(fields)/sizeof(fields[0]);i++)rtl_fe_action_set(mask,fields[i],~0U);
 if(destination==6)rtl_fe_action_set(mask,RTL_FE_DEEPQ,1);
 for(unsigned int i=0;i<count;i++) {
  rtl_fe_action_set(mask,i?RTL_FE_INNER_TPID_ENC1:RTL_FE_TOP_TPID_ENC1,~0U);
  rtl_fe_action_set(mask,i?RTL_FE_INNER_VID1:RTL_FE_TOP_VID1,~0U);
  rtl_fe_action_set(mask,i?RTL_FE_INNER_802_1P1:RTL_FE_TOP_802_1P1,~0U);
  rtl_fe_action_set(mask,i?RTL_FE_INNER_DEI1:RTL_FE_TOP_DEI1,~0U);
 }
}
'''

fe_source = (drivers / 'rtl9617c-fe.c').read_text()
selection = fe_source[fe_source.index(' if(data.output->netdev==fe->fttr_netdev)data.action.deep_queue=1;'):]
source = source.replace('P6_ACTION', selection.splitlines()[0])

def bits(data, bit, width, value):
    n = int.from_bytes(data, 'little')
    mask = ((1 << width) - 1) << bit
    data[:] = ((n & ~mask) | ((value << bit) & mask)).to_bytes(len(data), 'little')


cases = []
with tempfile.TemporaryDirectory(prefix='fttr-action-') as name:
    work = Path(name)
    (work / 'pack.c').write_text(source)
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                    '-I'+str(drivers), str(work / 'pack.c'), '-o', str(work / 'pack.so')], check=True)
    native = ctypes.CDLL(str(work / 'pack.so'))
    for protocol in (0x0806, 0x8888, 0x0800, 0x86dd, 0x8864):
        for count in (0, 1, 2):
            for destination in (0, 6):
                k, a = bytearray(32), bytearray(32)
                # Offsets confirmed in ca360: key+24 bit0 is ip_vld. The stock
                # header ABI differs from newer SDK structures and is not copied.
                k[12] = 6
                k[24] = int(protocol in (0x0800, 0x86dd, 0x8864))
                bits(a, 15, 1, 1)
                bits(a, 16, 2, count)
                bits(a, 32, 16, 0x8100)
                bits(a, 48, 12, 263)
                bits(a, 60, 1, 1)
                bits(a, 61, 3, 5)
                bits(a, 64, 16, 0x88a8)
                bits(a, 80, 12, 100)
                bits(a, 93, 3, 3)
                bits(a, 103, 1, 1)  # cos_update_en at action+8 bit39.
                bits(a, 104, 3, 5)
                bits(a, 107, 6, destination)
                fc.uc.mem_write(key, bytes(k))
                fc.uc.mem_write(action, bytes(a))
                assert fc.call('_rtk_8277c_asic_flow_action_gen', 0, key, action, result, 0, 0) == 0
                ni.uc.mem_write(ni.scratch, bytes(fc.uc.mem_read(result, 147)))
                ni.uc.mem_write(ni.scratch+0x1000, bytes(32))
                ni.call('convert_act_flow_nomal_mode', ni.scratch, ni.scratch+0x1000)
                actual = int.from_bytes(ni.uc.mem_read(ni.scratch+0x1000, 32), 'little')
                words, mask = (ctypes.c_uint*8)(), (ctypes.c_uint*8)()
                native.pack(protocol, count, destination, words, mask)
                # Native bridge forwarding retains its existing direct L2TM/no-interface
                # policy; compare packet edits, destination and preservation fields.
                expected, selected = int.from_bytes(words, 'little'), int.from_bytes(mask, 'little')
                assert (actual & selected) == (expected & selected), (hex(protocol), count, destination, hex(actual ^ expected))
                cases.append({'protocol': hex(protocol), 'output_tags': count, 'destination': destination,
                              'vlan_only': (actual >> 212) & 3})
report = {'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.fc, args.ni)},
          'cases': cases, 'all_selected_action_fields_match': True,
          'scope': 'actual stock FC action producer and NI converter; native action header; modeled TPID lookup; no physical PE execution'}
args.output.write_text(json.dumps(report, indent=2)+'\n')
print(f'{len(cases)} stock/native bridge actions match: non-IP VLAN-only, IP/PPPoE, 0..2 tags and P6/LAN outputs')
