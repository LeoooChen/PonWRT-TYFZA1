#!/usr/bin/env python3
"""Exercise actual iwinfo scan requests and native RX delivery without hardware."""
from pathlib import Path
import resource
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
archives = list((root / 'sources/openwrt/dl').glob('libiwinfo-*.tar.zst'))
assert len(archives) == 1, 'Cache the selected iwinfo source archive first'
monitor = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/monitor.h'


def function(source, name):
    at = re.search(r'^static (?:inline )?\w+ ' + name + r'\(', source, re.M).start()
    start = source.rfind('\n', 0, at) + 1
    end = source.index('{', at) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


iwinfo_model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <linux/nl80211.h>
#define NLM_F_DUMP 0x300
#define IWINFO_OPMODE_MASTER 3
#define IWINFO_OPMODE_CLIENT 4
struct iwinfo_scanlist_entry { int id; };
struct nl80211_scanlist { struct iwinfo_scanlist_entry *e; int len; };
struct nl_msg { bool has_scan_flags; uint32_t scan_flags; };
struct nl80211_msg_conveyor { struct nl_msg *msg; };
static struct nl_msg message;
static struct nl80211_msg_conveyor conveyor = { .msg = &message };
static int mode, mode_error, alloc_error, attr_error, send_error, wait_error, dump_error;
static unsigned live, triggers, waits, dumps, frees, netlink_flags;
static int nl80211_get_mode(const char *ifname, int *value) {
 (void)ifname;
 /* iwinfo reuses one static conveyor; query mode before building the scan. */
 assert(!live);
 *value = mode;
 return mode_error;
}
static struct nl80211_msg_conveyor *nl80211_msg(const char *ifname, int cmd, int flags) {
 (void)ifname; assert(cmd == NL80211_CMD_TRIGGER_SCAN && !live);
 netlink_flags = flags;
 if (alloc_error) return NULL;
 memset(&message, 0, sizeof(message)); live = 1; return &conveyor;
}
static int nla_put_u32(struct nl_msg *msg, int type, uint32_t value) {
 assert(live && msg == &message && type == NL80211_ATTR_SCAN_FLAGS);
 if (attr_error) return -ENOBUFS;
 msg->has_scan_flags = true; msg->scan_flags = value; return 0;
}
#define NLA_PUT_U32(msg,type,value) do { \
 if (nla_put_u32(msg,type,value)) goto nla_put_failure; \
} while (0)
static void nl80211_free(struct nl80211_msg_conveyor *cv) {
 assert(cv == &conveyor && live); live = 0; frees++;
}
static int nl80211_send(struct nl80211_msg_conveyor *cv, void *callback, void *data) {
 (void)callback; (void)data; assert(live); triggers++;
 int result = send_error;
 if (!result && mode == IWINFO_OPMODE_MASTER &&
     !(message.scan_flags & NL80211_SCAN_FLAG_AP)) result = -EOPNOTSUPP;
 nl80211_free(cv); return result;
}
static int nl80211_get_scanlist_cb(struct nl_msg *msg, void *data) {
 (void)msg; (void)data; return 0;
}
static int nl80211_request(const char *ifname, int cmd, int flags,
 int (*callback)(struct nl_msg *, void *), void *data) {
 if (cmd == NL80211_CMD_TRIGGER_SCAN)
  return nl80211_send(nl80211_msg(ifname, cmd, flags), NULL, NULL);
 assert(cmd == NL80211_CMD_GET_SCAN && flags == NLM_F_DUMP && !live);
 assert(callback == nl80211_get_scanlist_cb); dumps++;
 if (dump_error) return dump_error;
 ((struct nl80211_scanlist *)data)->len = 2; return 0;
}
static int scan_wait(const char *family, const char *group, int done, int aborted) {
 assert(!strcmp(family, "nl80211") && !strcmp(group, "scan"));
 assert(done == NL80211_CMD_NEW_SCAN_RESULTS && aborted == NL80211_CMD_SCAN_ABORTED);
 assert(!live); waits++; return wait_error;
}
#define nl80211_wait(...) scan_wait(__VA_ARGS__)
'''
iwinfo_checks = r'''
static void reset(int type) {
 assert(!live); mode = type;
 mode_error = alloc_error = attr_error = send_error = wait_error = dump_error = 0;
 triggers = waits = dumps = frees = netlink_flags = 0;
 memset(&message, 0, sizeof(message));
}
int main(void) {
 char results[64]; int length;
 reset(IWINFO_OPMODE_MASTER);
 assert(nl80211_get_scanlist_nl("phy0-ap0", results, &length) == 0);
 assert(length == 2 * sizeof(struct iwinfo_scanlist_entry));
 assert(message.has_scan_flags && message.scan_flags == NL80211_SCAN_FLAG_AP);
 assert(!netlink_flags && triggers == 1 && waits == 1 && dumps == 1 && frees == 1);
 reset(IWINFO_OPMODE_CLIENT);
 assert(!nl80211_get_scanlist_nl("wlan0", results, &length));
 assert(!message.has_scan_flags && !message.scan_flags && !netlink_flags);
 reset(IWINFO_OPMODE_CLIENT); mode_error = -ENODEV;
 assert(!nl80211_get_scanlist_nl("wlan0", results, &length));
 assert(!message.has_scan_flags);
 reset(IWINFO_OPMODE_MASTER); alloc_error = 1; length = 99;
 assert(nl80211_get_scanlist_nl("phy0-ap0", results, &length) == -1);
 assert(!length && !live && !triggers && !waits && !dumps);
 reset(IWINFO_OPMODE_MASTER); attr_error = 1; length = 99;
 assert(nl80211_get_scanlist_nl("phy0-ap0", results, &length) == -1);
 assert(!length && !live && frees == 1 && !triggers && !waits && !dumps);
 reset(IWINFO_OPMODE_MASTER); send_error = -EBUSY; length = 99;
 assert(nl80211_get_scanlist_nl("phy0-ap0", results, &length) == -1);
 assert(!length && !live && frees == 1 && !waits && !dumps);
 reset(IWINFO_OPMODE_MASTER); wait_error = -ETIMEDOUT; length = 99;
 assert(nl80211_get_scanlist_nl("phy0-ap0", results, &length) == -1);
 assert(!length && !live && waits == 1 && !dumps);
 reset(IWINFO_OPMODE_MASTER); dump_error = -EIO; length = 99;
 assert(nl80211_get_scanlist_nl("phy0-ap0", results, &length) == -1);
 assert(!length && !live && dumps == 1);
 puts("iwinfo: AP scan attribute, client behavior, static request ownership and error cleanup passed");
 return 0;
}
'''

rx_model = r'''
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
 bool ap_started; unsigned chip; struct { unsigned primary; } channel; void *hw;
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
'''
rx_model += '#define get_unaligned_le32 rtl_ax_ap_read32\n' + (root / monitor).with_name('scan.h').read_text()
rx_checks = r'''
int main(void) {
 u8 descriptor[32] = {}, frame[40] = {};
 const unsigned types[] = {0x80, 0x50, 0x08, 0x40, 0xb0, 0xd4};
 struct rtl_ax_ap radio = {};
 struct rtl_ax_rx_packet packet = {
  .descriptor=descriptor, .payload=frame, .bytes=sizeof(frame)
 };
 unsigned cases = 0;
 for (unsigned chip=2; chip<=3; chip++)
 for (unsigned ap=0; ap<2; ap++)
 for (unsigned dma=0; dma<2; dma++)
 for (unsigned crc=0; crc<2; crc++)
 for (unsigned i=0; i<ARRAY_SIZE(types); i++) {
  radio.chip=chip; radio.ap_started=ap; radio.channel.primary=chip==2 ? 36 : 1;
  u32 rate=(chip==2 ? 4U : 0U) << 16; memcpy(descriptor+4, &rate, sizeof(rate));
  frame[0]=types[i]; frame[1]=0; packet.crc_error=crc;
  packet.skb=dma ? dev_alloc_skb(sizeof(frame)) : NULL;
  if (packet.skb) skb_put_data(packet.skb, frame, sizeof(frame));
  unsigned before=deliveries;
  assert(!rtl_ax_monitor_receive(&radio, &packet));
  bool discovery=types[i]==0x80 || types[i]==0x50;
  if (discovery && !crc) {
   assert(deliveries==before && radio.scan_pending);
   rtl_ax_scan_complete_frame(&radio, NULL);
  }
  assert(deliveries==before+1);
  assert(!!(received.flag & RX_FLAG_ONLY_MONITOR)==((!ap && !discovery) || (discovery && !crc)));
  assert(!!(received.flag & RX_FLAG_FAILED_FCS_CRC)==crc);
  assert(received.flag & RX_FLAG_NO_SIGNAL_VAL);
  assert(received.band==(chip==2 ? NL80211_BAND_5GHZ : NL80211_BAND_2GHZ));
  assert(received.freq==(chip==2 ? 5180U : 2412U));
  assert(!!received.boottime_ns==(types[i]==0x80));
  cases++;
 }
 packet.skb=NULL; packet.crc_error=false;
 unsigned before=deliveries; packet.bytes=4;
 assert(rtl_ax_monitor_receive(&radio, &packet)==-EINVAL && deliveries==before);
 packet.bytes=sizeof(frame); u32 rate=12U << 16;
 memcpy(descriptor+4, &rate, sizeof(rate));
 assert(rtl_ax_monitor_receive(&radio, &packet)==-EOPNOTSUPP && deliveries==before);
 rate=0; memcpy(descriptor+4, &rate, sizeof(rate));
 u32 decrypted=BIT(2); memcpy(descriptor+12, &decrypted, sizeof(decrypted));
 packet.icv_error=true;
 assert(rtl_ax_monitor_receive(&radio, &packet)==-EBADMSG && deliveries==before);
 /* Unassociated beacon RSSI comes from its own following PPDU, not MACID. */
 u8 phy[48] = {}, report_wd[32] = {};
 struct rtl_ax_rx_packet report = {.descriptor=report_wd, .payload=phy, .bytes=sizeof(phy)};
 u32 mac_info=BIT(23); memcpy(report_wd, &mac_info, 4);
 phy[8]=0x8c; phy[9]=5; phy[16]=1; phy[17]=147;
 packet.icv_error=false; frame[0]=0x80; memset(descriptor+12, 0, 4);
 for (unsigned id=0; id<8; id++) {
  u32 w=4U<<16 | id<<4; memcpy(descriptor+4,&w,4); memcpy(report_wd+4,&w,4);
  assert(!rtl_ax_monitor_receive(&radio,&packet));
  assert(radio.scan_pending);
  rtl_ax_scan_complete_frame(&radio,&report);
  assert(received.signal==-37 && !(received.flag & RX_FLAG_NO_SIGNAL_VAL));
  assert(!(received.flag & RX_FLAG_ONLY_MONITOR) && !radio.scan_pending);
  for (unsigned bit=0; bit<32; bit++) {
   u32 wrong=w^(1U<<bit); memcpy(report_wd+4,&wrong,4);
   assert(!rtl_ax_monitor_receive(&radio,&packet));
   rtl_ax_scan_complete_frame(&radio,&report);
   assert(!!(received.flag & RX_FLAG_NO_SIGNAL_VAL)==!!((1U<<bit)&0xefff007f));
  }
  memcpy(report_wd+4,&w,4);
 }
 for (unsigned len=0; len<sizeof(phy); len++) {
  report.bytes=len;
  assert(!rtl_ax_monitor_receive(&radio,&packet));
  rtl_ax_scan_complete_frame(&radio,&report);
  assert(received.flag & RX_FLAG_NO_SIGNAL_VAL);
 }
 report.bytes=sizeof(phy); report.crc_error=true;
 assert(!rtl_ax_monitor_receive(&radio,&packet));
 rtl_ax_scan_complete_frame(&radio,&report);
 assert(received.flag & RX_FLAG_NO_SIGNAL_VAL);
 report.crc_error=false;
 assert(!rtl_ax_monitor_receive(&radio,&packet));
 test_time += 20000000;
 rtl_ax_scan_complete_frame(&radio,&report);
 assert(received.flag & RX_FLAG_NO_SIGNAL_VAL);
 puts("Scan RSSI: unassociated BSS, all PPDU IDs, rate/GI/BW mismatches, truncation and CRC rejection passed");
 printf("Native RX: %u AP/pre-AP, band, DMA/copy, management/data and CRC cases; short/rate/ICV rejection passed\n", cases);
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='tyfza1-scan-test-') as folder:
    folder = Path(folder)
    subprocess.run(['tar', '-xf', str(archives[0]), '-C', str(folder),
                    '--strip-components=1'], check=True)
    source = folder / 'iwinfo_nl80211.c'
    patches = {p.name: p for p in (root / 'sources/openwrt/package/network/utils/iwinfo/patches').glob('*.patch')}
    patches['131-iwinfo-ap-scan.patch'] = root / 'port/host-patches/131-iwinfo-ap-scan.patch'
    for name, patch in sorted(patches.items()):
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i',
                        str(patch)],
                       cwd=folder, check=True, stdout=subprocess.PIPE, text=True)
    radio_source = (root / monitor).read_text()
    cases = {
        'iwinfo': iwinfo_model + function(source.read_text(), 'nl80211_get_scanlist_nl') + iwinfo_checks,
        'rx': rx_model + function(radio_source, 'rtl_ax_scan_complete_frame') + function(radio_source, 'rtl_ax_monitor_receive') + rx_checks,
    }
    failed = []
    for name, content in cases.items():
        test, binary = folder / (name + '.c'), folder / name
        test.write_text(content)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-function', '-fsanitize=address,undefined',
                        str(test), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        print(result.stdout, end='')
        if result.returncode:
            failed.append(name)
            print(result.stderr, end='')
    if failed:
        raise SystemExit('Scan regressions failed: ' + ', '.join(failed))
print('Offline only; no device access or shared OpenWrt build changes')
