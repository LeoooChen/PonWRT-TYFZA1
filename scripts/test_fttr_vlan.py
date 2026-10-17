#!/usr/bin/env python3
"""Compare original VLAN producers/ONU replies, then exercise the native CLI over veth."""
import argparse
import ctypes as C
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_elf_oracle import ElfOracle
from test_fttr_association import Original
from test_fttr_controller import crc, HOST, OLT


class Slave:
    """Run the firmware's Normalizer, Get/GetNext workers and reply serializer.

    Model only the MIB storage/access API; no hardware/ONU driver is executed.
    The table snapshot layout follows the original Get worker's 14254..1439c.
    """
    def __init__(self, binary):
        self.oracle = ElfOracle(binary, self.handler)
        self.oracle.symbols.update(get_worker=0x13e00, next_worker=0x122d4, serialize=0xbeb4)
        self.oracle.hooks.update({0xc130:'capture_response', 0x11724:'mib_buffer', 0x13a54:'readable_mask'})
        self.p = self.oracle.scratch + 0x2000
        self.wire = None

    def handler(self, name, a):
        u, p = self.oracle.uc, self.p
        if name in ('GOS_Ntohs', 'GOS_Htons'):
            return int.from_bytes(struct.pack('<H', a[0] & 65535), 'big')
        if name in ('GOS_Ntohl', 'GOS_Htonl'):
            return int.from_bytes(struct.pack('<I', a[0] & 0xffffffff), 'big')
        if name == 'GOS_GetUINT16': return int.from_bytes(u.mem_read(a[0], 2), 'little')
        if name in ('GOS_SetUINT16', 'GOS_SetUINT32'):
            size = 2 if name.endswith('16') else 4
            u.mem_write(a[0], (a[1] & ((1 << (size*8))-1)).to_bytes(size, 'little')); return 0
        if name == 'MIB_ClearAttrSet': u.mem_write(a[0], bytes(4)); return 0
        if name == 'MIB_SetAttrSet':
            bits = int.from_bytes(u.mem_read(a[0], 4), 'little') | (1 << (a[1]-1))
            u.mem_write(a[0], struct.pack('<I', bits)); return 0
        if name == 'MIB_IsInAttrSet': return bool(int.from_bytes(u.mem_read(a[0], 4), 'little') & (1 << (a[1]-1)))
        if name == 'memset': u.mem_write(a[0], bytes([a[1] & 255])*a[2]); return a[0]
        if name == 'memcpy': u.mem_write(a[0], bytes(u.mem_read(a[1], a[2]))); return a[0]
        if name == 'free': return 0
        if name == 'mib_buffer': return p+12288
        if name == 'readable_mask': return 0x4400
        if name == 'MIB_GetTableIndexByClassId': assert a[0] == 171; return 1
        if name == 'MIB_GetTableEntrySize': return 128
        if name == 'MIB_SetAttrToBuf': return 0
        if name == 'MIB_Get': return 0
        if name == 'MIB_GetTableAttrNum': return 10
        if name == 'MIB_GetAttrLen': return {3:2, 7:16}[a[1]]
        if name == 'MIB_GetAttrDataType': return {3:1, 7:5}[a[1]]
        if name == 'MIB_GetAttrFromBuf':
            assert a[1] == 3 and a[4] == 2
            u.mem_write(a[2], struct.pack('<H', self.capacity)); return 0
        if name == 'OMCI_MeOperCfg':
            snapshot = self.oracle.symbols['gOmciMulGetData'] + a[5]*1032
            u.mem_write(snapshot, bytes(1032))
            u.mem_write(snapshot+7*32+8, struct.pack('<IH2xQIII', 7, (len(self.data)+28)//29,
                p+8192, len(self.data), 0, 1))
            u.mem_write(p+8192, self.data); return 0
        if name == 'capture_response': self.normalized = bytes(u.mem_read(a[0], 1992)); return 0
        if name == 'omci_wrapper_sendOmciMsg': self.wire = bytes(u.mem_read(a[0], a[1])); return 0
        raise AssertionError(('unmodeled original ONU call', name, a))

    def reply(self, request, data, capacity, status=0):
        o, p = self.oracle, self.p
        self.data, self.capacity = data, capacity
        o.uc.mem_write(p, request.ljust(48, b'\0')); o.uc.mem_write(p+256, bytes(1992))
        assert o.call('OMCI_NormalizeMsg', p, p+256) == 0
        if status or request[2] == 0x48:
            o.call('OMCI_ResponseMsg', p+256, status, 1)
        else:
            assert request[2] in (0x49, 0x5a)
            o.call('get_worker' if request[2] == 0x49 else 'next_worker', p+256)
        o.uc.mem_write(p+4096, self.normalized)
        o.call('serialize', p+4096)
        assert len(self.wire) == 48 and struct.unpack('>I', self.wire[44:])[0] == crc(self.wire[:44])
        return self.wire


