#!/usr/bin/env python3
"""Build a candidate for the observed TYFZA1 source/FIT/bootm ABI.

Uses U-Boot mkimage and veritysetup. Signing requires an explicit key directory
and verification against an explicit bootloader key DTB. Never flashes or disables
verification, and does not imply that the real board has booted.
"""
import argparse
import datetime
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from inspect_stock_layout import DUMP_SHA256
from project_paths import source_lock, receipt
from plan_ram_boot import reg_intervals, validate
from verify_artifacts import image_info
from verify_dtb import get, children

PROJECT = Path(__file__).resolve().parents[2]
BASE_ARGS = 'earlycon console=ttyS0,115200n8 loglevel=8 ignore_loglevel clk_ignore_unused maxcpus=4'


def boot_script(mode, verity=None):
    if mode == 'ram':
        return '\n'.join([f'setenv bootargs "{BASE_ARGS}"', 'bootm ${unzip_src}', ''])
    if mode != 'ubi' or verity is None:
        raise ValueError('UBI boot requires verified rootfs verity parameters')
    if not all(re.fullmatch('[0-9a-f]{64}', verity[k]) for k in ('root_hash', 'salt')):
        raise ValueError('Invalid verity hash or salt')
    blocks = int(verity['data_blocks'])
    if blocks <= 0 or int(verity['hash_start_block']) != blocks:
        raise ValueError('Invalid no-superblock verity geometry')
    table = (f'dm-verity,,0,ro,0 {blocks * 8} verity 1 '
             '${owrt_root} ${owrt_root} 4096 4096 '
             f'{blocks} {blocks} sha256 {verity["root_hash"]} {verity["salt"]}')
    quote = chr(92) + '"'
    args = (BASE_ARGS + ' ubi.mtd=ubi_device ubi.block=0,${owrt_ubivol} '
            'root=/dev/dm-0 rootfstype=squashfs ro dm-mod.waitfor=${owrt_root} '
            'dm-mod.create=' + quote + table + quote)
    return '\n'.join([
        '# Translate only the two CRC-verified stock slot selectors.',
        'if test "${root_mtd}" = "31:9"; then',
        '    setenv owrt_ubivol ubi_r0',
        '    setenv owrt_root /dev/ubiblock0_4',
        'elif test "${root_mtd}" = "31:12"; then',
        '    setenv owrt_ubivol ubi_r1',
        '    setenv owrt_root /dev/ubiblock0_7',
        'else',
        '    echo "Unsupported TYFZA1 slot selector; refusing boot"',
        '    exit 1',
        'fi', f'setenv bootargs "{args}"', 'bootm ${unzip_src}', '',
    ])

def compress(data, parameters):
    return lzma.compress(data, format=lzma.FORMAT_ALONE, filters=[{
        'id': lzma.FILTER_LZMA1, 'dict_size': parameters['dictionary_bytes'],
        'lc': parameters['lc'], 'lp': parameters['lp'], 'pb': parameters['pb'],
    }])


def board_boot_command():
    # OEM b1/b2 hand off b_conf; older 2.5G loaders use the FIT default.
    # Direct RAM boots may not have run the OEM selector yet.
    return '''if test -z "${b_conf}" && test -n "${conf_match}"; then
    run conf_match
fi
if test -n "${b_conf}"; then
    bootm ${unzip_src}#${b_conf}
else
    bootm ${unzip_src}
fi
'''


def make_its(load, entry, fdt_load, signed=False, board_variants=False):
    signature = ('signature { algo = "sha256,rsa2048"; key-name-hint = "dev"; };'
                 if signed else '')
    config_signature = ('signature { algo = "sha256,rsa2048"; key-name-hint = "dev"; '
                        'sign-images = "fdt", "kernel", "script"; };' if signed else '')
    label = 'SIGNED' if signed else 'UNSIGNED'
    its = f'''/dts-v1/;
/ {{
    description = "TYFZA1 native {label} boot-contract candidate";
    #address-cells = <1>;
    images {{
        kernel {{
            description = "Native OpenWrt Linux {source_lock()['openwrt']['kernel']}";
            data = /incbin/("kernel.lzma");
            type = "kernel"; arch = "arm64"; os = "linux";
            compression = "lzma"; load = <{load:#x}>; entry = <{entry:#x}>;
            hash {{ algo = "sha256"; }};
            {signature}
        }};
        fdt-dts {{
            data = /incbin/("fdt.lzma");
            type = "flat_dt"; arch = "arm64";
            compression = "lzma"; load = <{fdt_load:#x}>;
            hash {{ algo = "sha256"; }};
            {signature}
        }};
        script {{
            data = /incbin/("boot.cmd");
            type = "script"; compression = "none";
            hash {{ algo = "sha256"; }};
            {signature}
        }};
    }};
    configurations {{
        default = "conf-dts";
        conf-dts {{ compatible = "realtek,taurus"; kernel = "kernel"; fdt = "fdt-dts"; script = "script"; {config_signature} }};
    }};
}};
'''
    if board_variants:
        fdt = f'''        fdt-2p5g {{
            data = /incbin/("fdt-2p5g.lzma");
            type = "flat_dt"; arch = "arm64";
            compression = "lzma"; load = <{fdt_load:#x}>;
            hash {{ algo = "sha256"; }};
            {signature}
        }};
'''
        its = its.replace('        script {', fdt + '        script {')
        its = its.replace('default = "conf-dts";', 'default = "conf-0";')
        its = its.replace('        conf-dts {',
            '        conf-0 { compatible = "realtek,taurus"; kernel = "kernel"; '
            'fdt = "fdt-2p5g"; script = "script"; ' + config_signature + ' };\n'
            '        conf-1 {')
    return its


