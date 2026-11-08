#!/bin/sh
# Run offline checks only; never changes a network interface or shared build tree.
set -eu
STA_PROJECT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STA_OUTPUT=${1:-$STA_PROJECT/artifacts/sta-offload-check}
cd "$STA_PROJECT"
mkdir -p "$STA_OUTPUT"
PE_RADIO_CFLAGS=-DPE_UPLINK sh scripts/build_pe_radio_wd.sh "$STA_OUTPUT"
for test in test_sta_client_lifecycle test_sta_beacon test_sta_scan_offload test_wifi_station_release test_wifi_notifier \
    test_radio_key_cleanup test_wifi_authorization test_fe_wifi_peer_route \
    test_wifi_offload_transaction test_wifi_ni_retirement test_radio_ni_retire_flush \
    test_rx_uplink_sync test_wifi_cmac_aggregation test_wifi_tx_request; do
    python3 "scripts/$test.py"
done
python3 scripts/test_pe_host_headers.py "$STA_OUTPUT/pe-tx.bin"
for test in test_pe_sta_rx test_pe_sta_tx test_pe_fe_fallback \
    test_pe_tx_publication test_pe_instruction_memory; do
    python3 "scripts/$test.py" "$STA_OUTPUT"
done
