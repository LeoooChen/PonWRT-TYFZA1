# EPON / ITU ownership audit

2026-10-15. Follow-up to `pon-itu-startup.md`.

Three further defects were confirmed and corrected:

1. **Reverse identity dependency.** `GPON_SN` was still mandatory before the
   importer could save valid EPON LLID MAC / LOID fields. The ITU and EPON
   mandatory-field imports now run independently. A failed family still emits
   its error and sets the importer's nonzero exit; the other family's data is
   saved and can be consumed by its daemon.
2. **Wrong-family control effects.** Public EPON traffic-stop and MPCP-stop calls
   could disable shared TX / FE resources while ITU was active. LLID writes and
   resize were also accepted in ITU mode. Public control now checks protocol
   ownership while holding `control_lock`, returning `-EOPNOTSUPP` without those
   effects. The service stop path now reads the native driver's current
   `serdes.mode`: EPON uses traffic / MPCP / OAM; ITU uses activation / FE / OMCI.
   Pending UCI changes do not select the stop path.
3. **Stale state assigned to another family.** In EPON mode, `activation` fell
   through to cached GPON state and error fields, so an earlier GPON error could
   make a healthy EPON link appear faulty. EPON now reports ITU activation / SN
   as unsupported; ITU reports EPON MPCP / LLID / traffic observations as
   unsupported. Current-family error values are retained.

Stop also records and returns an operation failure while continuing the
remaining shutdown work, including when ITU FE cleanup fails.

## Original implementation

The board's original `ca-ne.ko` `aal_pon_mac_enable_set` was executed with mode
values 0 through 5 and both direction controls disabled. Modes 0..2 dispatch to
`aal_epon_mac_cfg_set`; mode 3 disables the laser / SerDes burst and calls
`aal_gpon_active_set`; modes 4..5 disable the laser / burst and call
`aal_xgpon_xgpn_glb_ctrl_set`. Callees were modeled, so this establishes original
dispatch, not optical behavior. Local record:
`artifacts/pon-family-audit/stock-enable.json`.

## Focused verification

- Before: missing GPON SN prevents any EPON identity file from being written.
  After: the actual importer saves its EPON identity and the ARM64 OAM daemon
  accepts its original MAC / LOID configuration.
- Actual control-function reproducer fails before the patch on cached GPON
  state exposed through EPON, and separately on EPON stop entering ITU TX / FE
  cleanup. After the patch, all six modes retain their own supported controls;
  cross-family stop / LLID requests do not enter hardware or netdev operations.
- Actual BusyBox service stops the running family with the opposite family
  already saved in UCI. An injected ITU FE failure remains a failed stop, while
  activation and data-interface shutdown continue.
- The existing ITU identity startup regression passes with the changed
  importer and service. Shell syntax and whitespace checks pass.
- All twelve PON translation units compile and link into an isolated ARM64
  module against the configured Linux 6.18.55 build. Modpost reports that the
  aggregate checking module lacks `MODULE_DESCRIPTION`; no compile/link error.

Records and source hashes: `manifests/pon-family-audit.json`. Driver hardware,
netdev and procd operations in the reproducer are modeled. No complete firmware
build, flash, protocol switch on the modem, or live O5 claim is included.

## 2026-10-16: internal lifecycle correction

The public-entry checks were insufficient. BOSA preparation, SerDes changes
and MAC initialization still called multiple families' stop routines. The MPCP
routine disabled shared FE before checking its family and discarded its return
code. SerDes then changed the selected mode even if cleanup had failed.

The original `aal_pon_deinit@0xcd870` was executed for all six supported modes:
0..2 call only `aal_epon_deinit`, 3 calls only `aal_gpon_deinit`, and 4..5 call
only `aal_xgpon_deinit`. Dependency bodies were modeled; this verifies the
original dispatch, not an optical link. The recorded initializer/deinitializer
calls are in `logs/stock-pon-mode-dispatch.json`.

