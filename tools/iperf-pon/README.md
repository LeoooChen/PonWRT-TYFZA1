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
UDP delivery enters RPS directly because default iperf3 sockets do not request UDP GRO;
TCP keeps the NI NAPI GRO context. This avoids UDP checksum work on the NI
poller before RPS distributes the packets to the receiver CPUs.

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

For high-rate downlink with an iperf3 server supporting UDP GSO, use one
stream and `--gsro`. The client can lack GSO/GRO support: the Linux server
still batches its sends, and the client receives ordinary UDP datagrams.
The feed does not advertise GSO, so Linux segments packets before calling
the adapter. Use 1464 payload bytes for the 1492-byte PPPoE IP MTU:

```sh
iperf3 -c 198.18.255.1 -B CLIENT_IP --cport 55000 -p 5206 \
    -u -l 1464 -P 1 -b 1712M -w 4M -O 15 -t 65 -R --gsro -J
```

Leave `IPERF_CPU_MASK` unset for this downlink test. With server 3.22 and
macOS client 3.21, GSO reduced server CPU from about 2.6 cores to 0.95 cores
at 1.712 Gbit/s offered load. Feed counters showed about 44 packets per
doorbell, no invalid packets, and actual reception of 1.709 Gbit/s with
0.18% sequence loss. This is a lower-overhead load source, not a loss fix.
`gso: 0` in the Mac client's JSON describes its local capability; verify
server batching through the feed packet/doorbell deltas. Omitting 15 seconds
affects iperf statistics only; hardware counter snapshots also include warmup.
The sink still sends UDP directly to RPS, including when `--gsro` is used,
so this option currently improves server transmission, not sink aggregation.

### Optional source backpressure

`EQ_BACKPRESSURE=1` at `bench.sh start` gates the internal PON source on
the selected radio's free EQ buffers (ap5/EQ6 or ap2/EQ7). It reserves 128
buffers plus submitted NI descriptors, publishes a pending batch before
stopping, and retries from the existing timer. It does not change FE/PE
forwarding or hardware flow-control registers. Default `0` retains the raw
saturation test; this option is not supported for LAN outputs.

```sh
EQ_BACKPRESSURE=1 IPERF3=/path/to/iperf3 IPERF_LIB_DIR=/path/to/libraries \
    sh bench.sh start udp ap5 CLIENT_IP CLIENT_MAC
iperf3 -c 198.18.255.1 -B CLIENT_IP --cport 55000 -p 5206 \
    -u -l 1464 -P 1 -b 1720M -w 4M -O 15 -t 65 -R --gsro -J
```

Two 65-second runs received 1.720 Gbit/s with zero sequence loss and zero
FE-to-PE packet gap or EQ6 no-buffer drops. The full warmup-inclusive
feed/FE/PE/recycle counts matched (11,750,465 and 11,749,981 packets).
The receiver summary can still exclude in-flight tail packets; compare the
full hardware counters and both endpoints' byte counts. This measures
sustained Wi-Fi forwarding with a flow-controlled diagnostic source, not
lossless handling of arbitrary unthrottled ingress bursts. Always report
`eq_backpressure` from `status` alongside a throughput result.

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