def make_verity(source, output):
    tool = shutil.which('veritysetup')
    if not tool:
        raise ValueError('Install cryptsetup-bin to build and verify the rootfs hash tree')
    data = source.read_bytes()
    data += b'\0' * (-len(data) % 4096)
    blocks = len(data) // 4096
    salt = hashlib.sha256(data).hexdigest()
    root = output / 'rootfs.squashfs'
    hashes = output / 'rootfs.hash'
    root.write_bytes(data)
    options = ['--no-superblock', '--format=1', '--hash=sha256',
               '--data-block-size=4096', '--hash-block-size=4096', f'--salt={salt}']
    formatted = subprocess.run([tool, 'format', *options, str(root), str(hashes)],
                               check=True, capture_output=True, text=True)
    fields = dict(line.split(':', 1) for line in formatted.stdout.splitlines() if ':' in line)
    digest = fields.get('Root hash', '').strip()
    if not re.fullmatch('[0-9a-f]{64}', digest) or int(fields['Data blocks']) != blocks:
        raise ValueError('Unexpected veritysetup geometry or digest')
    with root.open('ab') as dest:
        dest.write(hashes.read_bytes())
    subprocess.run([tool, 'verify', *options, f'--data-blocks={blocks}',
                    f'--hash-offset={blocks * 4096}', str(root), str(root), digest],
                   check=True, capture_output=True, text=True)
    (output / 'veritysetup.log').write_text(formatted.stdout)
    return {'data_blocks': blocks, 'hash_start_block': blocks,
            'root_hash': digest, 'salt': salt, 'block_size': 4096,
            'verification': 'veritysetup verify passed on the merged file'}


def verify_configuration_signature(fit, key_dtb, checker):
    put, get_tool = shutil.which('fdtput'), shutil.which('fdtget')
    if not put or not get_tool:
        raise ValueError('Configuration verification requires fdtput and fdtget')
    node = '/signature/key-dev'
    algorithm = subprocess.check_output([get_tool, '-t', 's', str(key_dtb), node, 'algo'], text=True).strip()
    with tempfile.TemporaryDirectory(dir=PROJECT / 'artifacts') as work:
        work = Path(work)
        verification_key = key_dtb.resolve()
        note = ''
        if algorithm == 'sha1,rsa2048':
            # Vendor U-Boot 2020 rsa_verify_with_keynode ignores this stale label.
            # U-Boot 2026 compares it to the FIT algorithm before RSA verification.
            # This exception is ONLY the identified original TYFZA1 key DTB.
            expected = 'd72ae016b8a7438852ac0a94ef632b34d0b1981386ddfbad1202a1a6dee9a04e'
            if hashlib.sha256(key_dtb.read_bytes()).hexdigest() != expected:
                raise ValueError('Unrecognized legacy key DTB; do not normalize its policy')
            verification_key = work / 'host-key.dtb'
            shutil.copyfile(key_dtb, verification_key)
            subprocess.run([put, '-t', 's', str(verification_key), node, 'algo', 'sha256,rsa2048'],
                           check=True, capture_output=True)
            for prop in ('rsa,modulus', 'rsa,exponent', 'rsa,num-bits', 'rsa,n0-inverse',
                         'rsa,r-squared', 'required', 'key-name-hint'):
                original = subprocess.check_output([get_tool, '-t', 'bx', str(key_dtb), node, prop])
                copied = subprocess.check_output([get_tool, '-t', 'bx', str(verification_key), node, prop])
                if original != copied:
                    raise ValueError('Host key normalization changed an RSA parameter or required policy')
            note = 'Host-only legacy algo-label normalization; exact RSA key and required=conf unchanged; original DTB untouched.\n'
        elif algorithm != 'sha256,rsa2048':
            raise ValueError('Unsupported verification-key algorithm')
        result = subprocess.run([checker, '-f', str(fit.resolve()), '-k', str(verification_key)],
                                check=True, capture_output=True, text=True)
        broken = work / 'bad-signature.itb'
        shutil.copyfile(fit, broken)
        subprocess.run([put, '-t', 'bx', str(broken),
                        '/configurations/conf-dts/signature', 'value', *(['0'] * 256)],
                       check=True, capture_output=True)
        negative = subprocess.run([checker, '-f', str(broken), '-k', str(verification_key)],
                                  capture_output=True, text=True)
        if negative.returncode == 0:
            raise ValueError('fit_check_sign accepted a broken signature; use build-fit-tools.sh with TOOLS_LIBCRYPTO=y')
    return note + result.stdout + result.stderr + '\nCorrupted configuration signature: correctly rejected\n'

