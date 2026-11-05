#!/usr/bin/env python3
"""Compare original SN relocation and exercise guarded native MiniOLT writes."""
import argparse
import ctypes as C
import errno
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from test_fttr_onu_admin import Stock, Registers
from test_fttr_fpga_boot import function

ROOT = Path(__file__).resolve().parents[1]


class Original(Stock):
    def io(self, name, a):
        if name == 'memset':
            self.relay.uc.mem_write(a[0], bytes([a[1]&255])*a[2]); return a[0]
        return super().io(name, a)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('asic', 'relay-asic', 'relay-fpga', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    headers = ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    rd = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))
    wr = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_uint)
    delay = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)
    class IO(C.Structure):
        _fields_ = [('context', C.c_void_p), ('read', rd), ('write', wr), ('delay', delay)]
    source = '''#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#include "rtl9617c-fttr-move.h"
struct rtl_fttr {struct rtl_fttr_reg_io reg_io;enum rtl_fttr_backend backend;int control_lock;};
struct device {struct rtl_fttr *fttr;}; struct device_attribute {int unused;};
static int locks;
#define dev_get_drvdata(d) ((d)->fttr)
#define mutex_lock(p) (locks++)
#define mutex_unlock(p) (locks--)
static int hex2bin(unsigned char *out,const char *in,size_t bytes) {
 for(size_t i=0;i<bytes;i++){unsigned v;
  if(strspn(in+2*i,"0123456789abcdefABCDEF")<2 || sscanf(in+2*i,"%2x",&v)!=1)return -1;
  out[i]=v;}return 0;
}
int move(const struct rtl_fttr_reg_io *io,int backend,unsigned from,unsigned to,const unsigned char *sn,const unsigned char *target) {
 return rtl_fttr_onu_move(io,backend,from,to,sn,target);
}
'''
    source += function((headers/'rtl9617c-fttr.c').read_text(), 'onu_move_store')
    source += '''
int store(const struct rtl_fttr_reg_io *io,int backend,const char *input) {
 struct rtl_fttr f={.reg_io=*io,.backend=backend};struct device d={&f};
 int ret=onu_move_store(&d,0,input,strlen(input));return locks?-EDEADLK:ret;
}
'''
    serial = bytes.fromhex('5445535401234567')
    target = bytes.fromhex('4f4c442100112233')
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp);(folder/'move.c').write_text(source)
        subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-sign-compare',
            '-shared','-fPIC','-I'+str(headers),str(folder/'move.c'),'-o',str(folder/'move.so')],check=True)
        native = C.CDLL(str(folder/'move.so'))
        native.move.argtypes = [C.POINTER(IO),C.c_int,C.c_uint,C.c_uint,C.c_char_p,C.c_char_p]
        native.store.argtypes = [C.POINTER(IO),C.c_int,C.c_char_p]
        def setup(from_id, to):
            r=Registers(from_id,seed=0x40000,pending=0)
            r.values.update({0x40801+to*4:int.from_bytes(target[:4],'big'),
                0x40800+to*4:int.from_bytes(target[4:],'big')})
            return r
        def io(r):
            def read(ctx, reg, value):
                value[0]=r.read(reg)
                return -errno.EIO if len(r.events)==r.fail else 0
            def write(ctx, reg, value):
                r.write(reg,value)
                if getattr(r, 'race_at', None)==reg:
                    r.values[0x40800+r.race_from*4]^=1
                return -errno.EIO if len(r.events)==r.fail else 0
            return IO(None,rd(read),wr(write),delay(lambda ctx,us:r.events.append(['delay',us])))
        for backend in (1,2,3):
            for from_id,to in ((0,63),(5,7),(63,0)):
                expected,actual=setup(from_id,to),setup(from_id,to)
                original=Original(args.asic,args.relay_asic if backend!=1 else args.relay_fpga,expected)
                o=original.relay;p=o.scratch+0x2000;o.uc.mem_write(p,serial)
                assert o.call('drv_gpon_onuid_mismatch_deactive',0,to,p,from_id)==0
                native_io=io(actual)
                assert native.move(C.byref(native_io),backend,from_id,to,serial,target)==0
                # Native adds identity, zero-grant and readback checks.
                assert [e for e in actual.events if e[0]!='read']==[e for e in expected.events if e[0]!='read']
                assert actual.values==expected.values
                trace=list(actual.events)
                for fail,event in enumerate(trace,1):
                    if event[0]=='delay':continue
                    broken=setup(from_id,to);broken.fail=fail;broken_io=io(broken)
                    assert native.move(C.byref(broken_io),backend,from_id,to,serial,target)==-errno.EIO
                    assert broken.events==trace[:fail]
                for id in (from_id,to):
                    r=setup(from_id,to);r.values[0x40800+id*4]^=1;r_io=io(r)
                    assert native.move(C.byref(r_io),backend,from_id,to,serial,target)==-errno.ESTALE
                    assert not any(e[0]=='write' for e in r.events)
                for flags in (0x40000,0x10000):
                    r=setup(from_id,to);r.values[0x40401+to*2]=flags;r_io=io(r)
                    assert native.move(C.byref(r_io),backend,from_id,to,serial,target)==-errno.EBUSY
                    assert not any(e[0]=='write' for e in r.events)
                for id in (from_id,to):
                    for slot in ((0,) if backend==1 else (1,7)):
                        for fixed in (False,True):
                            r=setup(from_id,to);base=0x40400+id*2 if backend==1 else 0x41000+id*32+slot*4
                            r.values[base+int(fixed)]=r.values.get(base+int(fixed),0)|1;r_io=io(r)
                            assert native.move(C.byref(r_io),backend,from_id,to,serial,target)==-errno.EBUSY
                            assert not any(e[0]=='write' for e in r.events)
                r=setup(from_id,to);r.drop=True;r_io=io(r)
                assert native.move(C.byref(r_io),backend,from_id,to,serial,target)==-errno.EUCLEAN
                assert len([e for e in r.events if e[0]=='write'])==1
                r=setup(from_id,to);r.race_at=0x40800+to*4;r.race_from=from_id;r_io=io(r)
                assert native.move(C.byref(r_io),backend,from_id,to,serial,target)==-errno.EUCLEAN
                assert [e[1] for e in r.events if e[0]=='write']==[0x40801+to*4,0x40800+to*4]
                r=setup(from_id,to);r.pending=10000;r_io=io(r)
                assert native.move(C.byref(r_io),backend,from_id,to,serial,target)==-errno.ETIMEDOUT
                assert r.values[0x40800+from_id*4]==int.from_bytes(serial[4:],'big')
                assert not any(e[0]=='write' and 0x40007<=e[1]<=0x4000a for e in r.events)
                cases.append(dict(backend=backend,source=from_id,target=to,io_events=len(trace),original_writes='match'))
            for text in ('64 1 '+serial.hex()+' '+target.hex(),'5 5 '+serial.hex()+' '+target.hex(),
                    '5 7 xxxx '+target.hex(),'5 7 '+serial.hex()+' '+target.hex()+' junk'):
                r=setup(5,7);r_io=io(r)
                assert native.store(C.byref(r_io),backend,text.encode())==-errno.EINVAL and not r.events
            r=setup(5,7);r_io=io(r);text=f'5 7 {serial.hex()} {target.hex()}\n'.encode()
            assert native.store(C.byref(r_io),backend,text)==len(text)
        r=setup(5,7);r_io=io(r)
        assert native.move(C.byref(r_io),4,5,7,serial,target)==-errno.EOPNOTSUPP and not r.events
    args.output.write_text(json.dumps(dict(cases=cases,guard_errors='passed',fault_cutoffs='passed',
        asic_sha256=hashlib.sha256(args.relay_asic.read_bytes()).hexdigest(),
        fpga_sha256=hashlib.sha256(args.relay_fpga.read_bytes()).hexdigest(),
        scope='Original ARM64 SN/deactivation writes and native guarded transaction. Registers modeled; no physical registration or retirement.'),indent=2)+'\n')
    print('PASS: original ONU relocation writes, both identities, occupied/blocked targets, data grants, IO cutoff and false success')


if __name__=='__main__':main()
