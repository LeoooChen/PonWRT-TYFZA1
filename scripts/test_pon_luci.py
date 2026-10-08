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
        "mpcp": "registered=00000000 attempts=0\n"
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
    (d / 'proc').mkdir()
    (d / 'proc/uptime').write_text('100.00 150.00\n')
    process = d / 'proc/123/stat'
    process.parent.mkdir()
    process.write_text('123 (pon-oamd) ' + ' '.join(['S'] + ['0'] * 18 + ['123456'] + ['0'] * 30) + '\n')
    marker = d / 'var/run/pon-oam/llid-0.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'pid':123, 'process_start':'123456', 'updated_ms':99500,
                                 'registered':True, 'discovery_stable':True, 'auth_state':'accepted'}))
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
    assert observed['oam_ports'][0]['live'] is True, observed['oam_ports']
    assert observed['oam_ports'][0]['auth_state'] == 'accepted'
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
    assert absent['oam_ports'] == []
(root / "logs/pon-luci-status-test.json").write_text(json.dumps({"actual_rpcd_source_executed": True,
    "observed_zero_and_error_fields_preserved": True, "missing_device_is_null": True,
    "OAM_live_process_identity_checked": True, "stale_auth_result_hidden": True,
    "LuCI_hardware_UI_verified": False}, indent=2) + "\n")
print("rpcd status parsing: observed zeros/errors, live OAM identity and stale auth cases passed")
