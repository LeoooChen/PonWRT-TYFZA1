#!/usr/bin/env python3
"""Execute the installed rpcd status source against observed/missing sysfs data."""
from pathlib import Path
import json
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
plugin = root / "port/packages/luci-app-tyfza1-pon/root/usr/share/rpcd/ucode/luci.tyfza1-pon"
ucode = root / "sources/openwrt/build_dir/hostpkg/ucode-2026.07.09~b885dd0f/ucode"
source = plugin.read_text().split("\n", 1)[1].replace("import { readfile } from 'fs';", "import { readfile as fixture_readfile } from 'fs';\nfunction readfile(path) { return fixture_readfile(FIXTURE + path); }")
with tempfile.TemporaryDirectory(dir=root / "artifacts") as d:
    d = Path(d)
    device = d / "sys/bus/platform/devices/4f5500000.pon"
    device.mkdir(parents=True)
    samples = {
        "state": "family=epon signal_loss=1 pcs10g_sync=0 pcs1g_sync=0 registered_llids=00000000\n",
        "serdes": "mode=epon-d10g configured=1 error=0 rx_los=1\n",
        "bosa": "configured=0 model=205 eeprom=0 writes=599 error=-6 identity=47/39/37\n",
        "traffic": "enabled=1 rx=0 tx=0 carrier=0\n",
        "mpcp": "registered=00000000 attempts=0\n",
        "fe": "available=1 enabled=1 prepared=1 error=0 flows=2 counter_slots=64 installs=8 deletes=6\n",
        "data_path": "configured=1 error=0 epoch=1 paths=2 session=1 profiles=0\ngem=100 index=0 vlan=100\ngem=200 index=1 vlan=200\n"
    }
    for name, value in samples.items():
        (device / name).write_text(value)
    deepq = d / 'sys/bus/platform/devices/4f4300000.ethernet/pon_deepq'
    deepq.parent.mkdir(parents=True)
    deepq.write_text('configured=1 buffers_per_pool=512 map=0000000a\n')
    for name, value in (('rx_packets', '0'), ('tx_packets', '17')):
        counter = d / 'sys/class/net/pon-data0/statistics' / name
        counter.parent.mkdir(parents=True, exist_ok=True)
        counter.write_text(value + '\n')
    for name in ("rx_errors", "rx_dropped"):
        p = d / "sys/class/net/eth0/statistics" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("0\n")
    for name, value in (('ifindex', '2'), ('carrier', '1'), ('speed', '1000'),
                        ('duplex', 'full'), ('address', '02:00:00:00:00:01')):
        (d / 'sys/class/net/eth0' / name).write_text(value + '\n')
    (d / 'proc').mkdir()
    (d / 'proc/uptime').write_text('100.00 150.00\n')
    process = d / 'proc/123/stat'
    process.parent.mkdir()
    process.write_text('123 (pon-oamd) ' + ' '.join(['S'] + ['0'] * 18 + ['123456'] + ['0'] * 30) + '\n')
    marker = d / 'var/run/pon-oam/llid-0.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'pid':123, 'process_start':'123456', 'updated_ms':99500,
                                 'registered':True, 'discovery_stable':True, 'auth_state':'accepted'}))
    omci_process = d / 'proc/124/stat'
    omci_process.parent.mkdir()
    omci_process.write_text('124 (pon-omcid) ' + ' '.join(['S'] + ['0'] * 18 + ['123457'] + ['0'] * 30) + '\n')
    omci_marker = d / 'var/run/pon-omci'
    omci_marker.write_text('pid=124 process_start=123457 updated_ms=99500 rx=12 tx=9 invalid=0 paths=2 error=-22\n')
    program = d / "status.uc"
    program.write_text("const FIXTURE = " + json.dumps(str(d)) + ";\n" +
                       source.replace("return { 'luci.tyfza1-pon': methods };",
                                      "print(sprintf('%J', methods.status.call()));"))
    observed = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert observed["serdes"]["rx_los"] == "1"
    assert observed["mpcp"]["registered"] == "00000000"
    assert observed["traffic"]["rx"] == "0"
    assert observed["bosa"]["error"] == "-6"
    assert observed["lan_rx_errors"] == "0"
    assert observed['deepq']['configured'] == '1' and observed['wan_rx_packets'] == '0'
    assert observed['wan_tx_packets'] == '17'
    assert observed['fe']['enabled'] == '1' and observed['fe']['flows'] == '2'
    assert observed['oam_ports'][0]['live'] is True, observed['oam_ports']
    assert observed['oam_ports'][0]['auth_state'] == 'accepted'
    assert observed['omci']['live'] is True and observed['omci']['error'] == '-22'
    assert observed['data_path']['paths'] == '2' and 'gem' not in observed['data_path']
    assert observed['data_path_raw'] == samples['data_path']
    omci_marker.write_text(omci_marker.read_text().replace('updated_ms=99500', 'updated_ms=100009'))
    current = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert current['omci']['live'] is True
    omci_marker.write_text(omci_marker.read_text().replace('updated_ms=100009', 'updated_ms=100010'))
    future = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert future['omci']['live'] is False
    omci_marker.write_text(omci_marker.read_text().replace('updated_ms=100010', 'updated_ms=99500'))
    (d / 'proc/uptime').write_text('106.00 150.00\n')
    expired = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert expired['omci']['live'] is False and expired['omci']['error'] == '-22'
    (d / 'proc/uptime').write_text('100.00 150.00\n')
    omci_process.write_text(omci_process.read_text().replace('123457', '654321'))
    reused = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert reused['omci']['live'] is False
    omci_process.unlink()
    stopped = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert stopped['omci']['live'] is False
    omci_marker.unlink()
    assert observed['ports'][0]['present'] is True
    assert observed['ports'][0]['carrier'] == '1'
    assert observed['ports'][0]['speed'] == '1000'
    assert observed['ports'][0]['statistics']['rx_errors'] == '0'
    assert observed['ports'][1]['present'] is False
    assert observed['ports'][1]['carrier'] is None
    assert observed['ports'][1]['statistics']['rx_bytes'] is None
    malformed = marker.with_name('llid-1.json')
    for invalid in ('{', 'null', '[]', '{"pid":"../1"}'):
        malformed.write_text(invalid)
        result = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
        assert len(result['oam_ports']) == 1
    malformed.unlink()
    process.write_text('123 (another-process) ' + ' '.join(['S'] + ['0'] * 18 + ['654321'] + ['0'] * 30) + '\n')
    stale = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert stale['oam_ports'][0]['live'] is False
    assert stale['oam_ports'][0]['auth_state'] is None
    marker.unlink()
    for path in device.iterdir():
        path.unlink()
    deepq.unlink()
    absent = json.loads(subprocess.check_output([str(ucode), str(program)], text=True))
    assert absent["state"] is None and absent["serdes"] is None and absent["mpcp"] is None
    assert absent["bosa"] is None and absent["traffic"] is None
    assert absent['fe'] is None
    assert absent['oam_ports'] == []
    assert absent['omci'] is None and absent['data_path_raw'] is None
(root / "logs/pon-luci-status-test.json").write_text(json.dumps({"actual_rpcd_source_executed": True,
    "observed_zero_and_error_fields_preserved": True, "missing_device_is_null": True,
    "OAM_live_process_identity_checked": True, "stale_auth_result_hidden": True,
    "LuCI_hardware_UI_verified": False}, indent=2) + "\n")
print("rpcd status: OAM/OMCI process identity and freshness, missing status and complete multi-GEM diagnostics passed")
