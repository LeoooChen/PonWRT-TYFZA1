#!/usr/bin/env python3
"""Compare original SMUX tag operations with the native Linux VLAN/bridge path."""
import argparse
import errno
import hashlib
import json
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import time

from arm64_module_oracle import ModuleOracle
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP


HARNESS = r'''
#define _DEFAULT_SOURCE
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "fttr-net.h"
int main(void)
{
    struct fttr_net net = { .fd = -1 };
    struct fttr_net_port port = { 0 };
    uint8_t serial[8] = { 'T', 'E', 'S', 'T', 0, 0, 0, 7 };
    char command[16];
    int ret = fttr_net_open(&net, "fttr0");
    if (ret) return 1;
    while (scanf("%15s", command) == 1) {
        if (!strcmp(command, "create"))
            ret = fttr_net_create(&net, &port, 7, serial, 263, "br-test");
        else if (!strcmp(command, "remove")) ret = fttr_net_remove(&net, &port);
        else if (!strcmp(command, "reset")) ret = fttr_net_reset(&net, 7);
        else if (!strcmp(command, "verify")) ret = fttr_net_verify(&net, &port);
        else return 2;
        printf("%d %d %d\n", ret, port.index, port.configured);
        fflush(stdout);
    }
    close(net.fd);
    return 0;
}
'''


class OriginalTags:
    def __init__(self, module):
        self.oracle = ModuleOracle(module,
            ['smuxdev_vlan_push.part.0', 'smuxdev_vlan_pop.part.0'], self.handler)
        self.skb = self.oracle.scratch + 0x100
        self.head = self.oracle.scratch + 0x1000

    def handler(self, name, args):
        o = self.oracle
        if name == 'memmove':
            o.uc.mem_write(args[0], bytes(o.uc.mem_read(args[1], args[2])))
            return args[0]
        if name in ('skb_push', 'skb_pull_rcsum'):
            assert args[0] == self.skb
            data = o.unpack(self.skb + 200, 'Q')[0]
            data += -args[1] if name == 'skb_push' else args[1]
            o.word(self.skb + 200, data)
            return data
        raise AssertionError(('unmodeled original tag operation', name))

    def transform(self, frame, push, vid=263):
        o = self.oracle
        o.uc.mem_write(self.skb, bytes(256))
        o.word(self.skb + 192, self.head)
        # RX already consumed ethhdr; original pop's direction0 branch moves
        # the MAC addresses relative to skb->data - ETH_HLEN.
        o.word(self.skb + 200, self.head + 128 + (0 if push else 14))
        o.uc.mem_write(self.skb + 178, struct.pack('<H', 128))
        o.uc.mem_write(self.head + 128, frame)
        if push:
            o.call('smuxdev_vlan_push.part.0', 1, self.skb, 1,
                   socket.htons(0x8100), socket.htons(vid))
        else:
            o.call('smuxdev_vlan_pop.part.0', 0, self.skb, 1)
        data = self.head + o.unpack(self.skb + 178, 'H')[0]
        return bytes(o.uc.mem_read(data, len(frame) + (4 if push else -4)))


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def packet_socket(interface):
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sock.setsockopt(263, 8, 1)  # PACKET_AUXDATA: reconstruct a veth RX-offloaded tag.
    sock.bind((interface, 0))
    sock.setblocking(False)
    return sock