`rtl_pon_stop_locked` now selects the current family once while holding the
control transaction lock. BOSA, SerDes, MAC preparation and device shutdown use
that operation. EPON stops its traffic/MPCP; GPON and XGTC stop their own
activation engines. Shared forwarding cleanup is explicit, and errors reach
the caller. A failed cleanup still stops the old protocol and optical TX, but
blocks BOSA reprogramming and SerDes mode changes. SerDes records the failure
while retaining the old selected mode. Unsupported typed MAC preparation
returns before touching the active family's laser or forwarding.

The MPCP hardware-stop routine no longer owns global FE teardown; the EPON
public stop and common lifecycle operation handle it and return failures.
PON FE ownership is cleared only after NI teardown succeeds, so a failed
cleanup cannot be skipped by a subsequent mode-change attempt. Ownership is
also recorded immediately after successful NI enable, before PDC steering
writes; a failed PDC write must not leave a running NI engine unowned.

The exact pre-change SerDes function fails the new cleanup-error reproducer:
it returns success instead of `-EIO` when switching XGS-PON to EPON. The changed
function returns the error, retains XGS-PON selection and FE ownership, and
keeps optical TX disabled. Existing concurrency checks still prevent MPCP from
restarting between stop and mode selection. Actual shutdown code selects only
the current protocol engine across six modes and still masks/drains IRQs and
joins work after an FE error. The actual FE function retains ownership on NI
failure and releases it after successful cleanup. EPON preparation/traffic,
the importer/service regression and the isolated ARM64 PON module build pass.

At this commit's offline checkpoint the connected device had only been read;
no PON replacement or restart had yet been performed. The following live
follow-up supersedes that deployment limit. Updated sources, module hash and
focused results are recorded in `manifests/pon-family-audit.json`.

## RAM EPON regression

The production PON sources from `b41a1b8` were built as
`artifacts/pon-family-live-20261016/kernel/rtl_pon_family_live.ko`. Only the
platform driver name changes to `rtl9617c-pon-family-live` for replacing the
built-in binding. Module SHA256:
`0ae8f4be481cabd5ea5144749ce4a33c29b7d9033409a3e2bca8b9ba0d304dca`.
The service script is bind-mounted from `/tmp`; flash and saved PON UCI settings
were not changed. `/tmp/restore-pon.sh` can stop the RAM service, unbind/unload
the candidate, rebind the built-in driver, unmount the temporary service and
start the original service. A reboot also removes this RAM deployment.

The running kernel and candidate use 6.18.55. Seven key NI/BOSA symbol
addresses match the local vmlinux, and the NI/frame headers in its build tree
match the overlay. Reduced kernel debug data did not provide complete NI
structure types for a DWARF layout comparison; kernel notes differ, so this is
not a claim that the entire local vmlinux equals the running kernel. Loading,
probing and the following actual transport operations succeeded.

- Stop old service, unbind old PON, bind the new module, start new service:
  BOSA/SerDes/MAC/PUC/PDC complete with error0. MPCP re-registers LLID0;
  OAM discovery completes, LOID authentication is accepted, and DHCP reacquires
  the WAN lease and default route.
- Gateway and public-IP ping each receive3/3 replies. A subsequent new-service
  stop/start returns0, disables MPCP/optical TX and clears FE flows during stop,
  then re-registers/re-authenticates and restores WAN; public ping receives3/3.
- In active EPON, reading `activation` and `active_serial_number` returns
  unsupported. Writing0 to `activation` fails without changing MPCP
  registration, optical TX-enable or FE-enable state. LuCI RPC reports
  `activation:null`, actual EPON state and service `stage=offload error=0`.

The device is left on the new RAM PON driver and temporary service, using its
original EPON D10G mode and identity. Wi-Fi remains on its earlier independent
RX refill module; the suspended Wi-Fi pipeline experiment was not reintroduced.
No optical protocol change or flash was performed. GPON/XG-PON/XGS-PON OLT
registration, actual cross-family switching, injected hardware cleanup failure
and sustained hardware-forwarding throughput remain outside this live result.
Raw records are in the Mac workspace `现场记录/20261016-pon-family/` and their
hashes are in `manifests/pon-family-live.json`.
