#!/usr/bin/env python3
"""Keep decrypted RX frames with Linux while the forwarding engine is off."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from check_pe_uplink_small_udp import case

directory = Path(sys.argv[1])
for slot in range(2):
    for ipv6, size in [(False, 0), (False, 1472), (True, 1452)]:
        disabled = case(directory, slot, ipv6, size, fe_ready=False)
        assert not disabled['PE_to_NI'], 'FE disabled: PE consumed a Linux RX frame'
        assert case(directory, slot, ipv6, size)['PE_to_NI']
print('Linked MIPS RX: both radios preserve payload/PN/SN for Linux with FE off; active FE forwarding remains intact')
