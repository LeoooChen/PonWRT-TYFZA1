# DFS regulatory lifecycle and CAC

2026-10-17, local main based on 14ca129. No push, publication, device
reconfiguration, flash, full test suite or full firmware build.

## Reproduced -95 lifecycle defect

The native regulatory notifier used `radar_required` without checking whether
mac80211's radio had actually started. `.start` sets this demand before channel
configuration; an error leaves it set while `monitor_started` is false.
mac80211 does not call `.stop` for a failed driver `.start` (its `drv_start`
returns the error directly). `.stop` also omitted clearing the demand.

A later country notification with DFS_UNSET therefore re-entered the saved
channel configuration. `rtl_ax_dfs_start_locked` returned -EOPNOTSUPP (-95)
for domain0. This produced `country radar update failed: -95` even though no
interface owned a running detector, and could overwrite the earlier start/stop
error. The focused regression executes the actual native `.start`, `.stop`,
notifier and DFS start/stop functions; RF retuning and framework calls are
explicit boundaries. Before the fix, an injected failed `.start` followed by a
world-domain notification reproduces that exact error. This is a software
lifecycle reproduction, not a new RF or issue-reporter device capture.

The notifier now caches the new domain without starting a detector on an
inactive radio. The normal country power-table update is retained. `.stop`
clears the demand; the next `.start` obtains a fresh demand from mac80211 and
uses the updated domain. An unknown domain while running still returns -95,
keeps TX blocked and logs the error, now including alpha2, DFS region, primary,
center and complete channel width. Hardware timeout errors remain visible.
No country is guessed, no domain is defaulted to FCC/ETSI, and no CAC timer or
radar check is bypassed.

## Original implementation evidence

The stock module SHA256 is
745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e.
`rtw_dfs_rd_disable@0xbf8ac` clears its active detector request, old channel
and CAC timestamps before stopping its timer and detector. Native stop now
also withdraws its own demand. Host bookkeeping is independent of the original
private driver structures.

Actual stock `rtw_chset_is_dfs_chbw@0xb4eac`, its frequency-range and center
helpers, and `rtw_chset_is_dfs_range@0xb4dec` inspect the entire channel width.
An explicit CN channel set with DFS flags for52–64 returns no DFS overlap for
36/20 and36/80, and DFS overlap for36/160,44/160 and52/80. These inputs come
from the locked regulatory DB; framework/timer boundaries are modeled. This
does not represent hardware receiving a radar pulse.

`rtw_get_ch_waiting_ms@0xbfcdc` also executes the original waiting decision:
with no preceding CAC or non-occupancy timer, the explicit CN input gives
64000ms for 36/160 and 0ms for 36/80. The original private driver's wait is
therefore not copied into mac80211: Linux owns the 60000ms CAC below. These
are separate timers with separate owners, not a reason to wait twice.

## The minute of silence at HE160

The locked CN regulatory rules mark5250–5350MHz as DFS-FCC. HE160 with primary36
or44 and center50 covers36–64, including the four DFS subchannels52–64.
The actual locked hostapd `dfs_get_start_chan_idx`, `dfs_get_used_n_chans`,
`dfs_check_chans_radar` and `dfs_get_cac_time` return four radar subchannels
and60000ms for these HE160 cases, and0ms for36/80. The kernel minimum is60000ms
in `include/net/cfg80211.h`. mac80211 schedules `dfs_cac_timer_work` using the
requested CAC duration, then sends CAC_FINISHED. The native driver keeps its
TX scheduler and beacon blocked until AP startup after CAC.

Existing physical evidence from2026-10-15 already records
`DFS-CAC-START ... chan=44 ... seg0=50 ... cac_time=60s` and simultaneous
`CAC=1`, scheduler0 and disabled beacon, followed by `CAC=0` and an associated
HE160 client. Source records are
`现场记录/20261015-160mhz-live/cac-status.txt`,
`现场记录/20261015-160mhz-live/ctrl-final-300-duplex-after.txt`, and
`manifests/radio-160-live.json`. This establishes a normal60-second CAC on that
prior setup. The issue's61–63-second observations are consistent with that
timer plus setup overhead, but its report lacks a paired CAC-start/completion
log, so the exact three historical intervals cannot be individually proven.
There is no justification for shortening CAC to improve startup time.

## Focused validation and remaining boundary

- `python3 scripts/test_radio_regulatory.py`: failed-start/stopped notifications,
  stop/restart demand, FCC/ETSI/JP mapping, active unknown-domain rejection,
  and hardware stop timeout preservation.
- Stock machine-code range/disable comparison and locked hostapd CAC decisions:
  `artifacts/dfs-regulatory-20261017/stock-evidence.json` and `cac-evidence.json`.
- ARM64 native radio module builds from the changed source; the pre-existing
  MODPOST MODULE_DESCRIPTION warning remains.

The issue contains the error text without the triggering notification's
country/domain/radio state. The reproduced lifecycle defect is fixed; this
does not prove that every historical occurrence had that cause. If the enriched
message occurs while running, its actual unknown/unsupported region or hardware
failure must be investigated. No new RF, DFS-generator, CAC completion or
device startup acceptance is claimed for this patch.
