#!/usr/bin/env python3
"""Execute original remote-Wi-Fi Set producers and baseline encoders."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from test_fttr_association import Original


class WifiOriginal(Original):
    def run_wifi(self, cfg, instance=0x101, fail_at=0):
        p, u = self.data, self.oracle.uc
        u.mem_write(p, struct.pack('<HH', 0, 5))
        u.mem_write(p+512, cfg)
        self.calls=[]
        self.fail_at=fail_at
        self.result=self.oracle.call('plr_gponrm_onu_wifi_set', p, instance, p+512)
        frames=self.encode_calls()
        return frames

    def handler(self, name, a):
        if name=='plr_gponomci_sync_api':
            ret=super().handler(name,a)
            return 1 if self.fail_at and len(self.calls)==self.fail_at else ret
        return super().handler(name,a)


def config(mode=3, security=3):
    # Synthetic input, never a retained user SSID or secret. These offsets
    # are loads in plr_gponrm_onu_wifi_set, not a copied vendor C structure.
    cfg=bytearray(247)
    cfg[3]=mode;cfg[4]=6;cfg[37]=1;cfg[46]=security
    cfg[5:29]=b'FTTR native research'.ljust(24,b'\0')
    cfg[47:71]=b'test-passphrase-123'.ljust(24,b'\0')
    cfg[111]=1;cfg[113]=2;cfg[242]=2;cfg[245]=cfg[246]=1
    for i, offset in enumerate((114,146,178,210)):
        cfg[offset:offset+26]=bytes([65+i])*26
    return bytes(cfg)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    stock=WifiOriginal(args.master)
    cases=[]
    for mode,security in ((0,0),(3,1),(7,3),(4,5),(6,6)):
        frames=stock.run_wifi(config(mode,security))
        assert stock.result==0
        masks=[int.from_bytes(frame[8:10],'big') for frame in frames]
        assert masks[:2]==[0xd000 if mode in (3,4,6,7) else 0xc000,0x2000]
        assert masks[-2:]==[0x20,0x10]
        assert all(frame[2:4]==b'\x48\x0a' and frame[6:8]==b'\x01\x01' for frame in frames)
        cases.append({'mode':mode,'security':security,'frames':[frame.hex() for frame in frames]})
    failure=stock.run_wifi(config(),fail_at=3)
    assert len(failure)==3 and stock.result!=0
    report={'master_sha256':hashlib.sha256(args.master.read_bytes()).hexdigest(),'cases':cases,
        'failed_third_group':{'return':stock.result,'sent_groups':len(failure)},
        'finding':'Original uses baseline Sets on classes ff69/ff6a, independent radio/name/security/enable groups. '
            'SSID and shared key wire fields are 24 bytes; key-index branches5/6 use four 26-byte fields. '
            'A failure stops subsequent groups without restoring the earlier acknowledged Sets.',
        'scope':'Actual original master producers, adapters and encoders. MIB storage, transport and peer execution are not established by this trace.'}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(report['finding'])


if __name__=='__main__':main()
