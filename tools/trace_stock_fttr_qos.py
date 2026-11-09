#!/usr/bin/env python3
"""Execute the target QosSync builder and pending response dispatcher."""
import argparse
import copy
import json
from pathlib import Path
import struct

from trace_stock_fttr_settings import SettingsOriginal
from trace_stock_fttr_sync import SyncOriginal


class QosOriginal(SettingsOriginal):
    def __init__(self, binary):
        super().__init__(binary)
        self.oracle.symbols['qos'] = 0x4128a0
        self.configuration = {'ahsapd.qos': {}}
        self.commands = []

    def handler(self, name, a):
        if name == 'system':
            command = self.string(a[0])
            assert command == '/bin/ubus call ahsapd.qos getQosConfig > /tmp/qos_sync'
            self.commands.append(command)
            return 0
        if name == 'read_request_file':
            assert self.string(a[0]) == '/tmp/qos_sync'
            if self.configuration is None:
                return 0
            raw = self.configuration if isinstance(self.configuration, str) else json.dumps(self.configuration)
            self.oracle.uc.mem_write(self.text + 0x6000, raw.encode() + b'\0')
            return self.text + 0x6000
        if name == 'json_tokener_parse':
            try:
                return self.object(json.loads(self.string(a[0])))
            except ValueError:
                return 0
        if name == 'json_object_object_get_ex':
            value = self.objects[a[0]].get(self.string(a[1]))
            self.oracle.uc.mem_write(a[2], struct.pack('<Q', self.object(value) if value is not None else 0))
            return int(value is not None)
        return super().handler(name, a)

    def qos(self, configuration):
        self.configuration = copy.deepcopy(configuration)
        p = self.object({})
        result = self.oracle.call('qos', p)
        return result, self.objects[p]


def verify(binary):
    stock = QosOriginal(binary)
    wire = SyncOriginal(binary)
    rows = []
    # Deliberately synthetic opaque fields, not asserted vendor QoS schema.
    for fields in ({}, {'synthetic': 1}, {'synthetic': [1, 'two', {'x': False}]},
                   {'SyncFromMaster': '0'}, {'synthetic': '引号"与\\反斜线'}):
        result, request = stock.qos({'ahsapd.qos': fields, 'unrelated': 'ignored'})
        assert result == 1
        expected = dict(fields, SyncFromMaster='1')
        assert request == {'RPCMethod': 'QosSync', 'MAC': stock.mac.hex().upper(), 'ahsapd.qos': expected}
        for reply in ({'Result': 1}, {'Result': 0}, {'RPCMethod': 'QosSync', 'Result': 1}):
            assert stock.reply(reply, pending=18) == ((0, 2, 0), {})
        rows.append(dict(input={'ahsapd.qos': fields}, request=request,
                         frame=wire.encode(request, ethertype=0x88aa).hex()))
    for invalid in (None, '{bad', {}, {'unrelated': {}}, {'ahsapd.qos': None}):
        result, request = stock.qos(invalid)
        assert result == 0xffffffff and request == {}
    return dict(cases=rows, source_command=stock.commands[0],
                scope='Actual retained builder, Ethernet encoder and pending18 ACK dispatcher; ubus/file/JSON boundaries and remote replies modeled. Opaque payload schema and downstream scheduler effects not established.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.write_text(json.dumps(verify(a.controller), indent=2) + '\n')
    print('Original QosSync: five opaque builders, source failures, pending18 replies and wire encoding PASS')