def verify_payload_signatures(fit, certificate):
    tools = {name: shutil.which(name) for name in ('dumpimage', 'fdtget', 'openssl')}
    if not all(tools.values()):
        raise ValueError('Payload verification needs dumpimage, fdtget and openssl')
    with tempfile.TemporaryDirectory(dir=PROJECT / 'artifacts') as work:
        work = Path(work)
        public = work / 'public.pem'
        public.write_bytes(subprocess.check_output([
            tools['openssl'], 'x509', '-in', str(certificate), '-pubkey', '-noout']))
        for index, name in enumerate(('kernel', 'fdt-dts', 'script')):
            data = work / (name + '.data')
            signature = work / (name + '.sig')
            subprocess.run([tools['dumpimage'], '-T', 'flat_dt', '-p', str(index),
                            '-o', str(data), str(fit)], check=True, capture_output=True)
            def binary_property(node, prop):
                text = subprocess.check_output([
                    tools['fdtget'], '-t', 'bx', str(fit), node, prop], text=True)
                return bytes(int(value, 16) for value in text.split())
            if hashlib.sha256(data.read_bytes()).digest() != binary_property('/images/' + name + '/hash', 'value'):
                raise ValueError('FIT payload hash mismatch: ' + name)
            signature.write_bytes(binary_property('/images/' + name + '/signature', 'value'))
            subprocess.run([tools['openssl'], 'dgst', '-sha256', '-verify', str(public),
                            '-signature', str(signature), str(data)],
                           check=True, capture_output=True)
    return 'kernel, fdt-dts and script SHA256/RSA signatures independently verified'


