# Short A-MSDU fallback: factory NI length contract

## Finding and scope

Baseline is `124850c`, including the previous PE TX publication change.
The PE RX path rejects an entire A-MSDU if any LLC payload is below 54 bytes.
This includes a 48-byte LLC/IPv4/TCP ACK with a 20-byte TCP header, and a
36-byte LLC/IPv4/UDP packet. The input is returned to the ARM receive path,
not dropped. WLAN exceptions then take the existing receive-state barrier.

The limit was introduced to avoid overwriting the next subframe when padding
Ethernet to 60 bytes. **That padding requirement is incorrect for the board's
Wi-Fi lookup DMA interface.** Removing the length check alone would corrupt
the next subframe. Both the false padding requirement and its check are removed.

This is a reproducible fastpath defect, not proof of the reported 530 Mbps limit.
No current device was accessed, and no Mbps improvement has been measured.

## Board-original evidence

Frozen modules:

- `rtk_wifi6.ko`: `745328f06968003a02afa7ba7a3656f54be8f715d993f382f2a2e68acbad374e`
- `ca-ne.ko`: `16840ba70afc0ae700c2d63c57b8248475a67154eef89536d90028d4fe6486d7`

Actual `amsdu_to_msdu@0xba4f4` and `rtw_os_alloc_msdu_pkt@0x1d648` execute
with LLC lengths 48, 36, 60 and 1508. They produce Ethernet lengths 54, 42,
66 and 1514 with exact addresses/EtherType/data and no 60-byte padding.
SKB primitives, allocation and delivery policy are explicit model callbacks;
the actual subframe traversal and conversion instructions execute.

Actual `nic_egress_start_xmit_for_fc_wifi_hw_lookup@.nictrx-sec+0x2ee0` calls
`__ca_ni_start_xmit_buf_for_fc_wifi_hw_lookup@.nictrx-sec+0x25b0` without padding.
The latter's instructions at 0x26b0..0x26c0 clamp to 34, not 60. Executing both
functions with linear SKBs of lengths 33/34/42/54/60/1514 produces DMA mapping
and descriptor lengths 34/34/42/54/60/1514. Packet bytes are untouched. CPU0's
context8 mapping is read from the original table; DMA mapping and vmalloc
classification are modeled. No alternate chipset driver supplies this result.

The native allowed IP/UDP minimum already produces 42 bytes, above 34, so
there is no reason to add a new minimum-size clamp. The native QM return
path accepts Ethernet headers of 14 bytes or more; it does not require 60.

## Change

Only `port/pe/rtl9617c-radio-uplink.h` changes forwarding behavior:

- Use the existing 36-byte LLC/IP/UDP minimum for both standalone and A-MSDU.
- Publish the actual Ethernet byte count; never write a fake padding tail
  into the next subframe.
- Preserve frame validation, unicast/source checks, FE gates, PN/SN,
  descriptor credit and the two-tail-buffer ownership rules.

The generated PE array is updated. The focused standalone UDP checker now
expects the original NI length contract instead of native-only padding.
Generic ARM/NI copy helpers, PON, RF, aggregation/queue/report limits and
other parallel changes are not altered.

## Focused validation

`tools/check_pe_amsdu_short.py` executes the actual old/new linked RX entry:

| Aggregate Ethernet lengths | Before | After |
| --- | --- | --- |
| 54, 54 (two valid TCP ACKs) | ARM required | VP9 fastpath |
| 42, 54, 1514 (UDP, ACK, full UDP) | ARM required | VP10 fastpath |
| 1514, 1514 | VP9 fastpath | VP9 fastpath |

Each successful case verifies all Ethernet bytes, ring63-to0 wrap, one PN/SN
advance per MPDU, subframe count, and the complete input allocation. Only
in-place Ethernet address bytes change; next subframes, network checksums,
payload, A-MSDU padding and crypto trailer remain intact. Failed old cases
leave the original MPDU and PN/SN untouched. Three standalone short/full
IPv4/IPv6 cases also pass.

The PE is 15904 bytes. Only the embedding `tx.o` is rebuilt in an isolated
copy of the previously compiled radio sources. Its embedded PE matches the
profiled binary. Parallel monitor/PCI/XB work is not rebuilt or modified.
No full suite, full module rebuild, image build, network change or flashing.

Local factory replay scripts, disassemblies, outputs and hashes are listed in
`manifests/wifi-ni-length-20261017.json`. Reproduction from the repository root:

```
python3 artifacts/wifi-ni-length-20261017/stock-amsdu.py
python3 artifacts/wifi-ni-length-20261017/stock-ni.py
python3 tools/check_pe_amsdu_short.py artifacts/wifi-ni-length-20261017/before artifacts/wifi-ni-length-20261017/after
```

## What this does not explain

Two existing Ethernet captures were checked, not newly recorded:

- `logs/ubi57-wifi5g-mac.pcap`: pure IPv4 ACK TCP-header lengths are
  32/44/52/60 bytes (4864/2301/595/1493 packets). None has a 20-byte TCP header.
- `logs/2g-swPN-real-client.pcap`: four pure ACKs, all with 32-byte TCP headers.

Those ACKs are too large to trigger this particular short-A-MSDU check.
Ethernet captures also do not prove wireless A-MSDU packaging. Therefore this
fix must not be described as the established cause of the Mac's throughput
plateau, or of the historical full-size UDP downlink plateau. There is no
current 0.6.0-alpha high-load counter capture in this turn establishing whether
PE service capacity, radio-side backpressure or another condition sets 530 Mbps.
The previous TX DDR optimization is still an unmeasured candidate too.
