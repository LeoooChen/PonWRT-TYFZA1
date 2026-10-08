#!/usr/bin/env python3
"""Execute the offload defaults and service action using actual OpenWrt UCI."""
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tools'))
from uci_config import command
base=root/'port/overlay/target/linux/rtl9617c/base-files'
defaults=(base/'etc/uci-defaults/91-tyfza1-pon-offload').read_text()
service=(base/'etc/init.d/pon').read_text()
offload=re.search(r'^offload\(\) \{.*?^\}',service,re.M|re.S)[0]
with tempfile.TemporaryDirectory(dir=root/'artifacts',prefix='pon-offload-') as work:
    work=Path(work);config=work/'config';stage=work/'stage';config.mkdir();stage.mkdir()
    cli=command()+['-c',str(config),'-t',str(stage)]
    wrapper=work/'uci';wrapper.write_text('#!/bin/sh\nexec '+shlex.join(cli)+' "$@"\n');wrapper.chmod(0o755)
    firewall=config/'firewall'
    firewall.write_text("config defaults\n option forward 'REJECT'\nconfig zone 'custom'\n option input 'DROP'\n")
    device=work/'device';device.mkdir();fe=device/'fe';fe.write_text('available=1 enabled=0\n')
    script=work/'defaults.sh';script.write_text(defaults.replace('/sys/bus/platform/devices/4f5500000.pon/fe',str(fe)))
    env={'PATH':str(work)+':/usr/bin:/bin'}
    subprocess.run(['sh',str(script)],env=env,check=True)
    get=lambda key:subprocess.check_output(cli+['get',key],text=True).strip()
    assert get('firewall.@defaults[0].flow_offloading_hw')=='1'
    assert get('firewall.@defaults[0].flow_offloading')=='1'
    assert get('firewall.@defaults[0].forward')=='REJECT' and get('firewall.custom.input')=='DROP'
    action=work/'action.sh';action.write_text(offload.replace('/sys/bus/platform/devices/4f5500000.pon',str(device))+'\noffload\n')
    subprocess.run(['sh',str(action)],env=env,check=True);assert fe.read_text()=='1\n'
    subprocess.run(cli+['set','firewall.@defaults[0].flow_offloading_hw=0'],check=True)
    subprocess.run(cli+['commit','firewall'],check=True)
    fe.write_text('available=1 enabled=1\n')
    subprocess.run(['sh',str(script)],env=env,check=True)
    assert get('firewall.@defaults[0].flow_offloading_hw')=='0'
    subprocess.run(['sh',str(action)],env=env,check=True);assert fe.read_text()=='0\n'
    subprocess.run(cli+['set','firewall.@defaults[0].flow_offloading_hw=1'],check=True)
    subprocess.run(cli+['set','firewall.@defaults[0].flow_offloading=0'],check=True)
    subprocess.run(['sh',str(action)],env=env,check=True);assert fe.read_text()=='0\n'
print('Actual UCI offload defaults/action:fresh enable,explicit disable preserved,software/hardware flags agree;firewall policy preserved')
