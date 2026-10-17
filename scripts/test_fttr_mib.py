#!/usr/bin/env python3
"""Verify original MIB request/parsing bytes and a focused native socket exchange."""
import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from arm64_elf_oracle import ElfOracle

HOST, OLT = bytes.fromhex('020304050607'), bytes.fromhex('0a0b0c0d0e0f')


class Stock:
    def __init__(self, binary):
        self.oracle = ElfOracle(binary, self.handler, ('plr_gponomci_me_high_prio_tx',))
        self.oracle.symbols['stock_upload_build'] = 0x667dc
        self.oracle.symbols['stock_next_build'] = 0x66b50
        self.oracle.symbols.update(stock_set=0x64f8c, stock_create=0x6535c,
                                   stock_get=0x65ce0, stock_delete=0x659c0)
        self.data = self.oracle.scratch + 0x2000
        assert self.oracle.call('plr_omci_sw_crc32_table_init') == 0

    def handler(self, name, args):
        uc = self.oracle.uc
        if name == 'stock_alloc_unused':
            return 0
        if name == 'memset':
            uc.mem_write(args[0], bytes([args[1] & 255]) * args[2])
            return args[0]
        if name in ('memmove', 'memcpy'):
            uc.mem_write(args[0], bytes(uc.mem_read(args[1], args[2])))
            return args[0]
        if name == 'plr_gponomci_me_high_prio_tx':
            self.body = bytes(uc.mem_read(args[2], 44))
            return 0x80000000
        raise AssertionError(('unmodeled original call', name))

    def request(self, transaction, next, sequence):
        self.oracle.uc.mem_write(self.data, struct.pack('<IIHH12x', 0, 6 if next else 5, 0, sequence))
        assert self.oracle.call('stock_next_build' if next else 'stock_upload_build',
                                transaction, 2, self.data) == 0x80000000
        return self.body

    def me_request(self, operation, class_id, instance, mask, ram):
        action, name = {0x44: (3, 'stock_create'), 0x46: (4, 'stock_delete'),
                        0x48: (1, 'stock_set'), 0x49: (2, 'stock_get')}[operation]
        data = struct.pack('<IIHHHH', 0, action, mask, instance, mask, mask) + ram
        self.oracle.uc.mem_write(self.data, data)
        args = [0x8123, class_id, self.data]
        args += [instance] if operation == 0x46 else [self.data + 10, len(ram) + 6]
        assert self.oracle.call(name, *args) == 0x80000000
        return self.body

    def frame(self, transaction, opcode, content, onu=5, class_id=2, instance=0):
        body = struct.pack('>HBBHH', transaction, opcode, 10, class_id, instance) + content
        body = body.ljust(40, b'\0') + b'\0\0\0\x28'
        self.oracle.uc.mem_write(self.data, body)
        crc = self.oracle.call('plr_omci_crc32_get', self.data, 44)
        return HOST + OLT + struct.pack('>HH', 0x8888, onu) + body + struct.pack('>I', crc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source = root / 'port/packages/tyfza1-fttr/src'

    class Link(ctypes.Structure):
        _fields_ = [('host_mac', ctypes.c_ubyte * 6), ('olt_mac', ctypes.c_ubyte * 6),
                    ('ethertype', ctypes.c_ushort), ('downstream_length', ctypes.c_bool)]

    class Session(ctypes.Structure):
        _fields_ = [('fd', ctypes.c_int), ('link', Link),
                    ('onu', ctypes.c_ushort), ('transaction', ctypes.c_ushort)]

    class Record(ctypes.Structure):
        _fields_ = [('class_id', ctypes.c_ushort), ('instance', ctypes.c_ushort),
                    ('mask', ctypes.c_ushort), ('attributes', ctypes.c_ubyte * 26)]

    record_fn = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ushort, ctypes.POINTER(Record))
    stock = Stock(args.stock)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        sources = [source / n for n in ('miniolt.c', 'omci-wire.c', 'omci-socket.c', 'omci-mib.c', 'omci-me.c', 'omci-objects.c', 'omci-gem.c', 'omci-association.c', 'omci-vlan.c', 'omci-model.c', 'omci-service.c')]
        flags = ['cc', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror']
        subprocess.run(flags + ['-shared', '-fPIC', '-o', str(path / 'native.so')] + list(map(str, sources)), check=True)
        subprocess.run(flags + ['-o', str(path / 'fttrctl'), str(source / 'fttrctl.c')] + list(map(str, sources)), check=True)
        library = ctypes.CDLL(str(path / 'native.so'))
        library.fttr_mib_request.argtypes = [ctypes.c_ushort, ctypes.c_bool, ctypes.c_ushort, ctypes.c_void_p]
        library.fttr_mib_record_decode.argtypes = [ctypes.c_char_p, ctypes.POINTER(Record)]
        library.fttr_mib_scan.argtypes = [ctypes.POINTER(Session), ctypes.POINTER(ctypes.c_ushort), record_fn, ctypes.c_void_p]
        library.fttr_miniolt_link.argtypes = [ctypes.c_int, ctypes.POINTER(Link)]
        library.fttr_miniolt_management.argtypes = [ctypes.c_int,ctypes.c_uint,ctypes.c_bool]
        library.fttr_miniolt_alloc_id.argtypes = [ctypes.c_uint,ctypes.c_uint]
        library.fttr_miniolt_alloc_id.restype = ctypes.c_ushort
        class Hardware(ctypes.Structure):
            _fields_=[('downstream',ctypes.c_uint),('upstream',ctypes.c_uint),
                      ('eqd',ctypes.c_uint),('serial',ctypes.c_ubyte*8)]
        library.fttr_miniolt_one.argtypes = [ctypes.c_int,ctypes.c_uint,ctypes.POINTER(Hardware)]
        for next, sequence in ((False, 0), (True, 0), (True, 257)):
            body = ctypes.create_string_buffer(44)
            library.fttr_mib_request(0x8123, next, sequence, body)
            assert body.raw == stock.request(0x8123, next, sequence)

        attribute = bytes(range(26))
        payloads = [struct.pack('>HHH', 256, 0, 0xc000) + attribute,
                    struct.pack('>HHH', 262, 0x8002, 0x8000) + attribute[::-1]]
        response = stock.frame(0x8001, 0x2e, payloads[0])[16:60]
        record = Record()
        library.fttr_mib_record_decode(response, ctypes.byref(record))
        o, p = stock.oracle, stock.data
        o.uc.mem_write(p, response)
        assert o.call('plr_gponomci_me_uploadnext_meid_parse', p, p + 128, p + 130) == 0x80000000
        assert struct.unpack('<HH', o.uc.mem_read(p + 128, 4)) == (record.class_id, record.instance)
        o.uc.mem_write(p + 256, b'\0')
        o.call('plr_gponomci_me_uploadnext_attrmask_get', p, p + 512, p + 256, p + 258)
        assert struct.unpack('<H', o.uc.mem_read(p + 258, 2))[0] == record.mask
        assert bytes(record.attributes) == attribute

        expected = [stock.request(0xffff, False, 0), stock.request(0x8001, True, 0),
                    stock.request(0x8002, True, 1)]
        replies = [[stock.frame(0xffff, 0x2d, b'\0\2', onu=6),
                    stock.frame(0xfffe, 0x2d, b'\0\2'), stock.frame(0xffff, 0x2d, b'\0\2')],
                   [stock.frame(0x8001, 0x2e, payloads[0])],
                   [stock.frame(0x8002, 0x2e, payloads[1])]]

        def run_peer(expected, replies):
            client, server = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            client.setblocking(False)
            server.settimeout(8)
            errors, received, entries = [], [], []

            def peer():
                try:
                    for body, responses in zip(expected, replies):
                        request = server.recv(2048)
                        received.append(request)
                        assert request[16:60] == body and request[:16] == OLT + HOST + bytes.fromhex('88880005')
                        for response in responses:
                            server.send(response)
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=peer)
            thread.start()
            link = Link((ctypes.c_ubyte * 6)(*HOST), (ctypes.c_ubyte * 6)(*OLT), 0x8888, False)
            session = Session(client.fileno(), link, 5, 0xfffe)

            def collect(context, sequence, pointer):
                r = pointer.contents
                entries.append((sequence, r.class_id, r.instance, r.mask, bytes(r.attributes)))
                return 0

            count = ctypes.c_ushort(0)
            callback = record_fn(collect)
            ret = library.fttr_mib_scan(ctypes.byref(session), ctypes.byref(count), callback, None)
            thread.join(9)
            client.close()
            server.close()
            assert not thread.is_alive() and not errors, errors
            return ret, count.value, entries, received

        ret, count, entries, sent = run_peer(expected, replies)
        assert ret == 0 and count == 2 and len(sent) == 3
        assert entries == [(0, 256, 0, 0xc000, attribute), (1, 262, 0x8002, 0x8000, attribute[::-1])]
        bad = [[stock.frame(0xffff, 0x2d, b'\0\2', class_id=3)]]
        assert run_peer(expected[:1], bad)[0] == -errno.EPROTO
        ret, count, entries, sent = run_peer([expected[0]] * 3, [[], [], []])
        assert ret == -errno.ETIMEDOUT and len(sent) == 3 and not entries
        assert sent[0] == sent[1] == sent[2]

        class MeJob(ctypes.Structure):
            _fields_ = [('state',ctypes.c_int),('onu',ctypes.c_ushort),('transaction',ctypes.c_ushort),
                        ('mask',ctypes.c_ushort),('attempts',ctypes.c_uint),('deadline',ctypes.c_int64),
                        ('error',ctypes.c_int),('replied',ctypes.c_bool),('result',ctypes.c_ubyte),
                        ('request',ctypes.c_ubyte*44),('reply',ctypes.c_ubyte*44)]

        class Message(ctypes.Structure):
            _fields_ = [('onu',ctypes.c_ushort),('body',ctypes.c_void_p),('length',ctypes.c_size_t)]

        library.fttr_me_job_start.argtypes = [ctypes.POINTER(MeJob),ctypes.c_ushort,
            ctypes.POINTER(ctypes.c_ushort),ctypes.c_int,ctypes.c_ushort,ctypes.c_ushort,
            ctypes.c_ushort,ctypes.c_char_p,ctypes.c_size_t]
        library.fttr_me_job_reply.argtypes = [ctypes.POINTER(MeJob),ctypes.POINTER(Message)]
        library.fttr_me_job_send.argtypes = [ctypes.POINTER(MeJob),ctypes.c_int,ctypes.POINTER(Link),ctypes.c_int64]
        library.fttr_me_job_timeout.argtypes = library.fttr_me_job_send.argtypes
        me_cases = [(0x44,268,0xfa80,struct.pack('>HHBHHHH',0x117,0x8007,3,0x8001,0xffff,0x102,0xffff),
                     struct.pack('<HHBHHBHBHB',0x117,0x8007,3,0x8001,0xffff,0,0x102,0,0xffff,0)),
                    (0x48,262,0x8000,b'\x01\x23',b'\x23\x01'+bytes(5)),
                    (0x49,268,0xc000,b'',bytes(16)),(0x46,268,0,b'',b'')]
        for operation,c,mask,wire,ram in me_cases:
            job,counter = MeJob(),ctypes.c_ushort(0x8122)
            assert library.fttr_me_job_start(ctypes.byref(job),5,ctypes.byref(counter),
                operation,c,0x8007,mask,wire,len(wire))==0
            assert bytes(job.request)==stock.me_request(operation,c,0x8007,mask,ram)
            response = stock.frame(0x8123,(operation & 0x1f)|0x20,
                b'\0'+(struct.pack('>H',mask)+b'\x01\x17\x80\x07' if operation==0x49 else b''),
                class_id=c,instance=0x8007)[16:60]
            body = ctypes.create_string_buffer(response)
            message = Message(6,ctypes.addressof(body),44)
            assert library.fttr_me_job_reply(ctypes.byref(job),ctypes.byref(message))==0
            message.onu=5
            assert library.fttr_me_job_reply(ctypes.byref(job),ctypes.byref(message))==1
            assert job.state==1 and job.replied and not job.result and bytes(job.reply)==response
        for status,mask,expected in [(6,0xc000,-errno.EREMOTEIO),(0,0x8000,-errno.EPROTO)]:
            job,counter = MeJob(),ctypes.c_ushort(0x8122)
            library.fttr_me_job_start(ctypes.byref(job),5,ctypes.byref(counter),0x49,268,0x8007,0xc000,None,0)
            response=stock.frame(0x8123,0x29,bytes([status])+struct.pack('>H',mask),
                                 class_id=268,instance=0x8007)[16:60]
            body=ctypes.create_string_buffer(response)
            message=Message(5,ctypes.addressof(body),44)
            assert library.fttr_me_job_reply(ctypes.byref(job),ctypes.byref(message))==expected
            assert job.state==2 and job.result==status and bytes(job.reply)==response
        job,counter = MeJob(),ctypes.c_ushort(0xffff)
        assert library.fttr_me_job_start(ctypes.byref(job),5,ctypes.byref(counter),
            0x49,268,0x8007,0xc000,None,0)==0 and job.transaction==0x8001
        client,server=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
        link=Link((ctypes.c_ubyte*6)(*HOST),(ctypes.c_ubyte*6)(*OLT),0x8888,False)
        assert library.fttr_me_job_send(ctypes.byref(job),client.fileno(),ctypes.byref(link),1000)==0
        first=server.recv(2048)
        for now in (3000,5000):
            assert library.fttr_me_job_timeout(ctypes.byref(job),client.fileno(),ctypes.byref(link),now)==0
            assert server.recv(2048)==first
        assert library.fttr_me_job_timeout(ctypes.byref(job),client.fileno(),ctypes.byref(link),7000)==-errno.ETIMEDOUT
        client.close();server.close()

        # Execute the actual allocator with its original constants. Only its
        # allocation-occupancy predicate is modeled as free; no service runs.
        o,p=stock.oracle,stock.data
        assert o.call('plr_gpon_const_val_init')==0
        assert o.call('plr_gpon_min_allocId_get')==257 and o.call('plr_gpon_max_allocId_get')==767
        o.hooks[0x914d4]='stock_alloc_unused'
        for id,slot in [(0,1),(5,3),(63,7)]:
            assert o.call('plr_ponsrv_alloc_id_get',0,id+1,slot,p)==0
            assert library.fttr_miniolt_alloc_id(id,slot)==struct.unpack('<H',o.uc.mem_read(p,2))[0]

        # Exercise the actual executable against the kernel attribute ABI only.
        fixture = path / 'device'
        fixture.mkdir()
        fixture.joinpath('onu_table').write_text(''.join(
            f'{id} {0x40000 if id == 5 else 0:08x} 12345678 0000002a 5445535401234567\n'
            for id in range(64)))
        fixture.joinpath('omci_link').write_text('host=02:03:04:05:06:07 olt=0a:0b:0c:0d:0e:0f ethertype=8888 downstream_length=1\n')
        fixture.joinpath('management_grant').write_text('')
        fixture.joinpath('onu_state').write_bytes(b''.join(struct.pack('<III8s',
            0x40000 if id==5 else 0,0x12345678,42,bytes.fromhex('5445535401234567')) for id in range(64)))
        result = subprocess.run([str(path / 'fttrctl'), str(fixture), 'status'], capture_output=True, text=True, check=True)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(records) == 64 and [r['id'] for r in records if r['hardware_registered']] == [5]
        assert records[5]['serial'] == '5445535401234567' and records[5]['eqd'] == 42
        fd = os.open(fixture, os.O_RDONLY | os.O_DIRECTORY)
        link = Link()
        assert library.fttr_miniolt_link(fd, ctypes.byref(link)) == 0
        hardware=Hardware()
        assert library.fttr_miniolt_one(fd,5,ctypes.byref(hardware))==0
        assert hardware.downstream==0x40000 and hardware.upstream==0x12345678 and hardware.eqd==42
        assert bytes(hardware.serial)==bytes.fromhex('5445535401234567')
        assert library.fttr_miniolt_management(fd,5,True)==0
        assert fixture.joinpath('management_grant').read_text()=='5 1\n'
        os.close(fd)
        assert bytes(link.host_mac) == HOST and bytes(link.olt_mac) == OLT and link.downstream_length
        fixture.joinpath('onu_table').unlink()
        result = subprocess.run([str(path / 'fttrctl'), str(fixture), 'status'], capture_output=True, text=True)
        assert result.returncode == 1 and not result.stdout

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'stock_sha256': hashlib.sha256(args.stock.read_bytes()).hexdigest(),
        'original_request_comparisons': 3, 'original_class_instance_mask_parse_match': True,
        'socket_mib_records': 2, 'unrelated_replies_do_not_complete_transaction': True,
        'transaction_wrap': 'ffff -> 8001', 'timeout_attempts': 3, 'timeout_seconds_per_attempt': 2,
        'matching_bad_response_errors': True, 'real_cli_attribute_rows': 64,
        'original_me_request_comparisons':len(me_cases),'me_rejection_retains_raw_result':True,
        'me_get_mask_mismatch_is_error':True,'me_retransmission_deadline_verified':True,
        'native_alloc_id_matches_original_slots':[0,5,63],
        'missing_attribute_is_error': True, 'scope': 'original code and local datagram peer; no live MiniOLT',
        'hardware_verified': False}, indent=2) + '\n')
    print('MIB/ME: original builders/parsers, native scan and transaction result/deadline checks passed')


if __name__ == '__main__':
    main()
