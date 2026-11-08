#!/usr/bin/env python3
"""Check routed STA EPP admission, crypto descriptors and A-MSDU ownership."""
from test_pe_downlink_gather import case

for client in (False, True):
    for lengths in ([60], [1514] * 7, [60, 61, 62, 63, 64, 65, 66]):
        for cap in (0, 7935, 10896):
            for compact in (False, True):
                case(lengths, cap, from_epp=True, compact_epp=compact, client=client)
print('PE STA/AP TX: EPP destination routing, hardware PN/CAM, A-MSDU padding and RPQ-gated BID return passed')