def build(image, dtb, output, mode, rootfs=None, key_dir=None, key_dtb=None):
    if output.exists():
        raise ValueError('Output directory already exists')
    contract = json.loads((receipt('tyfza1-boot-compression.json')).read_text())
    layout = json.loads((receipt('tyfza1-stock-layout.json')).read_text())
    volumes = json.loads((receipt('tyfza1-stock-volume-ids.json')).read_text())['volumes']
    if layout['dump_sha256'] != DUMP_SHA256 or not layout['factory_translation_is_identity']:
        raise ValueError('This candidate only supports the verified zero-factory-BBL fixture')
    info = image_info(image)
    if (mode == 'ram') != ('initramfs' in image.name):
        raise ValueError('RAM needs initramfs Image; UBI needs the ordinary Image')
    if 'realtek,rtl9617c' not in get(dtb, '/', 'compatible', 's'):
        raise ValueError('Not a native RTL9617C DTB')
    by_name = {v['name']: v for v in volumes}
    if by_name['ubi_r0']['volume_id'] != 4 or by_name['ubi_r1']['volume_id'] != 7:
        raise ValueError('Stock volume IDs no longer match the slot translation')
    if mode == 'ubi':
        if rootfs is None:
            raise ValueError('UBI mode requires the matching native SquashFS rootfs')
        with rootfs.open('rb') as stream:
            if stream.read(4) != b'hsqs':
                raise ValueError('UBI mode requires the matching native SquashFS rootfs')
        if rootfs.stat().st_size > min(by_name[n]['reserved_bytes'] for n in ('ubi_r0', 'ubi_r1')):
            raise ValueError('Rootfs exceeds the observed volume capacity')
        flash = '/soc/spi@4f4324000/flash@0'
        get(dtb, flash, 'read-only')
        parts = flash + '/partitions'
        actual = [(get(dtb, parts + '/' + n, 'label', 's')[0],
                   get(dtb, parts + '/' + n, 'reg')) for n in children(dtb, parts)]
        expected = [(p['name'], [p['offset'], p['size']]) for p in layout['partitions']]
        if actual != expected:
            raise ValueError('UBI candidate needs the verified stock read-only partition layout')
    stock = {v['name']: v for v in contract['images']}
    load, entry = (int(stock['kernel'][k][0], 0) for k in ('load', 'entry'))
    fdt_load = int(stock['fdt-dts']['load'][0], 0)
    if mode == 'ram':
        load = entry = 0x200000
        fdt_load = 0x1800000
    memory = reg_intervals(get(dtb, '/memory@0', 'reg'))
    reserved = [r for n in children(dtb, '/reserved-memory')
                for r in reg_intervals(get(dtb, '/reserved-memory/' + n, 'reg'))]
    # The original kernel loads its transient Image header at zero, inside the
    # VoIP vector reservation. Do not generalize this exception to any region.
    # This reproduces the observed loader ABI, NOT a verified live placement.
    reserved = [r for r in reserved if not (load == 0 and r == (0, 0x1000))]
    validate(memory, reserved, load, max(info['bytes'], info['image_size']),
             info['text_offset'], fdt_load, dtb.stat().st_size)
    tool = shutil.which('mkimage')
    if not tool:
        raise ValueError('Put the pinned OpenWrt staging_dir/host/bin on PATH')
    checker = shutil.which('fit_check_sign')
    if key_dir is not None and (key_dtb is None or checker is None):
        raise ValueError('Signing requires a bootloader key DTB and fit_check_sign on PATH')
    if key_dtb is not None and key_dir is None:
        raise ValueError('A key DTB alone does not supply signing material')
    output.mkdir(parents=True)
    verity = make_verity(rootfs, output) if mode == 'ubi' else None
    if verity and (output / 'rootfs.squashfs').stat().st_size > min(by_name[n]['reserved_bytes'] for n in ('ubi_r0', 'ubi_r1')):
        raise ValueError('Verity-protected rootfs exceeds the observed volume capacity')
    (output / 'kernel.lzma').write_bytes(compress(image.read_bytes(), stock['kernel']['lzma_alone']))
    (output / 'fdt.lzma').write_bytes(compress(dtb.read_bytes(), stock['fdt-dts']['lzma_alone']))
    (output / 'boot.cmd').write_text(boot_script(mode, verity), encoding='ascii')
    (output / 'image.its').write_text(make_its(load, entry, fdt_load, key_dir is not None), encoding='ascii')
    source = source_lock()['openwrt']
    env = dict(os.environ)
    env['SOURCE_DATE_EPOCH'] = str(int(datetime.datetime.fromisoformat(source['commit_date']).timestamp()))
    command = [tool, '-f', 'image.its']
    if key_dir is not None:
        command += ['-k', str(key_dir.resolve())]
    built = subprocess.run(command + ['image.itb'], cwd=output, env=env, check=True,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
    (output / 'mkimage.log').write_text(built.stdout + built.stderr)
    fit = output / 'image.itb'
    if mode == 'ubi' and fit.stat().st_size > min(by_name[n]['reserved_bytes'] for n in ('ubi_k0', 'ubi_k1')):
        raise ValueError('FIT exceeds the actual kernel volume capacity, not an environment size guess')
    if key_dir is not None:
        checked = verify_configuration_signature(fit, key_dtb, checker)
        (output / 'signature-check.log').write_text(checked)
        verify_payload_signatures(fit, key_dir / 'dev.crt')
    result = {'mode': mode, 'openwrt_commit': source['commit'], 'kernel': info,
              'dtb_sha256': hashlib.sha256(dtb.read_bytes()).hexdigest(),
              'fit_sha256': hashlib.sha256(fit.read_bytes()).hexdigest(),
              'signature': ('RSA2048 image/configuration signatures verified against supplied bootloader DTB'
                            if key_dir is not None else 'UNSIGNED; does not satisfy required=conf'),
              'signature_verified': key_dir is not None,
              'payload_signatures_verified': key_dir is not None,
              'corrupted_configuration_signature_rejected': key_dir is not None,
              'host_only_legacy_key_label_compatibility': key_dir is not None and hashlib.sha256(key_dtb.read_bytes()).hexdigest() == 'd72ae016b8a7438852ac0a94ef632b34d0b1981386ddfbad1202a1a6dee9a04e',
              'key_dtb_sha256': hashlib.sha256(key_dtb.read_bytes()).hexdigest() if key_dir is not None else None,
              'verity': verity,
              'rootfs_sha256': hashlib.sha256((output / 'rootfs.squashfs').read_bytes()).hexdigest() if verity else None,
              'live_load_addresses_verified': False, 'hardware_boot_tested': False,
              'persistent_installation_approved': False}
    (output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('dtb', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--mode', choices=('ram', 'ubi'), required=True)
    parser.add_argument('--rootfs', type=Path)
    parser.add_argument('--key-dir', type=Path, help='explicit dev.key/dev.crt directory; never exported')
    parser.add_argument('--key-dtb', type=Path, help='bootloader DTB used for signature verification')
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.image, args.dtb, args.output, args.mode, args.rootfs, args.key_dir, args.key_dtb), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'FIT candidate rejected: {error}\n')
