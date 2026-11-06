#!/usr/bin/env python3
"""Compare native replica encoding with the original FC/NI producer chain."""
import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from trace_stock_fttr_mcast_hw import replica_recipes

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fc', type=Path, required=True)
parser.add_argument('--ni', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
original = replica_recipes(args.fc, args.ni)
host_original = replica_recipes(args.fc, args.ni, (1, 6), True)
wifi_original = replica_recipes(args.fc, args.ni, (18,), True, wifi=(17, 18, 0))
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = r'''
#include <assert.h>
#include "rtl9617c-fe-mcast.h"
static unsigned int read_tpid(void *context,unsigned int reg) {
 unsigned int *tpid=context;
 assert(reg>=0x1704 && reg<=0x1710 && !(reg&3));
 return tpid[(reg-0x1704)/4];
}
int encode(unsigned int destination,unsigned int tags,unsigned int outer,unsigned int inner,
 unsigned int *tpid,unsigned int *words) {
 struct rtl_dq_io io={.context=tpid,.read=read_tpid};
 struct rtl_fe_mc_replica replica={.destination=destination,.tags=tags,
  .tag={{outer,4095|5<<13|1<<12},{inner,100|3<<13}}};
 return rtl_fe_mc_replica_encode(&io,&replica,words);
}
int encode_host(unsigned int group,unsigned int *words) {
 struct rtl_dq_io io={0};
 struct rtl_fe_mc_replica replica={.destination=16,.host_group=group};
 return rtl_fe_mc_replica_encode(&io,&replica,words);
}
int encode_direct(unsigned int cookie,unsigned int tags,unsigned int *tpid,unsigned int *words) {
 struct rtl_dq_io io={.context=tpid,.read=read_tpid};
 struct rtl_fe_mc_replica replica={.destination=16,.direct_cookie=cookie,.tags=tags,
  .tag={{0x8100,4095|5<<13|1<<12},{0x8100,100|3<<13}}};
 return rtl_fe_mc_replica_encode(&io,&replica,words);
}
'''
with tempfile.TemporaryDirectory(prefix='fttr-replica-') as name:
    work = Path(name)
    (work / 'replica.c').write_text(source)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-I'+str(drivers),
                    str(work / 'replica.c'), '-o', str(work / 'replica.so')], check=True)
    native = ctypes.CDLL(str(work / 'replica.so'))
    words, tpid = (ctypes.c_uint * 3)(), (ctypes.c_uint * 4)(*([0x8100] * 4))
    for case in original['cases']:
        assert native.encode(case['destination'], case['output_tags'], 0x8100, 0x8100, tpid, words) == 0
        assert list(words) == case['words'], (case, list(words))
    for case in host_original['cases']:
        stock = case['host_copy']['words']
        for group in range(1, 256):
            assert native.encode_host(group, words) == 0
            # Deliberate framework adaptations: CPU0 instead of CPU2,
            # a quarantined group ticket in POL and physical source retention.
            assert list(words) == [(stock[0] & ~(511 << 23)) | ((256 + group) << 23),
                                   (stock[1] & ~(63 << 24)) | (16 << 24), stock[2] | (1 << 11)]
    for group in (0, 256, 1024):
        before = list(words)
        assert native.encode_host(group, words) == -22 and list(words) == before
    for case in wifi_original['cases']:
        stock = case['words']
        for cookie in range(1, 256):
            assert native.encode_direct(cookie, case['output_tags'], tpid, words) == 0
            assert list(words) == [(stock[0] & ~(511 << 23)) | ((256 + cookie) << 23),
                                   (stock[1] & ~(63 << 24)) | (16 << 24), stock[2] | (1 << 11)]
    for cookie in (0, 256, 1024):
        before = list(words)
        assert native.encode_direct(cookie, 0, tpid, words) == -22 and list(words) == before
    # A separately programmed L3 TPID table cannot justify a missing L2 TPID.
    before = list(words)
    assert native.encode(6, 1, 0x88a8, 0, tpid, words) == -95
    assert list(words) == before
    # Exercise each allowed L2 selector, including slot0 unavailable to C-tag.
    for slot in range(4):
        tpid[:] = [0x8100] * 4
        tpid[slot] = 0x88a8
        assert native.encode(6, 1, 0x88a8, 0, tpid, words) == 0
        assert (words[1] >> 15) & 3 == slot
        result = native.encode(6, 2, 0x8100, 0x88a8, tpid, words)
        assert result == (0 if slot >= 2 else -95)
        if not result:
            assert (words[0] >> 15) & 1 == slot - 2
    for destination, tags, outer in ((7, 0, 0), (32, 0, 0), (6, 3, 0), (6, 1, 0x9100)):
        before = list(words)
        assert native.encode(destination, tags, outer, 0, tpid, words) == -95
        assert list(words) == before
original['host_copy'] = host_original
original['wifi_copy'] = wifi_original
original['host_adaptation'] = 'CPU16/POL256+group/keep_o_lspid; final VLAN/COS/source MAC/TTL commands retained'
args.output.write_text(json.dumps(original, indent=2)+'\n')
print('Native replica words match6 FC/NI recipes; 1530 host and765 wireless-copy adaptations, L2 TPID selectors and unsupported outputs checked')