def receive(sock, marker, seconds=1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([sock], [], [], max(0, deadline-time.monotonic()))
        if not ready:
            break
        data, aux, _, address = sock.recvmsg(4096, 128)
        if address[2] == socket.PACKET_OUTGOING or marker not in data:
            continue
        for level, kind, payload in aux:
            if level == 263 and kind == 8:
                status, _, _, _, _, tci, tpid = struct.unpack('=IIIHHHH', payload[:20])
                if status & (1 << 4):
                    data = data[:12] + struct.pack('>HH', tpid if status & (1 << 6) else 0x8100, tci) + data[12:]
        return data
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rootfs', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    assert json.loads(subprocess.check_output(['ip', '-j', 'link'], text=True))[0]['ifname'] == 'lo'
    assert len(json.loads(subprocess.check_output(['ip', '-j', 'link'], text=True))) == 1
    Path('/proc/sys/net/ipv6/conf/default/disable_ipv6').write_text('1\n')
    original = OriginalTags(args.rootfs/'lib/modules/smuxdev.ko')
    # Execute only the caller's straight-line VID assignment; it has no external
    # references. Other executable sections contain unrelated COPY relocations.
    caller = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    caller.mem_map(0x407000, 4096)
    caller.mem_map(0x20000000, 0x200000)
    with (args.rootfs/'bin/onuinfo').open('rb') as file:
        elf = ELFFile(file)
        text = elf.get_section_by_name('.text')
        offset = 0x40754c-text['sh_addr']
        caller.mem_write(0x40754c, text.data()[offset:offset+24])
    for onu in (0, 7, 63):
        sp, context = 0x201ff000, 0x20001000
        caller.reg_write(UC_ARM64_REG_SP, sp)
        caller.mem_write(sp+44, struct.pack('<I', onu+1))
        caller.mem_write(sp+544, struct.pack('<Q', context))
        caller.emu_start(0x40754c, 0x407564, count=100)
        assert caller.reg_read(UC_ARM64_REG_PC) == 0x407564
        assert struct.unpack('<H', caller.mem_read(context+40, 2))[0] == 256+onu
    run('ip', 'link', 'add', 'fttr0', 'type', 'veth', 'peer', 'name', 'wire')
    run('ip', 'link', 'add', 'lan0', 'type', 'veth', 'peer', 'name', 'lan-peer')
    run('ip', 'link', 'add', 'br-test', 'type', 'bridge')
    run('ip', 'link', 'set', 'lan0', 'master', 'br-test')
    # A full 1500-byte customer payload plus two customer tags needs eight
    # bytes of LAN headroom. This is an explicit bridge/network MTU choice.
    for name in ('fttr0', 'wire', 'lan0', 'lan-peer', 'br-test'):
        run('ip', 'link', 'set', name, 'mtu', '1982' if name in ('fttr0', 'wire') else '1508', 'up')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = Path(__file__).resolve().parents[1]/'port/packages/tyfza1-fttr/src'
        (root/'harness.c').write_text(HARNESS)
        run('cc', '-O2', '-Wall', '-Wextra', '-Werror', '-I'+str(source),
            str(root/'harness.c'), str(source/'fttr-net.c'), '-o', str(root/'harness'))
        process = subprocess.Popen([str(root/'harness')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        def command(text):
            process.stdin.write(text+'\n'); process.stdin.flush()
            return tuple(map(int, process.stdout.readline().split()))
        def links():
            return {x['ifname']: x for x in json.loads(subprocess.check_output(['ip', '-d', '-j', 'link'], text=True))}
        wire, lan = packet_socket('wire'), packet_socket('lan-peer')
        cases = []
        try:
            assert command('create')[0] == 0
            child = links()['fttr-u7']
            assert child['linkinfo']['info_data']['id'] == 263 and child['master'] == 'br-test'
            assert child['linkinfo']['info_data']['protocol'] == '802.1Q' and child['mtu'] == 1508
            for tags in (b'', bytes.fromhex('8100a07b'), bytes.fromhex('88a800638100a07b')):
                marker = b'FTTR-native-datapath'+bytes([len(tags)])+bytes(range(48))
                payload = marker.ljust(1500, b'x') if len(tags) == 8 else marker
                up = bytes.fromhex('0200000000b10200000000a1')+tags+b'\x88\xb5'+payload
                tagged = original.transform(up, True)
                assert tagged == up[:12]+bytes.fromhex('81000107')+up[12:]
                lan.send(up)
                assert receive(wire, marker) == tagged
                down = up[6:12]+up[:6]+up[12:]
                downstream = original.transform(down, True)
                wire.send(downstream)
                assert receive(lan, marker) == original.transform(downstream, False) == down
                assert receive(wire, marker, 0.05) is None  # No reflection to the ingress ONU.
                cases.append({'customer_tags': len(tags)//4, 'bytes': len(up), 'both_directions': True})
            marker = b'FTTR-unmapped-GEM'
            unrelated = bytes.fromhex('0200000000a10200000000b18100010888b5')+marker.ljust(64, b'x')
            wire.send(unrelated)
            assert receive(lan, marker, 0.1) is None
            # The bridge must forget learned ONU MACs when its service port is removed.
            fdb = json.loads(subprocess.check_output(['bridge', '-j', 'fdb', 'show', 'br', 'br-test'], text=True))
            assert any(x.get('mac') == '02:00:00:00:00:b1' and x.get('ifname') == 'fttr-u7' for x in fdb)
            assert command('remove') == (0, 0, 0)
            assert 'fttr-u7' not in links()
            fdb = json.loads(subprocess.check_output(['bridge', '-j', 'fdb', 'show', 'br', 'br-test'], text=True))
            assert not any(x.get('mac') == '02:00:00:00:00:b1' for x in fdb)
            assert command('create')[0] == 0
            run('ip', 'link', 'set', 'br-test', 'down')
            assert command('verify')[0] == -errno.ENETDOWN
            run('ip', 'link', 'set', 'br-test', 'up')
            assert command('verify')[0] == 0
            # A stale in-memory owner must not delete a port that has been reassigned.
            run('ip', 'link', 'set', 'fttr-u7', 'alias', 'unowned')
            assert command('verify')[0] == -errno.ESTALE
            assert command('remove')[0] == -errno.ESTALE
            assert command('reset')[0] == -errno.EEXIST and 'fttr-u7' in links()
            run('ip', 'link', 'set', 'fttr-u7', 'alias', child['ifalias'])
            assert command('reset')[0] == 0 and 'fttr-u7' not in links()
            # An interrupted create leaves a down VLAN marked by GROUP before
            # the separate RTM_NEWLINK applies its serial alias and master.
            run('ip', 'link', 'add', 'link', 'fttr0', 'name', 'fttr-u7', 'group', str(0x46545452),
                'type', 'vlan', 'id', '263')
            assert command('reset')[0] == 0 and 'fttr-u7' not in links()
        finally:
            wire.close(); lan.close()
            process.stdin.close(); process.wait(timeout=2)
        assert process.returncode == 0
    report = {'original_sha256': {str(path.relative_to(args.rootfs)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (args.rootfs/'bin/onuinfo', args.rootfs/'lib/modules/smuxdev.ko', args.rootfs/'etc/scripts/onu_setup.sh')},
        'frames': cases,
        'checks': ['original caller derives first service VID from GEM Port-ID',
            'actual original ARM64 push/pop bytes match real Linux VLAN and bridge forwarding',
            'customer VLAN and QinQ survive both directions, including a 1500-byte customer payload',
            'unmapped GEM is not bridged to LAN and no frame reflects to its ingress ONU',
            'deleting the service port removes its learned bridge FDB',
            'ownership mismatch is exposed without deleting the reassigned device; owned warm-start cleanup deletes stale port'],
        'scope': 'private Linux network namespace and veth; original skb memory helpers modeled; no optical or FE/PE acceleration verification'}
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
