#!/usr/bin/env python3
"""Execute STA hardware-decrypted RX through the production linked PE."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from check_pe_uplink_small_udp import case

for client in (False, True):
    for slot in range(2):
        for ipv6, size in ((False, 0), (False, 1472), (True, 1452)):
            for amsdu in (False, True):
                assert case(sys.argv[1], slot, ipv6, size, client=client, amsdu=amsdu)['PE_to_NI']
                faults = ['receiver', 'transmitter', 'decrypt', 'replay', 'admission']
                if amsdu: faults.append('subframe')
                for options in [{'fe_ready': False}, {'wrong_direction': True}] + [
                        {'corrupt': fault} for fault in faults]:
                    assert not case(sys.argv[1], slot, ipv6, size, client=client,
                                    amsdu=amsdu, **options)['PE_to_NI']
print('PE AP/STA RX: both radios, IPv4/IPv6, Ethernet addresses, PN/SN, FE fallback and wrong-DS rejection passed')
