# LAN address validation and Wi-Fi cleanup

A CIDR-list LAN configuration such as `192.168.1.1/24` has no separate visible
netmask field. LuCI previously accepted a bare replacement address through
`ipmask4`. With no UCI netmask, netifd installs it as `/32`, losing the connected
subnet route and preventing ordinary LAN clients from reaching the router.

Patch115 requires explicit CIDR or dotted-mask notation in list mode, including
when switching from the traditional address/netmask form. The traditional form
and explicit `/32` addresses remain supported. Address-list additions, removals
and deletion now trigger the existing connectivity warning. A separate template
cache suffix prevents clients retaining old JavaScript without depending on the
other resource-version patches' suffix.

`test_lan_address_ui.py` executes the actual LuCI validation compiler and types,
static protocol form methods, and connectivity-warning code. It applies the
entire LuCI patch series twice. `--before` reproduces the bare-address bug.
The isolated network experiment used netifd/rpcd/uhttpd binaries identical to the
running device: explicit `/24` changes retained ping and HTTP access, bare IP
installed `/32` and lost both, restoring `/24` recovered both. The rpcd timeout
restored the original UCI address. The fixture manually dispatched network reload;
it did not exercise procd, DHCP renewal, a full browser apply, or the physical NI.

## Wi-Fi cleanup after missing completions

The existing retirement fix retains PE withdrawal progress. Ordinary ARM-owned
key or station packets can also remain pending, however. The cleanup worker still
waited one second per key and station/TID under the wiphy mutex. Multiple stale
items and repeated cleanup passes multiply this lock occupancy. nl80211 acquires
RTNL before waiting for wiphy, so this can delay unrelated network management.

Cleanup now checks the existing key and station pending predicates immediately
and retries through the existing delayed worker. STA DROP commands retain their
ordering, but their submission is not treated as DMA completion. BA stop also
waits asynchronously before issuing its firmware update or completion callback.
The first synchronous key deletion and BA-start SSN wait remain unchanged.
No missing completion permits reuse of a key, MACID or DMA buffer.

`test_wifi_cleanup_nonblocking.py` executes the real pending predicates, key
installation/deletion, key cleanup, station DROP and BA completion functions.
It models 100 retries with missing ARM-key and PE-station completions, checks no
retry waits, retains owners and unrelated keys, then verifies late completion
permits cleanup. The same regression fails at the extra-wait assertion on
`2e69ef6`. Existing key fault/PN/CAM, overlapping waiter, FE transaction,
RX BA, NI retirement, inflight release and authorization tests pass.

The driver compiles for ARM64 against an isolated copy of the prepared 6.18.55
kernel. No hardware module reload or firmware installation was performed.
This removes another completion-wait amplification path; it does not explain
the first missing/rejected RPQ, remove H2C or RCU waits, or establish a cure for
all SAE/RCU stalls. WPA2 has also exhibited the recorded retirement failure.

## Integration

Changes are isolated on `fix/network-feedback-20261103`, based on `2e69ef6`.
The shared source and its active build tree are not modified by this work.
Current shared edits were copied and checked for patch compatibility; future
concurrent changes must be checked again before integration. The existing
retirement fix `85e6eb3` is a prerequisite already contained in the base.
The independent workflow checks these regressions without editing the active
release or general PR workflows.

2.5G NI4 already has FE registration, ingress admission and egress mapping.
There is no evidence-based driver change for that port in this patch. The blue
`lan1` has no cable carrier in the captured device, so physical FE forwarding
and sustained 2.5G throughput remain unverified.
