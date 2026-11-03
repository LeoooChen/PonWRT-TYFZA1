# Wi-Fi retirement and rejected completion records

When a PE transmit owner never completes, repeated station/key cleanup used to
pause the radio, send PKT_DROP and wait up to two seconds while holding the wiphy
mutex. This can block hostapd authentication even though other radio work still
runs. Retirement now remembers a successfully issued withdrawal and checks its
completion without restarting those operations on every retry.

The active retirement fence remains owned until its EPP cutoff and transmit
owners complete. A second departing peer withdraws its own FE admission without
replacing the first fence. New hardware offload admission waits; a new station
with no NI ownership can still proceed through software key/association setup.
No timeout authorizes reusing a MACID/key or returning an outstanding DMA buffer.

A BA retirement that later becomes full station/key retirement finishes the
original peer fence first, synchronizes its final RX state, then releases the
last global NI route if appropriate. Global release keeps its route ownership
until every release operation succeeds. The existing wiphy cleanup worker also
retries global release independently of individual station records, including
RX-only peers. This prevents a transient release error from leaving hardware
offload permanently unavailable.

PE saves sixteen rejected RPQ records in the per-radio shared allocation at
0x1e000, separate from the engine's private stack. On overwrite it publishes an
invalid sequence before replacement fields, publishes the completed sequence,
then advances the count. ARM checks the sequence before and after its snapshot.
The diagnostic does not change report acceptance or buffer recycling.

## Regression coverage

`test_radio_ni_retire_flush.py` executes the production retirement functions:
missing completions, 100 immediate retries, late completions, EPP fencing,
multiple peers, BA-to-station transitions, and one to three transient global
release failures. It also checks that an unrelated software-only station is not
blocked by pending global release. `test_radio_rx_ba_cleanup.py` verifies the
worker schedules global cleanup even without retired station/key records.
`test_ni_release_inflight.py` exercises the real final-release function with
outstanding owners and injected uplink/RPQ-stop failures.

`test_pe_rpq_firmware.py` executes linked MIPS instructions, including ring wrap,
rejected reports, and an ARM visibility model that delays invalidation stores
until SYNC. This model rejected the previous diagnostic writer. The BA-to-station
regression likewise failed against the previous containment candidate. These
are executable ownership/state and ordering models, not physical bus tests.

CI rebuilds the PE, compares it with the committed generated header, and runs
completion, publication and instruction/stack-layout tests. The ARM64 driver was
also built against the project's Linux 6.18.55 configuration.

This change repairs cleanup progress after completion loss. The first missing
or rejected completion remains a separate fault to identify from the captured
RPQ/owner evidence. It does not establish a cause or cure for the independent
CPU3 RCU-stall report. Normal reconnection after reboot is not a test of recovery
with outstanding owners; no new on-device fault-injection result is claimed.
