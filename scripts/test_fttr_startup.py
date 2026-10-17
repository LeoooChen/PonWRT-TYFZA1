#!/usr/bin/env python3
"""Run factory parameter import and the real hardware-start script in isolated files."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True, help='Native host build of fttrctl')
    parser.add_argument('--ucode', type=Path, required=True)
    parser.add_argument('--ucode-modules', type=Path, required=True)
    parser.add_argument('--factory', type=Path, required=True, help='Original read-only config directory')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        source, imported = path / 'factory', path / 'imported'
        source.mkdir()
        imported.mkdir()
        for name in ('config_hs.xml','config.xml','rtkbosa_k.bin','rtkoltbosa_k.bin'):
            if (args.factory / name).exists():
                shutil.copy2(args.factory / name, source / name)
        hs = source.joinpath('config_hs.xml').read_bytes()
        cs = source.joinpath('config.xml').read_bytes()

        def field(data, key):
            return re.search(rb'<Value\s+Name="' + key.encode() + rb'"\s+Value="([^"]*)"', data)[1].decode()

        expected = ' '.join([field(cs,'FTTR_MASTER_PON_ENABLE'),
            field(hs,'EXTERNAL_VIRTUAL_RGMII_PORT_SERDES_MODE'),
            field(hs,'EXTERNAL_VIRTUAL_RGMII_PORT_SERDES_NWAY'),
            field(cs,'FTTR_MASTER_OLT_PON_SERDES_MODE')]) + '\n'
        command = [str(args.ucode),'-L',str(args.ucode_modules),
            str(root/'port/packages/tyfza1-pon-control/files/factory.uc'),str(source),str(imported)]
        result = subprocess.run(command,capture_output=True,text=True)
        settings = imported/'tmp/tyfza1-factory/fttr_startup'
        assert settings.read_text() == expected
        assert 'Factory FTTR startup:' not in result.stderr
        source.joinpath('config.xml').write_bytes(cs.replace(b'Name="FTTR_MASTER_PON_ENABLE"',b'Name="TEST_REMOVED_FTTR_ENABLE"'))
        subprocess.run(command,capture_output=True,text=True)
        assert not settings.exists()

        miniolt, ni, optical, mock = [path/name for name in ('miniolt','ni','optical','bin')]
        for target in (miniolt,ni,optical,mock):
            target.mkdir()
        address = path/'address'
        address.write_text('02:11:22:33:44:55\n')
        script = path/'fttr-hardware'
        script.write_text((root/'port/packages/tyfza1-fttr/files/fttr-hardware').read_text().replace(
            '/sys/class/net/fttr0/address',str(address)))
        mock.joinpath('ip').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FTTR_TEST_IP_LOG"\n')
        mock.joinpath('ip').chmod(0o755)
        mock.joinpath('fttrctl').write_text('''#!/usr/bin/python3
import os,sys
from pathlib import Path
miniolt,ni,optical = [Path(os.environ['FTTR_TEST_'+n]) for n in ('MINIOLT','NI','OPTICAL')]
assert sys.argv[2]=='wait-link'
assert miniolt.joinpath('tx_enable').read_text()=='0\\n'
assert miniolt.joinpath('reset').read_text()=='1\\n'
assert miniolt.joinpath('host_serdes').read_text()=='9 1\\n'
assert ni.joinpath('fttr_link').read_text()=='9 1\\n'
assert miniolt.joinpath('pon_serdes').read_text()=='3\\n'
assert optical.joinpath('calibration').read_text()=='1\\n'
assert miniolt.joinpath('core_init').read_text()=='0\\n'
assert miniolt.joinpath('dba').read_text()=='0\\n'
mode=os.environ['FTTR_TEST_MODE']
link=0 if mode=='link-error' else 1
miniolt.joinpath('host_serdes').write_text(f'mode=9 nway=1 pll=1 error=0 link={link}\\n')
ni.joinpath('fttr_link').write_text(f'configured=1 mode=9 nway=1 clock=1 error=0 link={link}\\n')
optical.joinpath('calibration').write_text('configured=0 model=GN25L99 marker=00 writes=128 error=0\\n' if mode=='calibration-error' else 'configured=1 model=GN25L99 marker=b0 writes=128 error=0\\n')
os.execv(os.environ['FTTR_TEST_CONTROL'],[os.environ['FTTR_TEST_CONTROL']]+sys.argv[1:])
''')
        mock.joinpath('fttrctl').chmod(0o755)
        env = dict(os.environ,PATH=str(mock)+':'+os.environ['PATH'],FTTR_TEST_CONTROL=str(args.control.resolve()),
            FTTR_TEST_MINIOLT=str(miniolt),FTTR_TEST_NI=str(ni),FTTR_TEST_OPTICAL=str(optical),
            FTTR_TEST_IP_LOG=str(path/'ip.log'))
        for mode in ('ready','link-error','calibration-error'):
            miniolt.joinpath('backend').write_text('asic9690\n')
            for name in ('tx_enable','reset','host_serdes','pon_serdes','core_init','omci_link','dba'):
                miniolt.joinpath(name).write_text('0\n')
            ni.joinpath('fttr_link').write_text('0 0\n')
            optical.joinpath('calibration').write_text('0\n')
            path.joinpath('ip.log').write_text('')
            env['FTTR_TEST_MODE'] = mode
            result = subprocess.run(['sh',str(script),'start',str(miniolt),str(ni),str(optical),'9','1','3'],
                env=env,capture_output=True,text=True)
            assert result.stdout.count('reset MiniOLT') == 1
            if mode == 'ready':
                assert result.returncode == 0, result.stderr
                assert miniolt.joinpath('tx_enable').read_text() == '1\n'
                assert miniolt.joinpath('core_init').read_text() == '1\n'
                assert miniolt.joinpath('dba').read_text() == '1\n'
                assert miniolt.joinpath('omci_link').read_text() == address.read_text()
                assert path.joinpath('ip.log').read_text().splitlines() == [
                    'link set dev fttr0 down','link set dev fttr0 mtu 1982 up']
                subprocess.run(['sh',str(script),'stop',str(miniolt),str(ni)],env=env,capture_output=True,text=True,check=True)
                assert miniolt.joinpath('tx_enable').read_text() == '0\n'
                assert ni.joinpath('fttr_link').read_text() == '0 0\n'
            else:
                assert result.returncode != 0
                assert 'startup complete' not in result.stdout
                assert miniolt.joinpath('tx_enable').read_text() == '0\n'
                assert path.joinpath('ip.log').read_text().splitlines() == ['link set dev fttr0 down']
                if mode == 'link-error':
                    assert '(-110)' in result.stderr
                    assert miniolt.joinpath('core_init').read_text() == '0\n'
        descriptor = os.open(miniolt,os.O_RDONLY|os.O_DIRECTORY)
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result = subprocess.run(['sh',str(script),'stop',str(miniolt),str(ni)],env=env,capture_output=True,text=True)
        os.close(descriptor)
        assert result.returncode != 0 and 'MiniOLT is busy' in result.stderr
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'factory_mode_imported':True,'missing_mode_revokes_previous_file':True,
        'hardware_command_order_verified':True,'real_wait_link_command_used':True,
        'link_and_calibration_errors_do_not_enable_tx':True,'stop_disables_tx_and_p6':True,
        'device_lock_serializes_commands':True,'scope':'temporary files and modeled hardware status; no live MiniOLT',
        'hardware_verified':False},indent=2)+'\n')
    print('Startup: factory settings, ordered commands, real PCS wait and TX failure/stop behavior passed')


if __name__ == '__main__':
    main()
