#!/usr/bin/env python3
"""Execute production PE queue limits, expiry and BID/TIM ownership."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_PC

binary = (Path(sys.argv[1]) / 'pe-tx.bin').read_bytes()
# The driver-owned deadline uses the same policy as mac80211's skb queues.
kernel = root / 'sources/openwrt/build_dir/target-aarch64_cortex-a55_musl/linux-rtl9617c_generic/linux-6.18.55'
source = (kernel / 'net/mac80211/sta_info.c').read_text()
start = source.index('unsigned long ieee80211_sta_ps_buffer_timeout(')
end = source.index('\nEXPORT_SYMBOL_GPL(ieee80211_sta_ps_buffer_timeout);', start)
policy = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include "rx-geometry.h"
typedef uint32_t u32;
#define HZ 100
#define STA_TX_BUFFER_EXPIRE (10*HZ)
#define max_t(t,a,b) ((t)(a)>(t)(b)?(t)(a):(t)(b))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct ieee80211_sta {int unused;};
struct sdata {struct {struct {unsigned beacon_int;}bss_conf;}vif;};
struct sta_info {unsigned listen_interval;struct sdata *sdata;struct ieee80211_sta sta;};
struct rtl_ax_tx {bool peer_qos[128],peer_offloaded[128],peer_asleep[128],pe_active;u32 peer_rates[128];struct {void *memory;}pe;struct {u32 ps_seen[2][128];bool ps_issued[128];}ni;};
static void put_unaligned_le32(u32 value,void *p){memcpy(p,&value,4);}
static u32 get(const void *p){u32 v;memcpy(&v,p,4);return v;}
#define PE_PROBE_MAILBOX 0x2000
'''
tx = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()
arm = ''
for name in ('rtl_ax_tx_ps_clock', 'rtl_ax_tx_peer_qos'):
    at = tx.index('void ' + name + '(')
    arm += tx[at:tx.index('\n}\n', at) + 3]
with tempfile.TemporaryDirectory(prefix='wifi-ps-policy-') as directory:
    c, exe = Path(directory) / 'policy.c', Path(directory) / 'policy'
    c.write_text(policy + source[start:end] + arm + r'''
int main(void){struct sdata s={.vif={.bss_conf={100}}};struct sta_info sta={.sdata=&s,.listen_interval=1};
 assert(ieee80211_sta_ps_buffer_timeout(&sta.sta)==1000);
 sta.listen_interval=128;assert(ieee80211_sta_ps_buffer_timeout(&sta.sta)==2600);
 unsigned char memory[0x20000]={};struct rtl_ax_tx tx={.pe_active=true,.pe={memory}};
 rtl_ax_tx_peer_qos(&tx,1,true,ieee80211_sta_ps_buffer_timeout(&sta.sta)/HZ);
 rtl_ax_tx_ps_clock(&tx,100);
 assert(get(memory+0x19804)==26 && get(memory+0x230c)==100);
 sta.listen_interval=65535;assert(ieee80211_sta_ps_buffer_timeout(&sta.sta)==1342100);}
''')
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-I' + str(root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'),
                    str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
BASE, MB, NI, BAR, EPP, PAYLOAD, RING, PAGES = (
    0x04000000, 0x04002000, 0x10000000, 0x12000000,
    0x05000000, 0x06000000, 0x07100000, 0x07200000)


class Radio:
    def __init__(self, peers=1, inflight=0):
        self.u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        for address, size in [(0x1fc00000, 0x10000), (BASE, 0x20000), (NI, 0x10000),
                              (BAR, 0x10000), (EPP, 0x40000), (PAYLOAD, 0x600000),
                              (RING, 0x4000), (PAGES, 0xc000), (0x10020000, 0x1000)]:
            self.u.mem_map(address, size)
        self.u.mem_write(0x1fc00000, binary)
        for index, value in [(0, 0x50455744), (2, BAR | 0xa0000000), (3, 1),
                             (11, RING), (12, PAGES), (14, PAGES), (24, 1), (25, EPP),
                             (30, inflight), (114, 0x7060), (115, 0x7160), (116, 0x6380),
                             (117, 6), (140, MB | 0xa0000000), (142, 1), (167, 15), (195, 100)]:
            self.put(MB + index * 4, value)
        self.put(NI + 0x659c, 1 << 24)
        self.put(NI + 0x6340, 0x8000007c)
        self.put(NI + 0x6380, 0x80000000)
        self.put(BAR + 0x1058, 255 << 16)
        for peer in range(1, peers + 1):
            mac = bytes([2, 0, 0, 0, 0, peer])
            self.u.mem_write(BASE + 0xe400 + peer * 8, mac)
            self.put(BASE + 0x11a00 + peer * 4, 1)
            self.put(BASE + 0xe200 + peer * 4, (3839 << 18) | (1 << 17))
            self.put(BASE + 0xe800 + peer * 16, 1)
            self.put(BASE + 0x19800 + peer * 4, 10)
            self.u.mem_write(BASE + 0xd000 + peer * 32, struct.pack('<8I', 0, 0, 26 << 16,
                0x20100, (peer << 24) | (1 << 18) | (1 << 16), (3 << 16) | 64, 0, 0))
        # Real submitted WPs occupy their own slots; expiry must never return them.
        for index in range(inflight):
            self.put(BASE + 0xc400 + index * 8, PAYLOAD + 0x500000 + index * 2048)
            self.put(BASE + 0xc404 + index * 8, (1 << 15) | (1 << 8))
        if inflight:
            self.put(BASE + 0x18020, inflight)
        self.returns = []
        self.tx_consumers = []
        self.u.hook_add(UC_HOOK_MEM_WRITE,
            lambda uc, access, address, size, value, data: self.tx_consumers.append(self.get(NI + 0x7160)),
            begin=BAR + 0x1058, end=BAR + 0x1059)
        self.peer_peak = 0
        self.u.hook_add(UC_HOOK_MEM_WRITE, self.observe_peer,
                        begin=BASE + 0x11804, end=BASE + 0x11807)
        self.u.hook_add(UC_HOOK_MEM_WRITE,
            lambda uc, access, address, size, value, data: self.returns.append(value),
            begin=NI + 0x6384, end=NI + 0x6387)
        self.u.reg_write(UC_MIPS_REG_A0, MB | 0xa0000000)
        self.u.reg_write(UC_MIPS_REG_SP, (BASE + 0x1fff0) | 0xa0000000)
        self.pc = 0xbfc00400

    def observe_peer(self, uc, access, address, size, value, data):
        self.peer_peak = max(self.peer_peak, value)

    def put(self, address, value):
        self.u.mem_write(address, struct.pack('<I', value))

    def get(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def packets(self, peers):
        self.expected = set()
        for index, peer in enumerate(peers):
            address = PAYLOAD + index * 2048
            frame = bytes([2, 0, 0, 0, 0, peer]) + bytes.fromhex('0200000000090800') + bytes(1486)
            self.u.mem_write(address + 64, bytes(16) + frame)
            self.u.mem_write(EPP + index * 8, struct.pack('<II', address | 0x50, (1 << 14) | 1516))
            self.expected.add(address | 6)
        self.put(NI + 0x7060, len(peers) * 8)

    def run(self, count=150000):
        self.u.emu_start(self.pc, 0x9fc0fff0, count=count)
        self.pc = self.u.reg_read(UC_MIPS_REG_PC)

    def check_returns(self):
        assert len(set(self.returns)) == len(self.returns), 'BID returned twice'
        assert set(self.returns) <= self.expected, 'submitted WP or foreign BID returned'


# A single sleeping peer cannot keep the entire 2492-BID radio pool.
r = Radio(peers=2, inflight=124)
r.packets([1] * 2491 + [2])
r.put(BASE + 0xe820, 0)  # Peer2 remains awake.
r.run(3000000)
r.check_returns()
assert r.get(NI + 0x7160) == 2492 * 8
assert r.get(BASE + 0x11804) == 256
assert r.peer_peak <= 256
assert len(r.returns) == 2491 - 256
assert r.get(BASE + 0x11808) == 1  # Pending normal WP credit, not discarded.
assert r.get(MB + 30 * 4) == 124
assert r.get(BASE + 0x18020) == 256 + 124
# Expiry can reclaim unsent frames even when all 124 transmit credits are held.
r.put(MB + 195 * 4, 110)
r.run(1000000)
r.check_returns()
assert r.get(BASE + 0x11804) == 0 and len(r.returns) == 2491
assert r.get(BASE + 0x18020) == 124 and r.get(BASE + 0x14004) == 1
for index in range(124):
    assert r.get(BASE + 0xc400 + index * 8) == PAYLOAD + 0x500000 + index * 2048

# Multiple sleeping peers share a bounded total budget, with space for an awake peer.
total = Radio(peers=5)
total.packets([1, 2, 3, 4] * 600 + [5])
total.put(BASE + 0xe850, 0)
total.run(4000000)
total.check_returns()
assert total.get(NI + 0x7160) == 2401 * 8
assert sum(total.get(BASE + 0x11800 + peer * 4) for peer in range(1, 5)) <= 512
assert total.get(MB + 28 * 4) == 1  # Awake peer actually submitted.

# Excess arrivals must not delay an already queued awake peer until the flood stops.
stream = Radio(peers=2)
stream.packets([1] * 256 + [2] + [1] * 2000)
stream.put(BASE + 0xe820, 0)
stream.run(4000000)
stream.check_returns()
assert stream.peer_peak <= 256 and stream.get(MB + 28 * 4) == 1
assert stream.get(BASE + 0x11804) == 256 and len(stream.returns) == 2000
assert stream.tx_consumers and stream.tx_consumers[0] < 2257 * 8

# A blocked EQ push retains the discard owner and retries without releasing an in-flight WP.
credit = Radio()
credit.put(NI + 0x6380, 0)
credit.packets([1] * 300)
credit.run()
assert not credit.returns and credit.get(MB + 47 * 4)
held = credit.get(MB + 47 * 4) - 1
assert credit.get(BASE + 0xc400 + held * 8)
credit.put(NI + 0x6380, 0x80000000)
credit.run()
credit.check_returns()
assert len(credit.returns) == 44 and credit.get(BASE + 0x11804) == 256
assert credit.get(MB + 47 * 4) == 0 and credit.get(MB + 30 * 4) == 0

# Queued expiry keeps TIM set for a granted, submitted frame until its RPQ.
age = Radio()
age.put(BASE + 0x19804, 26)
age.packets([1] * 3)
age.run()
assert age.get(BASE + 0x11804) == 3 and not age.returns
age.put(MB + 195 * 4, 125)
age.run()
assert not age.returns
age.put(BASE + 0x19e04, 2)
age.put(BASE + 0xe814, 1)
age.put(BASE + 0xe81c, 1 << 16)
age.run()
assert age.get(MB + 28 * 4) == 1 and age.get(BASE + 0x11804) == 2
age.put(MB + 195 * 4, 126)
age.run()
age.check_returns()
assert len(age.returns) == 2 and age.get(BASE + 0x18020) == 1
assert age.get(BASE + 0x14004) == 1 and age.get(MB + 30 * 4) == 1

# A grant can race expiry of its last eligible packet, or arrive after that
# packet has already left the queue. Both must finish the same grant.
for late, generation in [(False, 1), (True, 1), (False, 0), (True, 0)]:
    empty = Radio()
    empty.packets([1])
    empty.run()
    if not late:
        empty.put(BASE + 0x19e04, 2)
        empty.put(BASE + 0xe814, generation)
        empty.put(BASE + 0xe81c, 1 << 16)
    empty.put(MB + 195 * 4, 110)
    empty.run()
    if late:
        empty.put(BASE + 0x19e04, 2)
        empty.put(BASE + 0xe814, generation)
        empty.put(BASE + 0xe81c, 1 << 16)
        empty.run()
    assert empty.get(BASE + 0x11804) == 0 and len(empty.returns) == 1
    assert empty.get(BASE + 0xe818) == 2 and empty.get(BASE + 0x19a08) == generation and empty.get(BASE + 0x19a0c) == 1, 'empty grant never completed'
# A partially published or changed command cannot be mistaken for a grant.
publication = Radio()
publication.packets([1])
publication.put(BASE + 0xe814, 5)
publication.put(BASE + 0xe81c, 1 << 16)
publication.put(BASE + 0x19e04, 1)
publication.run()
assert publication.get(MB + 28 * 4) == 0 and publication.get(BASE + 0xe818) == 0
publication.put(BASE + 0x19e04, 2)
publication.run()
assert publication.get(MB + 28 * 4) == 1 and publication.get(BASE + 0x1b800) == 5

from unicorn import UC_HOOK_MEM_READ
changed = Radio()
changed.packets([1])
changed.run()
changed.put(BASE + 0xe814, 1)
changed.put(BASE + 0xe81c, 1 << 16)
version_reads = []
def publish_during_snapshot(uc, access, address, size, value, data):
    version_reads.append(address)
    if len(version_reads) == 2:
        changed.put(BASE + 0xe814, 2)
        changed.put(BASE + 0xe81c, 2 << 16)  # Grant TID1; only TID0 is queued.
        changed.put(BASE + 0x19e04, 2)
changed.u.hook_add(UC_HOOK_MEM_READ, publish_during_snapshot,
                  begin=BASE + 0x19e04, end=BASE + 0x19e07)
changed.run()
assert changed.get(MB + 28 * 4) == 0 and changed.get(BASE + 0x11804) == 1
assert changed.get(BASE + 0x19a08) == 2 and changed.get(BASE + 0x19a0c) == 1

# Epoch zero is a valid MAC cookie, distinct from an unissued command.
for wrapped in (False, True):
    zero = Radio()
    zero.packets([1]); zero.run()
    zero.put(BASE + 0xe814, 0);zero.put(BASE + 0xe81c, 1 << 16)
    zero.put(BASE + 0x19e04, 0 if wrapped else 2)
    zero.put(BASE + 0xe818, 0xfffffffe if wrapped else 0)
    zero.run()
    assert zero.get(MB + 28 * 4) == 1 and zero.get(BASE + 0x1b800) == 0

# Actual RPQ decoding captures the grant before recycling its WP. New then
# old completions cannot replace the current service period's notification.
rpq = Radio()
rpq.packets([1, 1])
rpq.run()
rpq.put(BASE + 0x19e04, 2); rpq.put(BASE + 0xe814, 1); rpq.put(BASE + 0xe81c, 1 << 16)
rpq.run()
rpq.put(BASE + 0x19e04, 4); rpq.put(BASE + 0xe814, 2); rpq.run()
assert rpq.get(BASE + 0x1b800) == 1 and rpq.get(BASE + 0x1b804) == 2
reports, forwarded = 0x07800000, 0x07900000
rpq.u.mem_map(reports, 0x40000);rpq.u.mem_map(forwarded, 0x40000)
for index, value in [(68, reports), (72, forwarded), (187, BASE)]:rpq.put(MB + index * 4, value)
rpq.put(BAR + 0x121c, 1 << 16)
payload = struct.pack('<II', 1 | 257 << 16, 1 | 256 << 16)
rpq.u.mem_write(reports, struct.pack('<II', 28 | 0xc000 | 1 << 16, 7 << 24 | 8) + bytes(12) + payload)
rpq.run()
assert rpq.get(BASE + 0x1ba08) == 2 and rpq.get(BASE + 0x1ba0c) == 1
assert len(rpq.returns) == 2 and rpq.get(MB + 30 * 4) == 0

print(json.dumps({'scope': 'Linked production PE instructions; synthetic EPP, EQ credit and RPQ ownership.',
                  'per_peer_bound': 256, 'total_waiting_bound': 512,
                  'sleep_pool_reclaimed': True, 'awake_peer_submitted': True,
                  'expiry_preserves_submitted_WPs_and_TIM': True,
                  'blocked_EQ_push_retries_exactly_once': True}, indent=2))