def master_rules(master, address, input_vid, output_vid, priority):
    o, p = master.oracle, master.data
    o.symbols['vlan_rule'] = address
    o.uc.mem_write(p, bytes(2048)); o.uc.mem_write(p, struct.pack('<HH', 0, 5))
    cfg = bytearray(64)
    struct.pack_into('<HHHHH', cfg, 16, input_vid, 0xfffe, output_vid, output_vid, priority)
    o.uc.mem_write(p+64, bytes(cfg)); o.uc.mem_write(p+176, struct.pack('<H', 0x321))
    master.calls = []
    assert o.call('vlan_rule', p, p+64, p+160) == 0
    return [b[10:26] for b in master.encode_calls()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', required=True, type=Path)
    parser.add_argument('--slave', required=True, type=Path)
    parser.add_argument('--control', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link','show'], text=True))} == {'lo'}
    master, slave = Original(args.master), Slave(args.slave)
    results = []
    with tempfile.TemporaryDirectory() as directory:
        work, source = Path(directory), root/'port/packages/tyfza1-fttr/src'
        subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-o',str(work/'vlan.so')]+
            [str(source/n) for n in ('omci-vlan.c','omci-me.c','omci-socket.c','omci-wire.c','miniolt.c')], check=True)
        lib = C.CDLL(str(work/'vlan.so'))
        class Plan(C.Structure): _fields_ = [('count', C.c_ubyte), ('rows', (C.c_ubyte*16)*3)]
        lib.fttr_vlan_plan_build.argtypes = [C.POINTER(Plan), C.c_int, C.c_ushort, C.c_ushort, C.c_ubyte, C.c_bool]
        producers = [0xfb9b8, 0xfbeb0, 0xfbbcc, 0xfbd08, 0xfc20c, 0xfc0c0]
        deletes = [0xfc420, 0xfc420, 0xfc654, 0xfc7b0, 0xfcad4, 0xfc978]

        def plan(policy, input_vid=100, output_vid=200, priority=5, remove=False):
            native = Plan()
            assert lib.fttr_vlan_plan_build(C.byref(native), policy, input_vid, output_vid, priority, remove) == 0
            rows = [bytes(r) for r in native.rows[:native.count]]
            original = master_rules(master, (deletes if remove else producers)[policy], input_vid, output_vid, priority)
            assert rows == original, (policy, rows, original)
            return rows

        for policy in range(6):
            for remove in (False, True): plan(policy, remove=remove)
        for vid, priority in ((0,0), (4094,7)):
            plan(4, vid, vid); plan(5, output_vid=vid, priority=priority)
        results.append('six original producers and their delete rows match, including VID/priority boundaries')
        subprocess.run(['ip','link','add','ftv-host','type','veth','peer','name','ftv-peer'], check=True)
        for interface, mac in [('ftv-host','02:03:04:05:06:07'), ('ftv-peer','0a:0b:0c:0d:0e:0f')]:
            subprocess.run(['ip','link','set','dev',interface,'address',mac,'mtu','1982','up'], check=True)
        peer = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x8888))
        peer.bind(('ftv-peer',0x8888)); peer.setblocking(False)
        device = work/'miniolt'; device.mkdir()
        device.joinpath('omci_link').write_text('host=02:03:04:05:06:07 olt=0a:0b:0c:0d:0e:0f ethertype=8888 downstream_length=0\n')
        device.joinpath('management_grant').write_text('')

        def identity(replaced=False):
            serial = bytes.fromhex('5445535400000008' if replaced else '5445535400000007')
            device.joinpath('onu_state').write_bytes(b''.join(struct.pack('<III8s',
                (1 << 18) if i == 7 else 0, 1, 42, serial if i == 7 else bytes(8)) for i in range(64)))

        def run(command, rows, case='normal', capacity=8):
            identity(); pool = {r[:8]:r for r in rows}; operations = []; sets = 0
            query = subprocess.Popen([str(args.control.resolve()), str(device), *command, 'ftv-host'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic()+6
                while query.poll() is None:
                    assert time.monotonic() < deadline, (case, operations)
                    if not select.select([peer], [], [], 0.02)[0]: continue
                    frame, address = peer.recvfrom(2048)
                    if address[2] == socket.PACKET_OUTGOING: continue
                    assert frame[:16] == OLT+HOST+bytes.fromhex('88880007')
                    body = frame[16:60]; op = body[2]; mask = struct.unpack('>H', body[8:10])[0]
                    assert body[3:8] == bytes.fromhex('0a00ab0321') and body[40:44] == bytes.fromhex('00000028')
                    operations.append((op, mask, body[10:26].hex()))
                    status = 2 if case == 'unsupported' else 0
                    if op == 0x48:
                        assert mask == 0x0400
                        sets += 1
                        if case == 'replaced': identity(True); continue
                        if case in ('set-reject','restore-reject') and sets == 2: status = 6
                        if case == 'restore-reject' and sets == 3: status = 6
                        if not status and case != 'false-success':
                            row = body[10:26]
                            if case == 'verify-corrupt' and sets == 1:
                                row = row[:-1]+bytes([row[-1]^1])
                            if row[8:] == b'\xff'*8: pool.pop(row[:8], None)
                            else: pool[row[:8]] = row
                    data = b''.join(pool.values())
                    wire = slave.reply(body, data, capacity, status)
                    if op == 0x49 and mask == 0x0400 and case == 'bad-length':
                        wire = wire[:11]+struct.pack('>I', 17)+wire[15:44]
                        wire += struct.pack('>I', crc(wire))
                    peer.send(HOST+OLT+bytes.fromhex('88880007')+wire)
                output, error = query.communicate(timeout=2)
                record = json.loads(output)
                assert query.returncode == (1 if record['error'] else 0), (case, output, error)
                return record, list(pool.values()), operations
            finally:
                if query.poll() is None: query.terminate(); query.communicate(timeout=3)

        untouched = plan(2)[0]; translated = plan(4)
        for rows in ([], [untouched]+translated):
            record, pool, operations = run(['vlan-table','7','0x321'], rows)
            assert not record['error'] and record['before']['complete'] and bytes.fromhex(record['before']['data']) == b''.join(rows)
            assert len([o for o in operations if o[0] == 0x5a]) == (len(rows)*16+28)//29
        results.append('native table read matches original ONU Get/GetNext replies: empty and 48-byte table across two fragments')
        command = ['vlan','7','0x321','4','100','200','0','0']
        record, pool, operations = run(command, [untouched])
        assert record['verified'] and not record['error'] and set(pool) == set([untouched]+translated)
        assert [bytes.fromhex(o[2]) for o in operations if o[0] == 0x48] == translated
        record, pool, operations = run(command, [untouched]+translated)
        assert record['verified'] and not record['sent'] and not any(o[0] == 0x48 for o in operations)
        record, pool, operations = run(command[:-1]+['1'], [untouched]+translated)
        assert record['verified'] and pool == [untouched]
        results.append('add, no-write reuse and explicit removal preserve unrelated rows and verify the complete table')
        tagged = plan(5, output_vid=7, priority=2)[0]
        record, pool, operations = run(['vlan','7','0x321','5','0','200','5','0'], [tagged])
        assert record['verified'] and pool == plan(5)
        record, pool, operations = run(['vlan','7','0x321','5','0','200','5','0'], [tagged], 'verify-corrupt')
        assert record['error'] == -errno.EUCLEAN and record['restored'] and pool == [tagged]
        assert len([o for o in operations if o[0] == 0x48]) == 2
        for case, error in [('false-success',-errno.EUCLEAN), ('set-reject',-errno.EREMOTEIO), ('restore-reject',-errno.EREMOTEIO)]:
            record, pool, operations = run(command, [untouched], case)
            assert record['error'] == error and not record['verified']
            if case == 'restore-reject':
                assert not record['restored'] and record['cleanup_error'] == -errno.EREMOTEIO
                assert record['failure']['result'] == record['cleanup_failure']['result'] == 6
                assert not record['after']['complete'] and len([o for o in operations if o[0] == 0x48]) == 3
            else:
                assert record['restored'] and not record['cleanup_error'] and pool == [untouched]
        results.append('replaced prior row, false-success readback, partial Set rejection and failed restoration retain real state/errors')
        for case, capacity, rows, error in [('bad-length',8,[untouched],-errno.EPROTO),
                ('normal',1,[untouched]+translated,-errno.EMSGSIZE), ('unsupported',8,[],-errno.EREMOTEIO)]:
            record, pool, operations = run(command, rows, case, capacity)
            assert record['error'] == error and not any(o[0] in (0x48,0x5a) for o in operations)
        record, pool, operations = run(command, [untouched], capacity=1)
        assert record['error'] == -errno.ENOSPC and not any(o[0] == 0x48 for o in operations)
        record, pool, operations = run(command, [untouched], 'replaced')
        assert record['error'] == record['cleanup_error'] == -errno.ESTALE and not record['restored']
        assert len([o for o in operations if o[0] == 0x48]) == 1
        results.append('invalid/oversized tables and actual capacity stop writes; serial replacement stops retransmission and rollback')
        peer.close()
    report = {'master_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),
              'slave_sha256':hashlib.sha256(args.slave.read_bytes()).hexdigest(), 'checks':results,
              'scope':'offline original ARM64 execution with declared MIB/API boundaries and native AF_PACKET in a private namespace; no optical IO'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
