"""UCI string encoding and validation through the built OpenWrt libuci CLI."""
from pathlib import Path
import subprocess
import unicodedata
import os

PROJECT = Path(__file__).resolve().parents[1]


def quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def field(value, name, max_bytes):
    if not isinstance(value, str):
        raise ValueError(f'{name} must be text')
    if any(unicodedata.category(char) == 'Cc' for char in value):
        raise ValueError(f'{name} contains a control character')
    if len(value.encode('utf-8')) > max_bytes:
        raise ValueError(f'{name} exceeds {max_bytes} bytes')
    return value


def command():
    tree = Path(os.environ.get('TYFZA1_UCI_TREE', PROJECT / 'sources/openwrt'))
    found = list(tree.glob('build_dir/target-*/uci-*/uci'))
    loaders = list(tree.glob('staging_dir/toolchain-aarch64_cortex-a55_gcc-*_musl/lib/libc.so'))
    if len(found) != 1 or len(loaders) != 1:
        raise ValueError('Build the locked OpenWrt uci package before preparing private runtime configuration')
    loader = loaders[0]
    libraries = tree / 'staging_dir/target-aarch64_cortex-a55_musl/usr/lib'
    return [str(loader), '--library-path', str(libraries) + ':' + str(loader.parent), str(found[0])]


def validate(config, expected=None):
    config = Path(config)
    subprocess.run(command() + ['-c', str(config.parent), 'export', config.name],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for key, value in (expected or {}).items():
        result = subprocess.run(command() + ['-c', str(config.parent), 'get', f'{config.name}.pon.{key}'],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # libuci drops empty option values on import; the daemon likewise treats
        # an absent password as its valid twelve-byte empty password.
        if not value and result.returncode == 1 and not result.stdout:
            continue
        if result.returncode or result.stdout != value.encode('utf-8') + b'\n':
            raise ValueError(f'UCI string round-trip failed for {key}')
