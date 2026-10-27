# iperf3 PON-path benchmark

This diagnostic uses unmodified iperf3 endpoints for TCP/UDP generation and
measurement. The kernel adapters feed the server's actual data packets into
NI VP11 as source 7, PPPoE session 0x1234; the FE removes the PPPoE header,
applies the configured destination and forwards to the selected physical LAN
or AP netdevice. The reverse test installs an FE rule from that interface,
inserts a PPPoE session header in hardware, and forwards to a CPU endpoint. The
endpoint validates and removes that header before iperf3 receives the packets.
It does not exercise the actual PON egress queue or optical transmitter.

This is an internal PON ingress simulation. It does **not** test the optical
transceiver, an OLT, session negotiation, or the public internet. The FE and
Wi-Fi hardware are real. A wired test requires a carrier on the selected LAN
interface and a real peer connected to it. A single gigabit port limits wired
throughput to less than 1 Gbit/s.

## Build and prepare

On the Linux build host, run `sh scripts/build_iperf_pon.sh`. The output is
`artifacts/iperf-pon/`: three modules and the `set_peer` helper. Copy these and
`tools/iperf-pon/bench.sh` into the same directory on the device. Supply an
iperf3 binary and its shared libraries on the device. The test client also
needs iperf3 and must route `198.18.255.1` through this device.

Start a **single** test session on the device, selecting one protocol and one
output (`ap5`, `ap2`, or `lan1` through `lan4`):

```sh
IPERF3=/path/to/iperf3 IPERF_LIB_DIR=/path/to/libraries \
    IPERF_CPU_MASK=1 sh bench.sh start udp ap5 192.168.1.158 28:d0:ea:5e:0a:4c
```

The peer address and MAC above are examples; use the actual client on the
selected interface. `start` requires the interface to have carrier. The
diagnostic uses `ponfeed0`, `ponsink0`, source IP `198.18.255.1`, TCP/UDP
server port `5206`, client data ports `55000`–`55003`, routing table 220 and
rule priority 22000. It refuses to reuse that priority or an existing session.
It temporarily adds a narrowly scoped firewall input rule for the sink.

On this four-core device the NI RX interrupt is on CPU2. The temporary sink
uses RPS mask `a` (CPU1 and CPU3) to distribute network-stack processing;
set `RPS_CPUS` if the interrupt layout differs, or set it to `0` to disable
RPS. `IPERF_CPU_MASK=1` pins the server and its future worker threads to CPU0,
which reduced receive-buffer overruns in an uplink test. It is optional:
leave it unset when investigating downlink, where sender CPU demand differs.
Neither setting changes the production interfaces or persists after stop.

On the client, measure downlink (`-R`) and uplink, respectively:

```sh
iperf3 -c 198.18.255.1 -B CLIENT_IP --cport 55000 -p 5206 -u -l 1400 -b 400M -w 4M -t 10 -R -J
iperf3 -c 198.18.255.1 -B CLIENT_IP --cport 55000 -p 5206 -u -l 1400 -b 400M -w 4M -t 10 -J
```

Stop the session and restart it with `tcp` to test TCP (omit `-u`, `-l` and
`-b`). Up to four parallel data streams are supported (`-P 4`); iperf3 applies
`-b` per stream. `-w 4M` requests larger socket buffers at both endpoints;
check their OS buffer limits if iperf3 rejects it. Four streams at `-b 300M`
request 1.2 Gbit/s in total, but always compare the actual sender rate with
the requested rate: a client can be CPU limited. The control connection uses
the normal interface and is not counted as simulated PON traffic. When testing from a multihomed macOS host,
scope `-B` to its Wi-Fi interface, for example `192.168.1.226%en0`.

Read the adapter counters on the device with `sh bench.sh status`. For a
downlink result to be a hardware-path measurement, the feed data packet count
and FE hit count must increase; for uplink, the sink's FE and received counts
must increase. Compare iperf3 sender and receiver rates, loss and host CPU;
the router's iperf3 processes and CPU endpoint can limit throughput or drop
packets independently of the Wi-Fi/FE path. `status` includes cumulative
UDP `RcvbufErrors` and the sink's `bad_pppoe`; compare snapshots immediately
before and after each run. An increase in `RcvbufErrors` identifies receiver
socket overflow, while FE hits exceeding sink receipts indicate loss before
the CPU endpoint. Per-stream iperf3 loss can omit packets still in flight at
the end, so also compare sent and received bytes. For Wi-Fi, `status` also
includes PE counters; compare their deltas with the feed and FE counters. A
data mismatch or counter that does not increase is not a successful test.
The device's `qm_state` contains hardware no-buffer counters; reading it can
clear interval counters, which can saturate, so record the full snapshots.

Finally run `sh bench.sh stop`. It stops only the test server and removes its
firewall rule, modules and policy rule. The sink module is pinned until the NI
endpoint has drained, and the feed is pinned until its DMA reset completes. If release fails, `stop` returns an
error and retains the remaining resources for a safe retry rather than freeing
an active endpoint or DMA buffer.

## Verification scope

TCP and UDP in both directions, including four UDP streams, have been exercised
on a 5 GHz AP with real iperf3 clients and FE counters. The FE PPPoE push has
been verified with TCP and UDP clients; the CPU destination is still a
diagnostic substitute for the PON egress queue. Failure rollback and
module pinning have also been checked. LAN rule installation requires a live
physical link and its end-to-end path has not yet been tested on this device.
