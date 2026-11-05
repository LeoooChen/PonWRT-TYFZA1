# Flowtable worker lifetime and automatic reboot

## Evidence and scope

Two failures were captured on a TYFZA1 2.5G running 0.8.3-beta, at roughly
793 and 437 seconds of uptime. The second Oops identifies:

```text
pc=flow_offload_get_timeout+0x4/0x34 [nf_flow_table]
lr=flow_offload_work_handler+0x168/0x3fc [nf_flow_table]
comm=kworker/u19:674
x0=000000000000434f
fault address=0000000000004395
```

The module disassembly places this call in the statistics worker. The
faulting instruction dereferences the invalid conntrack pointer. The first
capture is incomplete and does not independently identify the function.
The collected device module and the release build have byte-identical
`.text` sections, SHA256:

`682fff0094212a6f8f191f5cb862f682b084004564927ec952bceef3ac09c8cb`.

With hardware flow offload temporarily disabled and software offload still
enabled, the user reported no reboot during the same activity. This is a
short control observation, not a long-term stability result. Raw logs,
network configuration, addresses and device dumps remain private.

The source-level lifetime violation below is reproducible and matches the
faulting path. Without KASAN or allocation/free tracing on the affected
device, the captured Oops does not prove the exact history of that object.
The final reset mechanism after the Oops is also not established. This fix
does not claim that every reported network loss or reset has this cause.
In particular, the separately recorded Wi-Fi dispatcher halt with an
unchanged boot ID is a different incident (see the WD-retention report).

## Root cause

1. TCP FIN/RST sets `NF_FLOW_CLOSING`. GC queues hardware DESTROY, which
   marks the flow `NF_FLOW_HW_DYING` without necessarily setting TEARDOWN.
2. DESTROY finishes and publishes `NF_FLOW_HW_DEAD`. The software flow can
   remain until its timeout or another teardown condition.
3. The next GC skips its CLOSING deletion branch because DYING is set, but
   still enters the HW statistics branch. The allocator checks only
   PENDING, so it queues STATS against an already-deleted hardware flow.
4. A later GC sees TEARDOWN and HW_DEAD and unlinks/frees the flow without
   checking the outstanding statistics work. Workqueue latency can exceed
   both the next GC interval and an RCU grace period.
5. STATS holds a raw flow pointer, not an independent reference. It can
   dereference the released flow/conntrack while updating timeout or stats.

A simple DYING check before claiming PENDING is insufficient: a stale RCU
reader can pass the check, pause during deletion, then enqueue new work.
Checking PENDING only at the start of GC also leaves a check/use race.

## Correction

`218-flowtable-work-lifetime.patch` uses the existing PENDING bit as the
worker/retirement ownership gate, without changing flow layout or flag ABI:

- After claiming PENDING atomically, the work allocator checks DYING and
  rejects new ADD/STATS and duplicate DESTROY requests. The first DESTROY
  remains admissible: it sets DYING only after allocation succeeds.
- GC defers flows with work pending. Workers publish their updates before
  clearing PENDING; GC observes completion with an acquire read.
- To retire a dead flow, GC atomically claims PENDING and leaves it set
  through unlink and RCU free. A late submitter either holds the gate first
  (so GC retries), or is rejected by the retirement gate.
- DESTROY retains the existing ordering: clear PENDING, then publish DEAD
  after its last other flow access. GC cannot free it in that interval.

Hardware/software offload defaults, FE forwarding, conntrack references,
RCU freeing, watchdog settings and radio code are unchanged. There is no
fixed delay, address-validity workaround, swallowed exception or reboot
suppression.

## Verification

The new test extracts actual GC, allocation, ADD/STATS/DESTROY entry points,
statistics processing and worker completion from the supplied kernel tree.
It models scheduling, RCU unlink/free, device statistics and atomic
interleavings. ASan/UBSan checks the harness; this is not kernel KASAN or a
proof of all weak-memory interleavings.

- Reversing only this patch reproduces all six checks: stats after deletion,
  unlink with queued stats, a late PENDING claim, pending-work retirement,
  stale-reader admission after unlink, and work admission racing DYING.
- The patched functions reject all six bad states and complete 2,000
  create/stats/teardown cycles with bidirectional accounting, allocation
  failure/retry, expiry, conntrack teardown and bridge changes.
- GC between DESTROY's PENDING clear and DEAD store does not free the flow;
  subsequent GC does. Software-only expiry remains unchanged.
- Existing bridge-move, hardware FDB ageing, IPv4/IPv6 software ageing,
  native FE release/stats/parser, PPPoE cleanup, LAN lifecycle and ITU PPPoE
  datapath tests pass. The bridge-move harness gains only the new bitops it
  needs to execute the changed production GC function.
- The two changed files regenerate byte-identically from the SHA256-checked
  Linux 6.18.55 archive by applying their OpenWrt and platform patch hunks
  in build order with `--fuzz=0`. The archive matches `port/sources.lock.json`.
- All constituent `nf_flow_table` sources compile and link into an ARM64
  module using the existing OpenWrt musl toolchain and prepared kernel.
  Standalone modpost reports missing `MODULE_DESCRIPTION`, not a compile or
  unresolved-symbol failure. No firmware image or source tree is replaced.

Tests with a prepared, patched kernel:

```sh
python3 scripts/test_flowtable_work_lifetime.py --kernel "$KERNEL"
python3 scripts/test_flowtable_work_lifetime.py --kernel "$KERNEL" --before
python3 scripts/test_flowtable_bridge_move.py --kernel "$KERNEL"
python3 scripts/test_flowtable_bridge_ageing.py --kernel "$KERNEL"
python3 scripts/test_flowtable_software_ageing.py --kernel "$KERNEL"
python3 scripts/test_fe_flow_release.py
python3 scripts/test_fe_flow_stats.py
python3 scripts/test_fe_flow_parser.py
python3 scripts/test_fe_pppoe_cleanup.py
python3 scripts/test_fe_lan_lifecycle.py
python3 scripts/test_itu_pppoe_datapath.py
```

Build and test logs are retained locally under
`artifacts/flow-lifetime-fix-20261031/`. The existing shared OpenWrt tree
is not patched in place; tests and the module build use isolated copies.

## Remaining device acceptance

No device access, reboot, module hot-swap, flash or firmware release was
performed for this fix. The patched module must be included in a normal
firmware rebuild and tested with hardware offload enabled, short-lived
TCP/UDP traffic, reconnects, firewall reloads and sustained mixed traffic.
Monitor for Oops, resets, stuck flows and packet loss beyond the observed
failure interval. Until that acceptance passes, treat this as a verified
source-level fix for a strong candidate cause, not confirmed end-to-end
resolution of the user's device.
