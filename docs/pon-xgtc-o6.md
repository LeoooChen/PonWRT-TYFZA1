# XGTC short loss of synchronization

The native XG-PON/XGS-PON IRQ unconditionally started its SDS/MAC rebuild on
ACTP loss-sync bit6. That bit may be reported while hardware remains in O6
(state4) or has already resynchronized to O5 (state3). In both cases the old
IRQ turned off ACTP, disabled SDS burst and queued a full rebuild, destroying
a session that could remain in service after the grace interval.

The board's original `ca-ne.ko` has `aal_xgpon_mac_reset_at_lods=0` in its
compiled state. Executing `aal_xgpn_act_fsm_lods_common_handler@0x115e50` with
that unmodified value moves the software FSM to O6 without accepting any
reset/timer callback. Executing
`aal_xgpn_act_fsm_s_o6_e_dsync_change@0x114950` returns it to O5. The selected
handlers perform no PON MMIO writes. Mode getter and logging are modeled;
this verifies their instructions and default dispatch, not optical timing.
Record and module hash: `logs/stock-xgpon-o6.json`.

The native IRQ now preserves ACTP/SDS, keys, OMCC identity, FE enable and line
epoch when loss-sync is reported in O5/O6. OMCI carrier remains off in O6 and
returns on O5; normal business invalidation/reapplication remains in place.
The actual production IRQ is replayed for both XG modes, including a coalesced
loss-sync plus O6-resynchronized event. The pre-change function disables ACTP
and burst and schedules recovery; the changed function retains them and
resumes OMCI without changing the line epoch. The vectors contain no PLOAM or
key-exchange packets. MMIO, carrier, NI invalidation and work scheduling
boundaries are modeled.

This is a bounded correction for O5/O6. Recovery outside those states still
uses the pre-existing full rebuild. Its normal timeout/drain behavior needs
further reconciliation with the original default0 path. An actual-function
reproducer also shows that a failed full rebuild can lose the FE restore
setting on its next attempt. A `fe_requested` candidate for that symptom was
saved under `artifacts/pon-recovery-policy-20261016/candidate-fe-policy/` and
is not applied; it must not substitute for correcting the recovery strategy.

The targeted IRQ check and isolated ARM64 PON module build pass. No full
firmware build or full test suite was run. This change was not deployed to
the modem: the connected line is EPON, so replaying it there cannot validate
XGTC O6. The previously verified `rtl9617c-pon-fe-family-live` RAM driver and
EPON service remain running. Physical XG-PON/XGS-PON short-loss, timeout and
re-registration tests still require an appropriate OLT.

Sources, module and replay hashes: `manifests/pon-xgtc-o6.json`.

## Assign-ONU-ID before O5

The same production IRQ reproducer exposed a first-activation failure: after
an Assign-ONU-ID PLOAM in hardware O4, the code programmed OMCC XGEMs1..7 but
cleared `xgpon_omcc_ready` because the line had not reached O5. The later O5
interrupt had no second assignment to restore that flag, so OMCI stayed down.

The board's original `ploam_ngpon2_proc_assign_onuid@0xe6c10` was executed with
the matching synthetic serial, ONU-ID17 and8-queue capability. It saved that
ONU-ID and issued upstream/downstream mappings for indices1..7 without an O5
gate. T-CONT lookup, mapping setters and the NGPON channel callback are modeled;
the original parser/serial comparison/dispatch executes. Record:
`logs/stock-xgpon-assignment.json`.

OMCC readiness now represents a configured channel, while OMCI carrier remains
gated by O5 and the integrity key. Cached channel/key/business state is cleared
on actual session termination, deregistration, Disable-SN or Deactivate-ONU-ID,
before processing PLOAM buffers captured by that IRQ. This also handles a
coalesced deactivation plus new burst profile and assignment: the old MSK,
data keys and business paths are invalidated first; the new channel/IK survive.
The hardware ACTP state is captured with the interrupt snapshot, rather than
after processing its packets.

Directed execution covers O4 assignment followed by O5 and coalesced new
identity in both O4/O5 for XG-PON/XGS-PON, retaining the O6 vectors. It executes
the production packet-buffer/table helpers; indirect completion, crypto key
derivation, NI invalidation, carrier and scheduling are modeled. It does not
cover every key-exchange interrupt or a physical OLT. The isolated ARM64 PON
module compiles. It was not deployed to the connected EPON line, whose verified
RAM module remains running. Full timeout/FIFO recovery and its FE restore
defect remain open. Evidence: `manifests/pon-xgtc-assignment.json`.
