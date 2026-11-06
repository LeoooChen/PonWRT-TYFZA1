#!/usr/bin/env python3
"""Execute stock EPON SID production through FC storage and NI replica encoding."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from arm64_module_oracle import ModuleOracle
from trace_stock_fttr_mcast_hw import replica_recipes


def run(epon_path, fc_path, ni_path):
    sw_index = 0
    fail = 0
    events = []
    depth = 0

    def fc_boundary(name, args):
        nonlocal depth
        if name == 'lock':
            assert not depth
            depth = 1
            return 0
        if name == 'unlock':
            assert depth == 1
            depth = 0
            return 0
        if name == 'aal_ni_dma_lso_set_streamid_tbl':
            assert depth == 1
            events.append(dict(operation='lso', sid=args[0], config=fc.unpack(args[1], 'I')[0]))
            return fail
        if name == 'rtk_fc_flow_delete_by_pattern':
            assert depth == 1
            events.append(dict(operation='withdraw', pattern=bytes(fc.uc.mem_read(args[0], 16)).hex()))
            return fail
        raise AssertionError(('unmodeled FC boundary', name, args))

    fc = ModuleOracle(fc_path, ['rtk_fc_pon_sid_set'], fc_boundary)
    db = fc.symbols['fc_db'][0]
    helper = fc.scratch + 0x1000
    fc.word(db + 48, helper)
    fc.word(helper + 80, fc.callback('lock'))
    fc.word(helper + 88, fc.callback('unlock'))
    calls = []

    def epon_boundary(name, args):
        if name == 'aal_mpcp_reg_sw_index_offset_get':
            assert args[0] == 0
            epon.uc.mem_write(args[1], bytes([sw_index]))
            return 0
        if name == 'rt_ponmisc_sid_set':
            sid, flags, fields = args[:3]
            assert flags == 0x100000001
            assert fields == (32+sw_index) | sid << 8
            result = fc.call('rtk_fc_pon_sid_set', *args[:3])
            assert not depth and result == 0
            route = bytes(fc.uc.mem_read(db + 0x94b2e8 + sid*6, 6))
            assert route == struct.pack('<BBBBH', 1, 0, 32+sw_index, sid, 0)
            calls.append(dict(sid=sid, flags=flags, fields=fields, route=route.hex(), returned=result))
            return result
        raise AssertionError(('unmodeled EPON SID boundary', name, args))

    epon = ModuleOracle(epon_path, ['us_pon_sid_mapping_adding.isra.0'], epon_boundary)
    cases = []
    for sw_index in (0, 4, 15, 31):
        for fail in (0, 5):
            calls.clear()
            events.clear()
            # Reset only the original route rows: first configuration.
            fc.uc.mem_write(db + 0x94b2e8, bytes(256*6))
            epon.call('us_pon_sid_mapping_adding.isra.0')
            assert len(calls) == len(events) == 8
            for call, event in zip(calls, events):
                assert event['operation'] == 'lso' and event['sid'] == call['sid']
                # Valid, destination6bits, COS3bits, POL0, DEEPQ1.
                assert event['config'] & 0x1ff077f == 1 | (32+sw_index)<<1 | call['sid']<<8 | 1<<24
            first = list(events)
            events.clear()
            calls.clear()
            epon.call('us_pon_sid_mapping_adding.isra.0')
            assert len(calls) == 8 and len(events) == 16
            assert [e['operation'] for e in events] == ['withdraw', 'lso']*8
            cases.append(dict(sw_index=sw_index, injected_callee_error=fail,
                              routes=list(calls), first=first, replace=list(events)))
    # Feed these original-produced values into the actual member7/bit12
    # action parser and NI converter. Allocation/Hash publication are boundaries.
    recipes = []
    for sw_index in (0, 4, 15, 31):
        for queue in (0, 3, 7):
            recipes += replica_recipes(fc_path, ni_path, (7,), False, False,
                                       (0, queue, sw_index))['cases']
    return dict(producer=cases, replicas=recipes, findings=[
        'Original EPON SID initializer creates exactly eight SIDs0..7, destination32+MPCP software-index offset, COS=SID and POL/flowId0; this is not ITU GEM8..255.',
        'FC stores the EPON route with flowId0 and emits native LSO metadata. Replacing a valid SID first requests flow deletion by that SID.',
        'Both failed flow deletion and failed LSO programming are ignored by this FC wrapper. Native invalidation must retain retry ownership rather than copy that success behavior.',
        'Original multicast member7/action-bit12 consumes those destination/COS/POL fields and final VLAN recipes. Endpoint selection and registration lifetime still require native integration.'],
        scope='Actual original EPON initializer, FC SID storage/replacement, multicast action parser and NI action converter. rt_ponmisc_sid_set forwarding, software-index read, locks, LSO hardware write, flow withdrawal and allocation are modeled; no claim of physical LLID queue mapping or packet retirement.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('epon', 'fc', 'ni', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    result = run(args.epon, args.fc, args.ni)
    result['sha256'] = {name: hashlib.sha256(getattr(args, name).read_bytes()).hexdigest()
                        for name in ('epon', 'fc', 'ni')}
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print('PASS: 128 original EPON SID updates/replacements, ignored-callee-error cases and36 full FC/NI replica recipes')


if __name__ == '__main__':
    main()
