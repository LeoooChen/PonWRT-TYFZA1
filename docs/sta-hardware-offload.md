# STA hardware offload implementation and verification

Status: candidate implementation integrated with main `880bd4a` and its mesh
changes on 2026-11-06. Offline tests and standalone ARM64 builds
pass; RF association, reconnect and throughput remain untested on the board.
The original candidate `d116434` is retained at
`archive/sta-hardware-offload-before-main-20261105`.

## Supported topology

One AP, managed STA or mesh point per physical radio. The other radio can provide the
downstream AP for a routed repeater. The STA path uses three-address frames;
same-radio AP+STA, four-address WDS, TDLS and station power saving remain
unsupported. Mesh keeps its mac80211 forwarding and software encryption;
STA/AP NI/FE offload is excluded from mesh. Legacy 802.11a/b/g peers now use the
existing factory-matched legacy rate command. Their lack of TX BA keeps them
out of NI offload; ordinary frames retain the normal mac80211/driver path.

CCMP128 uses the existing hardware CAM, DMAC packet number, firmware rate
control, A-MPDU and A-MSDU paths. PE receives only admitted, successfully
decrypted unicast TCP/UDP frames with valid PN and sequence state. Protocol
exceptions and unsupported ciphers remain in the existing mac80211 path.
Automatic NI admission retains the existing authorized-peer, hardware-key and
TID0 TX-BA prerequisites. Open or non-CCMP networks do not acquire NI offload.

## Changes

- The factory STA role uses MACID1 and address/BSSID CAM0. MACID0 keeps its
  existing AP/group/discard meaning. Port0 enables BSSID matching and TSF
  synchronization without beacon transmission.
- PE request bit23 identifies a three-address client. Ethernet TX uses ToDS,
  RA=BSSID, TA=self and address3=Ethernet DA; A-MSDU address3 is the BSSID.
  The BSSID table is no longer overwritten with Ethernet destinations.
- PE RX admits FromDS for a client and ToDS for an AP. Client decapsulation
  recovers DA from address1 and SA from address3. Client A-MSDUs validate the
  destination against self while allowing source hosts behind the AP.
- The netdev notifier registers managed interfaces with NI. FE routes all
  destinations on that STA interface to its sole admitted AP; AP interfaces
  continue matching an exact peer address. Route withdrawal retains the
  existing hash/cache transaction ordering and descriptor ownership rules.
- Disconnect/rekey paths revoke admission before fallible cleanup. A failed
  role owns its MACID until retry finishes. Late GTK deletion cannot unlink
  a CAM reused by a newer key. Connected beacons without RSSI reports still
  reach mac80211 link maintenance with NO_SIGNAL_VAL.
- STA software scans withdraw NI before RF channel changes. Failed withdrawal
  blocks the channel change; completion retries retirement before admission.

## Alignment fixes and current verification

- Retain main's DMA ownership, asynchronous retirement, FE cookie index,
  TX128/RX256 BA limits, RTS fields and 4ms short-frame batching.
- Generate the STA NI template from mac80211 raw data frames, including
  ToDS direction, the client flag and BSSID. Main no longer enables host
  Ethernet encapsulation, so the old STA-only encapsulation path could not
  establish NI admission.
- Preserve the STA role in capability-update JOIN commands. Association and
  BSSID/beacon updates retain negotiated bandwidth, HE padding and the HE CAM
  bit. Native AP/STA JOIN and CAM output matches the actual original module
  across 1,600 cases; AP self-role bandwidth matches 16 cases.
- Enable LuCI Client and Join Network. The join form requires replacing this
  radio's configuration; WDS and ad-hoc remain unavailable for this
  driver. The complete patch series applies twice and JS/ucode checks pass.

Current results are in `artifacts/sta-main-20261105/`. The STA suite and all
regression commands in `.github/workflows/pr-checks.yml` pass. Coverage includes
4,224 AP/STA TX request vectors, PE STA/AP RX/TX, PN/SN, A-MSDU, wrong-direction
rejection, route withdrawal/retry, key reuse, BA teardown and RPQ ownership.
The wireless module and FE/Wi-Fi objects compile against the private prepared
ARM64 kernel tree; this check does not build a complete firmware image.

## Self-review on 2026-11-05

The initial alignment missed a mac80211 registration constraint: the original
STA candidate mixed `bss_info_changed` with `vif_cfg_changed` without
`link_info_changed`. `ieee80211_alloc_hw_nm()` rejects that combination before
allocating a wiphy. Compiling the module and testing isolated callbacks did
not cover registration. The driver now uses only the existing legacy callback
and dispatches STA configuration changes from it. The lifecycle test checks
the real ops table and executes the registered BSS callback.

The native STA port also used beacon hold time1 where the executed original
C/XB `mac_port_init()` writes400. It now writes400. BSSID, beacon interval and
first-beacon DTIM changes share association-update failure handling; a failed
update revokes NI admission and notifies mac80211 of connection loss.

