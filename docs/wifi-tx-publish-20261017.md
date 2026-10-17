# PE TX publication and the reported 530 Mbps ceiling

## Status

Baseline: `eb77212`, 0.6.0-alpha. The user reports approximately 530 Mbps with
5 GHz/160 MHz, both on a local simulated network and another user's gigabit
WAN. This turn did not reproduce either measurement. The Mac's route to
192.168.1.1 goes through its hotspot; no modem access, Wi-Fi switch, register
operation, module replacement or flashing was attempted.

The change below is a confirmed removal of unnecessary uncached memory work.
**It is not a confirmed root cause or resolution of the entire 530 Mbps ceiling.**
No new Mbps result is available. Historical results must not be relabeled as
results of this candidate. In particular the old 166 Mbps RPQ-credit dependency
is already addressed and is not an explanation established for today's limit.

## Factory evidence

Board module `rtk_wifi6.ko`, SHA256:
`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`.

- Actual `_phl_txsc_apply_shortcut@0x195614` copies the prepared cached WP
  image to the request's destination. Executing its valid shortcut branch
  with WD length 56 and used WP length 94 copies precisely 94 bytes and
  propagates these lengths; only the memcpy callee is modeled.
- Actual `phl_prepare_tx_pcie@0x196ae4` prepares descriptors before submission;
  its cache synchronization path calls `pci_cache_wback@0x1971d0` (call site).
  `phl_tx_pcie@0x193134` and `hal_update_txbd_8852ce@0x27cdb4` are preserved
  in the same local disassembly. These are not evidence of an identical
  factory PE implementation or factory runtime cache settings.

The native rewrite follows the preparation/publication boundary, not the
factory private request ABI. It does not import a different chipset's driver,
reuse the vendor binary as a runtime compatibility layer, or add a new cache
coherency scheme for DMA buffers.

## Native change

`port/pe/rtl9617c-radio-wd.c` previously built both the WD/WP and a temporary
WLAN/A-MSDU header in uncached DDR. NI packets then read that temporary header
back and copied it into BID headroom, which is what the radio actually reads.
The WD was also cleared in DDR and overwritten field by field.

The new code constructs both in the PE's existing private stack. It copies
only the final WP image (rounded to a word) and, for ARM requests, the persistent
DMA header to their existing allocations before publishing TXBD and the
existing barrier/doorbell. NI headers still go into their original BID
headroom. No DMA descriptor points to stack memory. Payload bytes, WP owners,
PN/SN, completion, limits and initialization are unchanged.

The `radio_tx` diagnostic now prints `header=in-place-NI` for PE-owned IDs,
rather than showing an obsolete scratch header. ARM header reporting remains.

## Focused results

The counts are executed MIPS load/store instructions, not bus transactions,
cache misses, time estimates or throughput. LWL/LWR/SWL/SWR count individually.
They cover the first TX publication, with deterministic fixture IO.

| Input | Temporary-header loads/stores, before -> after | WD stores, before -> after | Total instructions, before -> after |
| --- | --- | --- | --- |
| 1 x 1514-byte MSDU | 50 -> 0 | 51 -> 18 | 1325 -> 1434 |
| 5 x 1514-byte MSDUs | 190 -> 0 | 67 -> 24 | 2894 -> 3042 |
| 8 x 64..67-byte MSDUs | 270 -> 0 | 79 -> 28 | 3970 -> 4147 |

The total instruction count increases. Savings in uncached accesses cannot be
converted to a throughput gain without hardware latency and cache behavior.

All three before/after cases publish byte-identical TXBD/WD/WP images and pass
exact header/payload/padding and RPQ/EQ ownership checks. Two existing 160 MHz
cases and seven existing ARM Ethernet/raw shared-sequence cases pass, including
scatter, sequence wrap and CCMP header modes. No full suite ran.

The ARM64/musl radio module compiles and its embedded 15944-byte PE image is
identical to the profiled candidate. MODPOST retains the existing missing
MODULE_DESCRIPTION warning. No full firmware build ran.

Candidate: `artifacts/wifi-throughput-20261017/radio/rtl_ax_ap_tx_publish.ko`.
Evidence and hashes: `manifests/wifi-tx-publish-20261017.json`.

## Remaining root-cause verification

A same-device comparison is required using the baseline and this candidate,
with the same wired source, wireless client, PHY mode and TCP/UDP direction.
Existing `scripts/observe_wifi_offload.sh` exposes the useful boundaries:
PHY/retries, PE sent/MPDU/recycled/pending, per-peer queues, RX work/NI progress,
ARM exception bytes, and NI tail drops. Its QM counters are read-clear, so it
must not run beside another counter reader. No new profiling subsystem was
added to the production driver.

A 530 Mbps result alone does not distinguish PE service capacity, radio airtime,
completion stalls, uplink ACK processing or client limitations. The next result
must correlate the actual plateau with those counters. This patch only changes
TX preparation; it does not claim to repair an unmeasured uplink bottleneck.
