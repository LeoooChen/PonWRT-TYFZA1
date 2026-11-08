#!/usr/bin/env python3
"""Associated AP beacons reach link maintenance even without a PHY RSSI sample."""
from pathlib import Path
import re
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'monitor.h').read_text()
def function(name):
    start = re.search(r'^static .*?\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end, depth = source.index('{', start) + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]
common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef u16 __le16;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define READ_ONCE(v) (v)
#define BIT(n) (1U << (n))
#define FCS_LEN 4
#define RTL_AX_8852C 2
#define NL80211_IFTYPE_MESH_POINT 7
struct ieee80211_vif {int type;};
#define RTL_AX_8192XB 3
enum { RATE_INFO_BW_20, RATE_INFO_BW_40, RATE_INFO_BW_80, RATE_INFO_BW_160 };
enum { NL80211_RATE_INFO_HE_GI_0_8, NL80211_RATE_INFO_HE_GI_1_6, NL80211_RATE_INFO_HE_GI_3_2 };
enum { NL80211_BAND_2GHZ, NL80211_BAND_5GHZ };
enum { RX_ENC_LEGACY, RX_ENC_HT, RX_ENC_VHT, RX_ENC_HE };
enum { RX_FLAG_NO_SIGNAL_VAL=1, RX_FLAG_ONLY_MONITOR=2, RX_FLAG_DECRYPTED=4,
 RX_FLAG_FAILED_FCS_CRC=8, RX_ENC_FLAG_SHORT_GI=16 };
struct ieee80211_rx_status {
 u32 flag; unsigned band, freq, bw, encoding, rate_idx, enc_flags, nss, he_gi;
 u64 boottime_ns; int signal;
};
struct sk_buff { struct ieee80211_rx_status status; unsigned len; u8 data[256]; };
struct rtl_ax_rx_packet {
 const u8 *descriptor, *payload; unsigned bytes; struct sk_buff *skb;
 bool icv_error, crc_error;
};
struct rtl_ax_ap {
 struct sk_buff *scan_pending; u32 scan_word0, scan_word1;
 u64 scan_matched, scan_unmatched, scan_received_ns;
 u8 scan_last_bssid[6], scan_last_rpl;
 struct ieee80211_vif *ap_vif;void *sta_vif;bool sta_associated;u8 sta_bssid[6];bool ap_started,mesh_started; unsigned chip; struct { unsigned primary; } channel; void *hw;
 u64 rx_decrypted, mac80211_packets, rx_copy_bytes, rx_prepare_ns, rx_mac80211_ns;
};
static bool rtl_ax_datapath_profile;
static struct ieee80211_rx_status received;
static unsigned deliveries;
#define IEEE80211_SKB_RXCB(skb) (&(skb)->status)
static u32 rtl_ax_ap_read32(const u8 *p) { u32 v; memcpy(&v, p, sizeof(v)); return v; }
static __le16 read_frame_control(const void *p) { __le16 v; memcpy(&v, p, sizeof(v)); return v; }
#define get_unaligned(p) read_frame_control(p)
static bool ieee80211_is_beacon(__le16 fc) { return (fc & 0xfc) == 0x80; }
static bool ieee80211_is_probe_resp(__le16 fc) { return (fc & 0xfc) == 0x50; }
static u64 test_time = 123456;
static u64 ktime_get_ns(void) { return test_time; }
static u64 ktime_get_boottime_ns(void) { return 987654; }
static struct sk_buff *dev_alloc_skb(unsigned bytes) {
 assert(bytes <= 256); return calloc(1, sizeof(struct sk_buff));
}
static void skb_put_data(struct sk_buff *skb, const void *data, unsigned bytes) {
 memcpy(skb->data, data, bytes); skb->len = bytes;
}
static void dev_kfree_skb(struct sk_buff *skb) { free(skb); }
static unsigned ieee80211_channel_to_frequency(unsigned channel, unsigned band) {
 return (band == NL80211_BAND_5GHZ ? 5000 : 2407) + channel * 5;
}
static void ieee80211_rx_ni(void *hw, struct sk_buff *skb) {
 (void)hw; received = skb->status; deliveries++; free(skb);
}
static void rtl_ax_ps_poll(struct rtl_ax_ap *radio, const struct rtl_ax_rx_packet *packet) {
 (void)radio; (void)packet;
}

#define ether_addr_equal(a,b) (!memcmp(a,b,6))
'''
common += '#define get_unaligned_le32 rtl_ax_ap_read32\n' + (driver / 'scan.h').read_text()
test = r'''
int main(void){
 u8 descriptor[32]={},frame[40]={};frame[0]=0x80;frame[16]=2;frame[21]=9;
 struct rtl_ax_rx_packet packet={.descriptor=descriptor,.payload=frame,.bytes=40};
 struct rtl_ax_ap radio={.sta_vif=&radio,.sta_associated=true};memcpy(radio.sta_bssid,frame+16,6);
 for(unsigned connected=0;connected<2;connected++)for(unsigned own=0;own<2;own++){
  radio.sta_associated=connected;radio.sta_bssid[5]=own?9:10;
  assert(!rtl_ax_monitor_receive(&radio,&packet));rtl_ax_scan_complete_frame(&radio,NULL);
  assert(received.flag&RX_FLAG_NO_SIGNAL_VAL);
  assert(!!(received.flag&RX_FLAG_ONLY_MONITOR)==!(connected&&own));
 }
 /* A delayed RX already observed the active mesh when removal cleared vif.
  * It may still deliver this last beacon, but must never dereference vif. */
 radio.ap_started=true;radio.mesh_started=true;radio.ap_vif=NULL;
 radio.sta_associated=false;
 assert(!rtl_ax_monitor_receive(&radio,&packet));rtl_ax_scan_complete_frame(&radio,NULL);
 assert(!(received.flag&RX_FLAG_ONLY_MONITOR));
 radio.mesh_started=false;
 assert(!rtl_ax_monitor_receive(&radio,&packet));rtl_ax_scan_complete_frame(&radio,NULL);
 assert(received.flag&RX_FLAG_ONLY_MONITOR);
 radio.ap_started=false;
 frame[0]=0xb0;assert(!rtl_ax_monitor_receive(&radio,&packet));assert(!(received.flag&RX_FLAG_ONLY_MONITOR));
 radio.sta_vif=NULL;assert(!rtl_ax_monitor_receive(&radio,&packet));assert(received.flag&RX_FLAG_ONLY_MONITOR);
 puts("STA RX: own beacon without RSSI reaches link monitor; unassociated/foreign samples stay unknown; authentication allowed");
}
'''
with tempfile.TemporaryDirectory(prefix='sta-beacon-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(common + function('rtl_ax_scan_complete_frame') + function('rtl_ax_monitor_receive') + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