The STA suite and ARM64 module rebuild pass after these changes. The PE binary
is unchanged. Fresh original STA port traces and review logs are in
`artifacts/sta-self-review-20261105/`. These results establish the checked
contracts and offline behavior, not complete factory feature parity or a
measured on-board accelerated connection.

## Continued review on 2026-11-05

Three more issues were reproduced and corrected:

- AP-to-STA setup cleared `role_stage` only after CAM invalidation. A successful
  firmware AP role removal followed by a failed CAM command left a nonexistent
  AP marked ready. Clear the stage at the successful role-removal boundary;
  a retry does not repeat deletion, and a later AP start must recreate it.
- mac80211 attempts the operating channel before `sw_scan_complete()` and
  ignores that transaction's error. A failed restore could still schedule
  STA offload. The callback now retries a failed channel restore once before
  admission and reports connection loss if restoration still fails.
- `rtl_ax_ra_peer()` rejected legacy-only APs despite an existing legacy
  encoder. It now selects that encoder, BW20/NSS1 and no aggregation capability.
  Sixteen a/b/g commands match the actual factory RA registration; eight
  initial/update selector cases and the empty-rate rejection pass, alongside
  the existing 128 HT and 48 VHT vectors.

Before/after failure evidence and passing results are under
`artifacts/sta-review2-20261105/`. The complete STA suite, channel retry, stop,
BA admission and ARM64 module rebuild pass. The PE binary is unchanged, with
CCMP, PN/SN, A-MSDU and RPQ ownership regressions passing. No board changes or
commits were made.

## Main integration on 2026-11-06

The STA change is integrated on top of the three mesh commits through
`880bd4a`. Interface creation, removal, beacon delivery, HE capabilities and
JOIN encoding retain AP, STA and mesh behavior. STA forwards through NI/FE;
mesh keeps the explicit TC/forward-path exclusion. A switch from mesh to STA
removes firmware role6 rather than using the AP role2 deletion command.
LuCI exposes both Client and mesh while enforcing one active role per radio.

Validation in `artifacts/sta-merge-20261106/` covers the STA suite, AP/STA/mesh
raw headers, 2,400 factory peer JOIN/CAM cases, 32 AP/mesh self JOIN cases,
mesh lifecycle, TSF and HWMP metrics, 160MHz, the PR regression commands and
the standalone ARM64 wireless module build. The RPQ test fixture now records
the new mesh success updates so the existing batch-order test remains valid.

## Reproduce offline checks

The suite needs a host C compiler, clang/LLVM18 and Python Unicorn. It writes
only its output directory and temporary test fixtures:

```sh
sh scripts/test_sta_hardware_offload.sh "$PWD/artifacts/sta-offload-check"
```

The tests execute compiled production C and the linked MIPS PE image. They
cover frame bytes, crypto descriptors, A-MSDU padding, PN/SN, duplicate and
invalid-frame rejection, queue wrap/backpressure, RPQ-gated ownership, FE
failure/retry, key/CAM reuse, role setup faults and scan withdrawal.

Factory verification executes the supplied original module, without RF or
hardware access. Pass the local factory ELF path explicitly:

```sh
python3 tools/trace_stock_ap_port.py --client --module "$STA_FACTORY_ELF" \
  --output artifacts/sta-roles.json
python3 tools/trace_stock_station_tables.py --client --module "$STA_FACTORY_ELF" \
  --output artifacts/sta-tables.json
python3 scripts/test_sta_factory_payloads.py artifacts/sta-roles.json \
  artifacts/sta-tables.json
```

The factory module used in development has SHA256
`745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`.
Eighteen C/XB CAM and CCTL cases match byte-for-byte, including aggregation.
The PE image is 24,504 bytes within 32KiB IMEM; its conservative static stack
bound is 6,768 bytes within 8KiB DMEM.

The regular firmware build regenerates `pe-tx-code.h` from `port/pe` before
syncing the overlay. A manually built test module must embed the matching
generated header as well. The FE and Wi-Fi netdev changes also require the
matching Ethernet driver; replacing only the radio module is insufficient.

## Remaining board acceptance

Use an authorized test AP and a wired recovery connection. Coordinate with
other active board experiments before replacing drivers or changing radios.

1. Verify 2.4GHz and 5GHz association, DHCP, IPv4/IPv6 and the four-way handshake.
2. Verify wrong passwords, AP reboot, repeated disconnect/reconnect, PTK/GTK
   rekey, scan during traffic and AP-to-STA-to-AP transitions.
3. Exercise wired-to-STA and other-radio-AP-to-STA routed traffic in both
   directions. Confirm `STA_associated=1`, hardware key/PN activity, BA sessions,
   `auto_offload_starts`, `PE_NI_sent`, `PE_uplink_frames` and FE flow counters.
   A firewall offload checkbox alone is not acceptance evidence.
4. Compare TCP/UDP throughput and CPU usage with offload enabled/disabled;
   check for stuck queues, replay errors, packet loss and kernel warnings.

The main web/package/scan fixes are included in the current baseline.
