# Late AP membership and the 530 Mbps investigation

## Status and correction of the working hypothesis

The short-A-MSDU fix is separately committed as `961b754`. This continuation
finds and fixes an OpenWrt control-plane notification defect. It does not
establish that the reported 530 Mbps plateau has been resolved.

A previous broad assumption that the peer's slow transfer necessarily ran
through PE was not justified. The peer's local record
`wifi-deferred-home-test.txt` reports, on a52b556/0.5.0-alpha:

- a connected HE/2SS/160 MHz station;
- no wireless AP device in the installed nft flowtable, although fresh
  `fw4 print` included it;
- zero PE downlink MPDU and uplink frame counters at that observation.

Those counters are preserved as a prior observation, not a fresh reading.
In particular, the 26 station samples in `native-wifi-speed-cpu-fe.txt` do
**not** capture a high-load plateau: the 24 adjacent non-reset intervals peak
at only 0.03035 Mbps TX and 0.03090 Mbps RX by station accounting. The file name
is not evidence that 530 Mbps was measured during those samples. Zero PE in
an idle observation does not prove which path the later speedtest used.

## Confirmed notification defect

The exact pinned netifd and firewall4 sources establish this chain:

1. `bridge_enable_member()` broadcasts `DEV_EVENT_TOPO_CHANGE` after successful
   kernel bridge membership; `bridge_disable_member()` does likewise on removal.
2. `interface_main_dev_cb()` only requests protocol renewal for this event.
   It does not emit `IFEV_UPDATE`. A static, already-up LAN thus has no hotplug
   notification when the AP arrives late.
3. firewall4's interface hook only accepts ifup or an ifupdate with address/data
   causes. Its init script has no bridge-membership notification subscription.
4. `resolve_hw_offload_devices()` recursively walks current sysfs lower devices.
   It obtains the correct AP leaf on a fresh render, but the installed table
   is not refreshed by an event that never reaches firewall4.
5. In this kernel, `nft_dev_forward_path()` checks
   `nft_flowtable_find_dev(info.indev, ft)` before filling the physical reverse
   ingress and direct-transmit metadata. A missing AP leaf is not cosmetic.

This is a source-level root cause for stale flowtable membership. It is not
proof of a particular Mbps ceiling, nor proof that it explains the local
synthetic PE/NI load experiments.

## Original-firmware boundary

The board-original kernel configuration disables RTK_WFO and the PE A-MSDU
options. Its FC module contains the real `_rtk_fc_wlan_register` device/hook
registration path. Thus vendor FC behavior is the hardware/identity reference;
there is no basis for treating an unrelated WFO firmware as this board's
mandatory datapath, or assuming that every native transfer is PE-accelerated.

This repair is to the replacement OpenWrt control plane, verified against the
actual code that the project builds. It does not claim that factory firmware
uses nftables/netifd or copies any factory private ABI into OpenWrt. No radio,
NE action encoding, rate, buffer size or report threshold is changed.

Other checked evidence: original serial logs and native October 17 boot logs
both report PCIe0 Gen2 x1 / PCIe1 Gen1 x1. There is no recorded PCIe link-speed
downgrade to explain the plateau. Current radio pools are 2496 BIDs (2492
published), not the old 512 or a test fixture's 1024. The current 124-WP cap,
eight-MSDU maximum and report batch12 do not reproduce the old fixed
124-BID/121-report deadlock by those counts alone. Runtime backpressure,
power-save and completion delays remain separate possibilities.

## Native event-driven repair

- netifd emits an explicit topology update for an already-up interface.
- Existing DHCP renewal remains; setup/down states do not gain an artificial
  topology reload.
- A pending update-cause mask retains reasons while hotplug serializes or
  coalesces events. A later update that overwrites `iface->updated` cannot erase
  the queued topology cause.
- The child exports `IFUPDATE_TOPOLOGY=1`; firewall4 accepts this cause while
  preserving its enabled-service and configured-zone checks.
- firewall4 re-resolves its policy-authorized leaves and replaces its own
  flowtable through the existing reload transaction. No new permissive rules,
  direct ASIC learning, sleeps, polling daemon or unconditional boot reload.

Production patches:
`port/host-patches/120-netifd-interface-topology.patch` and
`port/host-patches/121-firewall4-interface-topology.patch`.
`port/scripts/sync-overlay.sh` installs them in the normal package preparation.
The current OpenWrt source checkout was not synced/rebuilt, avoiding parallel work.

## Focused verification

`scripts/test_fw4_topology_events.py` extracts the pinned package archives,
applies the patches without fuzz, compiles actual netifd callback/queue/dispatch
functions, uses real fork/exec, and runs the actual firewall hotplug script.
Protocol/ubus and fw4 execution are explicit modeled boundaries.

- Late add and removal each produce the topology cause and a reload after the
  fix; the original produces neither corresponding reload.
- An intervening update with no address/data flags does not erase the pending
  topology cause.
- A topology change during setup is covered by the pending ifup, not a second
  artificial boot event.
- DHCP still renews and now also notifies the firewall.

The four scenarios yield one original reload (ifup only), versus four corrected
reloads. A separate full production fw4 render at three fixture topologies
produces `{lan1, pon0}`, then `{lan1, phy0-ap0, pon0}`, then `{lan1, pon0}`;
all retain the configured hardware-offload flag. No kernel netlink installation
is simulated by this result.

The patched `interface.c` and `interface-event.c` compile with the production
ARM64/musl/LTO flags in an isolated directory. Both package patches apply to
the pinned archives without fuzz. Shell syntax checks pass. No full test suite,
full netifd link, firmware build, restart, upgrade or network switching ran.

## Device access and next decisive evidence

The USB Ethernet interface is inactive. The documented UART1120 was unowned;
it was opened with no reset, given a line-clear/empty-line input, and produced
no modem output. It was closed without sending device commands, interrupting
boot, operating power or touching UART1140. No live load result was obtained.

The next valid high-load observation must align all of these in the same run:
installed flowtable AP membership, actual ASIC flow keys/counters, PE TX/RX
activity, WP/queue/RPQ state, NI tail drops and client PHY/retry state. This
separates "acceleration never installed" from "PE service capacity" and
"radio/completion backpressure". The current notification repair is not an
on-device throughput acceptance result.

Evidence and source hashes: `manifests/wifi-flowtable-topology-20261017.json`.
