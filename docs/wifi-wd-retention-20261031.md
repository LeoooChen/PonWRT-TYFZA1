# TX WD lifetime correction

## Observed incident

On 0.8.3-beta, RTL8852C reported `firmware halt=00001000`,
`DMAC_error=00000100`, `host_dispatcher=00000040` at boot time
3029.147213 seconds (approximately 2026-10-31 01:35:38 Asia/Shanghai).
H2C timeouts and failed beacon updates followed. Linux boot ID and uptime
remained unchanged: this incident was a radio stall, not a machine reboot.

The first archive is retained privately on the host and router; SHA256:
`5c831c5a7e81ab0b2b68026419a376009dd458d3aff0679c7d199202980e6b54`.
Its logs, MACs, network information and subsequent physical RAM dumps are
not included in the public source tree.

## Factory evidence

The board's `rtk_wifi6.ko` SHA256 is
`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`.

- `hal_hci_cfg_8852ce@0x27b1a0` and `hal_hci_cfg_8192xbe@0x281c44`
  set `txbd_multi_tag=8` and `txbd_upd_lmt=1`.
- `rtw_release_busy_wd_page@0x1929b4` checks that flag at `0x192aa0`.
  The enabled path inserts a consumed WD into the channel's work ring at
  `0x192be4..0x192c08` and returns the displaced older page to idle at
  `0x192c54`, not the just-consumed page.
- This WD release path is separate from the payload's WP/RPQ completion.

`test_stock_wd_retention.py` executes both configuration functions and 64
calls of the actual release function. Station lookup, idle enqueue and BH
accounting are explicit callbacks. It verifies the eight-page retention
and four work-ring wraps for each chip; it does not model PCIe latency.

## Correction

The old builder used `WP_ID * 128` for the WD address. RPQ completion made
that WP ID available for another submission, potentially on a different
channel, and the next submission overwrote the old WD. WP payload lifetime
is not sufficient to satisfy the factory's independent WD retention rule.

WD pages now have six independent, monotonically rotating pools, each
containing 264 pages. A 256-entry TXBD ring admits at most 255 outstanding
descriptors; adding eight consumed-but-retained WDs yields 263 protected
pages. The 264th is the next writable page. Existing TXBD credit checks
prevent overrunning this bound. No timing heuristic or delayed free is used.

The PE maintains its own per-channel WD cursors and records the last WD page
separately for diagnostics. The non-PE host path uses the same geometry.
Neither an RPQ completion nor a TXBD index wrap resets these cursors.
Only fresh allocation initializes them. WP IDs, headers, payload ownership,
RPQ/EQ recycling, queue credit, publication barriers and packet formats are
unchanged. The generated PE header and host allocation/free sizes change
together; do not hot-swap just the new PE into an old 384-page allocation.

Logical WD storage increases from 49,152 to 202,752 bytes per radio
(an additional 150 KiB); coherent allocation rounding may reserve more.
This avoids per-packet allocations, extra payload copies and new MMIO.

## Verification

Artifacts are under `artifacts/wifi-wd-lifetime-20261031/`.

- The old linked PE reproduces retained-WD overwrite when a completed WP ID
  is reused across channels. The corrected builder executes 4,800 mixed
  ARM/NI submissions while checking the previous 263 pages of each channel.
  Six channels, repeated WP IDs, TXBD wraps and WD pool wraps all pass.
  The same retention test passes for the non-PE_UPLINK debug build.
- Production PE: 23,528 bytes, within the 32 KiB instruction window.
  Conservative sum of static stack frames: 6,688 bytes, within 8 KiB DMEM.
- Full ARM64/musl standalone radio module builds. Source header, tested PE
  and module-embedded PE are byte-identical. The existing standalone
  `MODULE_DESCRIPTION` modpost warning remains.
- Production regressions pass: TX publication/credit/dual-radio isolation,
  RXQ backpressure, RPQ release, EPP batching, downlink gather (including
  160 MHz and large HE aggregates), shared sequence, staged host headers,
  PS budget/isolation, NI descriptor credit, peer lifetime/waiters,
  physical reset, RX handoff, IRQ retry, instruction memory and scatter DMA.
- Three older debug fixtures already fail on unchanged HEAD:
  `test_pe_ethernet_firmware` (unmapped read), `test_pe_wd_firmware`
  (outdated host compilation fixture), and `test_pe_ni_radio_firmware`
  (context/queue assertion). They were not refactored as part of this fix.

PE SHA256:
`d7e4d1fbba282d840243d3f086ee61e3b63b5665ac734a38ade473d9b6ff48af`.

```sh
PE_RADIO_CFLAGS=-DPE_UPLINK sh scripts/build_pe_radio_wd.sh artifacts/wd-test
.venv/bin/python3 scripts/test_stock_wd_retention.py
.venv/bin/python3 scripts/test_pe_wd_retention.py artifacts/wd-test
.venv/bin/python3 scripts/test_pe_tx_publication.py artifacts/wd-test
.venv/bin/python3 scripts/test_pe_downlink_gather.py artifacts/wd-test
bash scripts/build_live_radio.sh "$PWD/artifacts/wd-module" rtl_ax_ap_wd_retention
```

## Scope and remaining validation

This fixes a demonstrated lifetime mismatch with the board's original
driver. It is a candidate fix for the observed dispatcher underflow, not
proof that the retained-page race caused that particular physical halt.
The captured pending descriptors were length-consistent when inspected;
that later snapshot cannot reconstruct what PCIe fetched before the halt.

No device reset, driver replacement, firmware flash or on-device long-run
test was performed for this change. A new kernel/module must be deployed
with the matching embedded PE, then tested through mixed traffic, sparse
traffic, peer sleep/wake and reconnect cycles for longer than the observed
failure interval. The independent SER L1 recovery gap is not addressed by
this patch, and errors are not masked or converted to apparent success.
