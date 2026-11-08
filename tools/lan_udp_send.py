#!/usr/bin/env python3
"""Send sequenced UDP frames with a selected rate to the native TYFZA1 LAN."""
import argparse
import json
import socket
import struct
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--count", type=int, default=10000)
parser.add_argument("--pps", type=int, default=1000)
args = parser.parse_args()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("192.168.1.170", 0))
start = time.monotonic()
for sequence in range(args.count):
    sock.sendto(struct.pack("!I", sequence) * 350, ("192.168.1.1", 18081))
    if args.pps:
        delay = start + (sequence + 1) / args.pps - time.monotonic()
        if delay > 0:
            time.sleep(delay)
print(json.dumps({"sent": args.count, "packet_bytes": 1400, "pps": args.pps,
                  "seconds": round(time.monotonic() - start, 3)}))
