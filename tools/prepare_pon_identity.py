#!/usr/bin/env python3
"""Import this TYFZA1's original LLID0 MAC and MPCP parameters into UCI."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from uci_config import field, quote, validate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("stock_configuration", type=Path)
parser.add_argument("scfg", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--auth-config", type=Path, help="Private LOID and WAN protocol configuration")
args = parser.parse_args()
text = args.stock_configuration.read_bytes()
# The original MIB exports contain unescaped text elsewhere; LLID chains are XML.
chains = re.findall(br'<chain chainName="EPON_LLID_TBL">.*?</chain>', text, re.S)
first = ET.fromstring(chains[0])
mac_hex = next(node.attrib["Value"] for node in first if node.attrib.get("Name") == "macAddr")
mac_bytes = bytes.fromhex(mac_hex)
if len(mac_bytes) != 6 or mac_bytes[0] & 1 or not any(mac_bytes):
    raise ValueError("Original LLID0 MAC must be a six-byte unicast address")
mac = ":".join(f"{byte:02x}" for byte in mac_bytes)
scfg = args.scfg.read_text()
count = int(re.search(r'CFG_ID_PON_LLID_NUM\s*=\s*(0x[0-9a-fA-F]+|\d+)', scfg)[1], 0)
if not 1 <= count <= 32:
    raise ValueError("MPCP hardware supports 1..32 MAC instances")
args.output.parent.mkdir(parents=True, exist_ok=True)
options = [('enabled', '1'), ('mode', 'epon-d10g'), ('onu_mac', mac), ('llid_count', str(count))]
if args.auth_config:
    auth = json.loads(args.auth_config.read_text())
    options += [('loid', field(auth['loid'], 'LOID', 24)),
                ('loid_password', field(auth['password'], 'LOID password', 12)),
                ('wan_protocol', field(auth['network_protocol'], 'WAN protocol', 16))]
    if auth['network_protocol'] not in ('dhcp', 'pppoe', 'static'):
        raise ValueError('Unsupported WAN protocol')
args.output.touch(mode=0o600, exist_ok=True)
args.output.chmod(0o600)
args.output.write_text("config pon 'pon'\n" + ''.join(f'\toption {key} {quote(value)}\n' for key, value in options))
validate(args.output, dict(options))
record = {"identity_source": "Original EPON_LLID_TBL index0", "llid_count": count,
          "stock_configuration_sha256": hashlib.sha256(args.stock_configuration.read_bytes()).hexdigest(),
          "scfg_sha256": hashlib.sha256(args.scfg.read_bytes()).hexdigest(),
          "uci_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
args.output.with_suffix(".receipt.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps({"identity_imported": True, "llid_count": count, "output": str(args.output)}))
