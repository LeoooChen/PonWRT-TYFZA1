#!/usr/bin/env python3
"""Check native mode words/burst pages against the fixed GPL SerDes source."""
from pathlib import Path
import json
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
headers = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
reference = root / "evidence/rtl-workspace/downloads/reference/ca-network-drivers-20261005/ca-network-engine/1.0-r0/ca-network-engine-1.0/aal-77c/aal_psds.c"
text = reference.read_text()
mode_switch = text.split("static void __psds_mode_init", 1)[1].split("AAL_PON_REG_FIELD_WRITE(PSDS_MODE", 1)[0]
burst_switch = text.rsplit("static ca_status_t __psds_burst_polarity_set", 1)[1].split("AAL_PON_REG_WRITE", 1)[0]
mapping = {"epon": "EPON", "epon-d10g": "XGEPON_ASYM", "epon-bi10g": "XGEPON_SYM",
           "gpon": "GPON", "xgpon": "XGPON_ASYM_25M", "xgspon": "XGPON_SYM_25M"}
code = r'''
#include <stdio.h>
#include "rtl9617c-pon-mode.h"
int main(void) {
    for (unsigned int i=0;i<RTL_PON_MODE_COUNT;i++) {
        const struct rtl_pon_mode_configuration *c=&rtl_pon_modes[i];
        printf("%s %u %u %u %s\n",c->name,c->sds_mode,c->burst_page,c->epon_onu_mode,c->profile);
    }
}
'''
with tempfile.TemporaryDirectory(dir=root / "artifacts") as d:
    d = Path(d)
    source, binary = d / "modes.c", d / "modes"
    source.write_text(code)
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-I", str(headers), str(source), "-o", str(binary)], check=True)
    output = subprocess.check_output([str(binary)], text=True)
checks = []
for line in output.splitlines():
    name, word, page, onu, profile = line.split()
    symbol = mapping[name]
    case = mode_switch.split(f"case AAL_PSDS_MODE_{symbol}:", 1)[1].split("break;", 1)[0]
    sds = int(re.search(r"sds\s*=\s*(0x[0-9A-Fa-f]+|\d+)", case)[1], 0)
    usx = int(re.search(r"usx\s*=\s*(0x[0-9A-Fa-f]+|\d+)", case)[1], 0)
    burst = burst_switch.split(f"case AAL_PSDS_MODE_{symbol}:", 1)[1].split("break;", 1)[0]
    expected_page = int(re.search(r"reg_page\s*=\s*(\d+)", burst)[1])
    assert int(word) == 0x400 | sds | usx << 5, name
    assert int(page) == expected_page, name
    assert (root / "artifacts/pon-serdes-firmware" / Path(profile).name).exists(), profile
    checks.append({"mode": name, "word": hex(int(word)), "burst_page": int(page),
                   "profile_present": True, "MAC_registration_hardware_verified": False})
(root / "logs/pon-mode-source-comparison.json").write_text(json.dumps(checks, indent=2) + "\n")
print("6 mode words, burst pages and firmware profiles agree with the fixed GPL source")
