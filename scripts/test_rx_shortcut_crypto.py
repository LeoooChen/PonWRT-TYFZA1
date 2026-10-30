#!/usr/bin/env python3
"""Run the production CCMP shortcut hook against protected and plaintext RX."""
import argparse
from pathlib import Path
import subprocess
import tempfile


root = Path(__file__).resolve().parents[1]
relative = 'port/overlay/target/linux/rtl9617c/patches-6.18/204-mac80211-rx-shortcut-state.patch'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision')
args = parser.parse_args()
patch = (subprocess.check_output(['git', 'show', f'{args.revision}:{relative}'],
                                cwd=root, text=True)
         if args.revision else (root / relative).read_text())
first_hunk = patch.split('@@')[2]
hook = '\n'.join(line[1:] for line in first_hunk.splitlines() if line.startswith('+'))

common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint16_t u16;
#define BIT(n) (1U << (n))
#define cpu_to_le16(x) (x)
#define le16_to_cpu(x) (x)
#define IEEE80211_SCTL_FRAG 15
#define WLAN_CIPHER_SUITE_CCMP 0x000fac04
struct ieee80211_hdr {u16 frame_control, seq_ctrl;};
struct sk_buff {void *data;};
struct ieee80211_key {
    struct {unsigned cipher;} conf;
    u16 rx_shortcut_next_seq[16], rx_shortcut_valid_tids;
    unsigned char pn[16][6];
};
struct ieee80211_rx_data {
    void *sta;struct ieee80211_key *key;struct sk_buff *skb;unsigned security_idx;
};
static bool ieee80211_is_data_qos(u16 fc){return (fc & 0x8c) == 0x88;}
static bool ieee80211_has_morefrags(u16 fc){return fc & 0x400;}
static inline bool ieee80211_has_protected(u16 fc){return fc & 0x4000;}
static void accepted(struct ieee80211_rx_data *rx) {
'''
case = r'''
}
int main(void) {
    struct ieee80211_key key={.conf={WLAN_CIPHER_SUITE_CCMP}};
    struct ieee80211_hdr hdr={0};struct sk_buff skb={&hdr};
    struct ieee80211_rx_data rx={&key,&key,&skb,3};
    key.pn[3][5]=9;
    /* rx_h_decrypt selects the PTK but returns RX_CONTINUE for plaintext.
     * It has not accepted a new CCMP PN, so neither SN nor valid may change. */
    hdr.frame_control=0x0188;hdr.seq_ctrl=100 << 4;
    accepted(&rx);
    assert(!key.rx_shortcut_valid_tids && !key.rx_shortcut_next_seq[3]);
    for(unsigned tid=0;tid<16;tid++) {
        rx.security_idx=tid;
        for(unsigned seq=0;seq<4096;seq++) {
            hdr.frame_control=0x4188;hdr.seq_ctrl=seq << 4;
            accepted(&rx);
            assert(key.rx_shortcut_next_seq[tid] == ((seq+1)&4095));
            assert(key.rx_shortcut_valid_tids & BIT(tid));
        }
    }
    struct ieee80211_key saved=key;
    rx.security_idx=3;
    for(unsigned fc_index=0;fc_index<4;fc_index++) {
        const u16 fc[]={0x0188,0x4588,0x4188,0x4180};
        hdr.frame_control=fc[fc_index];
        hdr.seq_ctrl=(200 << 4) | (fc_index==2 ? 1 : 0);
        accepted(&rx);
        assert(!memcmp(&key,&saved,sizeof(key)));
    }
    hdr.frame_control=0x4188;hdr.seq_ctrl=100 << 4;
    key.conf.cipher=0;saved=key;accepted(&rx);
    assert(!memcmp(&key,&saved,sizeof(key)));
    key.conf.cipher=WLAN_CIPHER_SUITE_CCMP;saved=key;rx.sta=NULL;accepted(&rx);
    assert(!memcmp(&key,&saved,sizeof(key)));
    rx.sta=&key;rx.key=NULL;accepted(&rx);
    assert(!memcmp(&key,&saved,sizeof(key)));
    puts("CCMP shortcut: plaintext preserves SN/PN, protected SN wrap on all TIDs, fragments and other ciphers passed");
}
'''
with tempfile.TemporaryDirectory(prefix='rx-shortcut-crypto-') as temporary:
    temporary = Path(temporary)
    source = temporary / 'case.c'
    source.write_text(common + hook + case)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(source), '-o', str(temporary / 'case')],
                   check=True)
    subprocess.run([str(temporary / 'case')], check=True, timeout=10)
