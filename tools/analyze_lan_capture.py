#!/usr/bin/env python3
"""Check captured IPv4/TCP checksums and compare a known test payload."""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct


def checksum_valid(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total & 65535) + (total >> 16)
    total = (total & 65535) + (total >> 16)
    return total == 65535


def inspect(capture, payload):
    offset = 24
    counts = Counter()
    samples = []
    while offset + 16 <= len(capture):
        _, _, length, _ = struct.unpack_from("<4I", capture, offset)
        packet = capture[offset + 16:offset + 16 + length]
        offset += 16 + length
        counts["frames"] += 1
        if len(packet) < 34 or packet[12:14] != b"\x08\x00":
            continue
        counts["ipv4"] += 1
        header = (packet[14] & 15) * 4
        total = int.from_bytes(packet[16:18], "big")
        if packet[23] != 6:
            continue
        counts["tcp"] += 1
        if len(packet) < total + 14:
            counts["truncated"] += 1
            continue
        tcp = packet[14 + header:14 + total]
        pseudo = packet[26:34] + b"\0\x06" + len(tcp).to_bytes(2, "big")
        if checksum_valid(pseudo + tcp):
            continue
        counts["tcp_bad"] += 1
        if len(samples) == 10:
            continue
        data = tcp[(tcp[12] >> 4) * 4:]
        match = payload.find(data[:32]) if len(data) >= 32 else -1
        record = {"capture_offset": offset - length, "length": length,
                  "seq": int.from_bytes(tcp[4:8], "big"),
                  "ip_checksum_valid": checksum_valid(packet[14:14 + header]),
                  "payload_offset": match}
        if match >= 0:
            mismatch = [i for i, (a, b) in enumerate(zip(data, payload[match:match + len(data)])) if a != b]
            record.update(mismatched_bytes=len(mismatch),
                          first_mismatches=mismatch[:16], last_mismatches=mismatch[-16:])
            if mismatch:
                start = mismatch[0]
                record["corrupted_tail_payload_offset"] = payload.find(data[start:start + 32])
        samples.append(record)
    return {"counts": dict(counts), "samples": samples}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.capture.read_bytes(), args.payload.read_bytes()), indent=2))
