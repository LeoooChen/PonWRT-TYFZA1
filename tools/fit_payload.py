"""Read the selected FIT payload and its actual uncompressed newc initramfs."""
import hashlib
import lzma
from pathlib import Path
import stat
import subprocess
import tempfile
import zlib

PROJECT = Path(__file__).resolve().parents[1]


def property_value(fit, node, name, kind='s'):
    return subprocess.check_output(['fdtget', '-t', kind, str(fit), node, name], text=True).split()


def fit_images(fit, configuration=None):
    fit = Path(fit)
    config = configuration or property_value(fit, '/configurations', 'default')[0]
    names = subprocess.check_output(['fdtget', '-l', str(fit), '/images'], text=True).split()
    result = {}
    with tempfile.TemporaryDirectory(prefix='fit-read-', dir=PROJECT / 'artifacts') as temporary:
        for role in ('kernel', 'fdt', 'script'):
            name = property_value(fit, '/configurations/' + config, role)[0]
            path = Path(temporary) / role
            subprocess.run(['dumpimage', '-T', 'flat_dt', '-p', str(names.index(name)),
                            '-o', str(path), str(fit)], check=True, capture_output=True)
            packed = path.read_bytes()
            node = '/images/' + name
            if property_value(fit, node + '/hash', 'algo') != ['sha256']:
                raise ValueError(f'Unsupported FIT hash for {role}')
            digest = bytes(int(value, 16) for value in property_value(fit, node + '/hash', 'value', 'bx'))
            if hashlib.sha256(packed).digest() != digest:
                raise ValueError(f'FIT {role} data hash mismatch')
            compression = property_value(fit, node, 'compression')[0]
            if compression == 'lzma':
                unpacked = lzma.decompress(packed, format=lzma.FORMAT_ALONE)
            elif compression == 'none':
                unpacked = packed
            else:
                raise ValueError(f'Unsupported FIT compression: {compression}')
            result[role] = unpacked
    return result


def newc(data, start):
    position = start
    files = {}
    hardlinks = {}
    while True:
        header = data[position:position + 110]
        if len(header) != 110 or header[:6] not in (b'070701', b'070702'):
            raise ValueError('Incomplete newc archive')
        fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        inode, mode, uid, gid, links, mtime, size, major, minor, rmajor, rminor, namesize, checksum = fields
        if not namesize:
            raise ValueError('Empty newc name')
        name_end = position + 110 + namesize
        raw_name = data[position + 110:name_end]
        if len(raw_name) != namesize or raw_name[-1:] != b'\0':
            raise ValueError('Truncated newc name')
        name = raw_name[:-1].decode('utf-8').removeprefix('./').lstrip('/')
        content_start = start + ((name_end - start + 3) & ~3)
        content_end = content_start + size
        if content_end > len(data):
            raise ValueError('Truncated newc file')
        content = data[content_start:content_end]
        if header[:6] == b'070702' and sum(content) & 0xffffffff != checksum:
            raise ValueError('newc checksum mismatch')
        position = start + ((content_end - start + 3) & ~3)
        if name == 'TRAILER!!!':
            break
        entry = {'data': content, 'mode': mode, 'uid': uid, 'gid': gid}
        files[name] = entry
        if links > 1 and stat.S_ISREG(mode):
            key = (major, minor, inode)
            hardlinks.setdefault(key, []).append(entry)
    for entries in hardlinks.values():
        contents = [entry['data'] for entry in entries if entry['data']]
        if contents:
            for entry in entries:
                entry['data'] = contents[-1]
    return files, position


def embedded_rootfs(image):
    # The pinned target uses CONFIG_INITRAMFS_COMPRESSION_NONE. Parse complete
    # archives rather than accepting the kernel decompressor's magic strings.
    roots = []
    position = 0
    while True:
        position = image.find(b'070701', position)
        if position < 0:
            break
        try:
            files, end = newc(image, position)
        except (ValueError, UnicodeDecodeError):
            position += 6
            continue
        if 'etc/config/pon' in files and 'sbin/procd' in files:
            roots.append((files, position, end))
        position = end
    position = 0
    while True:
        position = image.find(b'\x1f\x8b\x08', position)
        if position < 0:
            break
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = decoder.decompress(image[position:], 64 * 1024 * 1024)
            if not decoder.eof:
                raise ValueError('Incomplete or oversized gzip initramfs')
            files, end = newc(data, 0)
        except (ValueError, UnicodeDecodeError, zlib.error):
            position += 3
            continue
        packed_end = len(image) - len(decoder.unused_data)
        if 'etc/config/pon' in files and 'sbin/procd' in files:
            roots.append((files, position, packed_end))
        position = packed_end
    if len(roots) != 1:
        raise ValueError(f'Expected one actual PON initramfs, found {len(roots)}')
    return roots[0]
