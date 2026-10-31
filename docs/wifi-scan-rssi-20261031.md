# Scan RSSI and DFS AP scanning (2026-10-31)

Baseline: main `ed8ba43`, firmware `0.8.3-beta`. Tested on the 2.5G-LAN
TYFZA1 using RAM-only driver replacement and bind-mounted userspace files.
No firmware image was flashed; permanent wireless configuration was not edited.

## Root causes

- Every discovery frame carried `RX_FLAG_NO_SIGNAL_VAL`; the PHY did not
  advertise `SIGNAL_DBM`. iwinfo consequently left its unsigned signal byte
  at zero, and rpcd converted it to `0 - 256`. This was missing data, not a
  measured -256 dBm signal.
- The test AP used primary channel 36 with HE160, center 5250 MHz, country CN.
  Its occupied bandwidth includes DFS channels. mac80211 rejects its
  off-channel scan with `-EBUSY`. rpcd previously returned an empty successful
  result, hiding this distinction. Stopping hostapd allowed the same hardware
  to discover a neighbouring 5GHz AP immediately.

## Driver change

`scan.h` parses the actual MAC/PHY report layout, including aligned user
entries, optional RX counters and PLCP data. A valid normal CCK/OFDM/HT/VHT/HE
PHY page must contain the appropriate common IE0/IE1. Its frequency-domain
RPL is converted as `(RPL >> 1) - 110`, consistent with the board's original
ARM64 parser; header TD RSSI and associated-client samples are not substituted.

One discovery skb is retained until the following PPDU report. BB selector,
PPDU type/count, rate, GI and bandwidth must match within 20 milliseconds
of host receipt; an old pending frame cannot survive into a later periodic
sampling window and match a wrapped 3-bit PPDU ID. A subsequent Wi-Fi frame,
channel transition or stop retires an unmatched skb without a BSS-cache update.
This intentionally prefers missing an unmeasured observation over assigning
another AP's RSSI or overwriting a valid sample with zero. Normal data-frame
and PE forwarding paths are unchanged. Only scan discovery RSSI is added;
this is not a claim that all receive PHY metadata is implemented.

Software scan callbacks keep PPDU reports enabled for the scan and accept
foreign discovery frames. Completion restores the previous AP receive mode
and periodic RA sampling policy, rather than permanently sending every
neighbour PPDU to the host CPU. `radio_ra` exposes matched/unmatched counts
and the last matched BSSID/raw RPL for verification. The final module also
returned 5GHz RPL 151 / -35 dBm and 2.4GHz RPL 157 / -32 dBm in device tests.

## DFS-safe scan workflow

The ordinary `iwinfo.scan` RPC now reports failure instead of silently
returning an empty list. Both LuCI wireless scanning and channel analysis
stop automatic retries and offer a translated **Pause AP and scan** action.
It requires confirmation and write permission. No automatic refresh pauses
an AP, and no regulatory/DFS rule is removed.

The separate `iwinfo.scan_pause` RPC verifies hostapd is enabled, issues
DISABLE, scans, and issues ENABLE even after a failed scan or a lost DISABLE
reply. A separate recovery process is notified through a socket pair; it
also sends ENABLE if the RPC process dies or fails to complete in 15 seconds.
The iwinfo netlink wait has a 10-second completion deadline and reports scan
aborts. Restoration failures are explicitly reported. ENABLE initiates
normal hostapd startup; it does not mean CAC has already finished.

The test HE160 AP performed a normal 60-second CAC and returned to ENABLED,
with the DFS detector enabled and no radar bypass. Users connected through
this AP will be disconnected during this operation, potentially for over a
minute. A non-DFS channel/bandwidth permits normal scanning without this
explicit AP stop, subject to normal off-channel pauses.

## Verification

- `test_wifi_scan.py`: original AP scan request cases and 96 receive cases,
  plus matched discovery RSSI, all PPDU IDs, mismatched metadata, truncated
  PHY reports and CRC rejection (ASan/UBSan).
- `test_wifi_scan_rpl.py`: 6,144 CCK/OFDM vectors against actual original
  ARM64 `halbb_physts_ie_00/01`, including unassociated user records.
- `test_wifi_scan_pause.py`: real fork/socket-pair recovery with mocked
  hostapd/scan boundaries: success, scan failure, lost DISABLE reply, RPC
  death, disabled/CAC rejection, and failed/retried AP restoration.
- `test_wifi_scan_timeout.py`: completion, abort, missing event, EINTR,
  socket failure and unrelated-event deadline (ASan/UBSan).
- Scan-interface, channel-retry, DIG bandwidth (1,332 original ARM64 vectors),
  AP stop/NI retirement, and peer RA tests pass. Relevant extracted-function
  test fixtures were updated for the new scan-retirement boundary.
- Native ARM64 wireless module, iwinfo library and rpcd iwinfo plugin compile.
  Kernel module build retains the existing MODULE_DESCRIPTION warning.
- Browser validation through Kimi WebBridge: channel analysis displays a
  neighbouring 5GHz AP and graph; the wireless scan modal also lists it.
  2.4GHz shows multiple BSSes with distinct RSSI values, not -256 dBm.
  One 5GHz sample was RPL 158, -31 dBm in both sysfs and LuCI. Other scan
  samples vary with RF conditions; they are not calibrated power measurements.
- Final observed state: TYFZA1-5G, primary 36/HE160, DFS enabled, CAC finished,
  no kernel BUG/Oops/WARNING captured during the session.
- Before committing, all nine relevant tests were rerun on main `57842be`.
  The flowtable lifetime patch from that commit applies without fuzz to an
  isolated copy of the prepared kernel; its 2,000-cycle lifetime test and
  IPv4/IPv6 bridge-move test pass. The two changes touch disjoint files.
  The shared OpenWrt build tree was not modified for these checks.

`test_radio_regulatory.py` and `test_wifi_lifecycle.py` have pre-existing
outdated fixture definitions (DFS fields and Ethernet `llid`, respectively).
They fail to compile on unchanged HEAD as well. This is not a full-suite pass,
nor a long-duration traffic/throughput or laboratory RF accuracy certification.

## Artifacts and deployment

VM: `artifacts/wifi-scan-rssi-20261031/` contains module and userspace builds,
original-HEAD failure logs and exported LuCI sources. Mac workspace artifacts
with the same directory name contain scan outputs, regression output and
`luci-2g.png` / `luci-5g-wireless.png` screenshots.

RAM module SHA256:
`cf31241bbe2ee5e624b23e221c39188870882ae1a560e6ce923d5c2fa61ce3ae`

rpcd iwinfo plugin SHA256:
`475c3ca0c129030f5603dd8974eb25cb3383e11d40a3a6707dfc1597eaa4e24e`

The device currently runs the RAM candidate. Reboot removes the temporary
module replacement and bind mounts and restores the original installed
0.8.3-beta behavior. Source changes are wired into
`port/scripts/sync-overlay.sh` for a subsequent firmware build. No new
release number or full firmware image has been produced by this change.
