#!/usr/bin/env python3
"""Compare native FPGA2 startup registers to the supervisor-selected original caller."""
import argparse
import ctypes as C
import errno
import json
from pathlib import Path
import subprocess
import tempfile
from trace_stock_fttr_backend import dispatch, startup, fpga2_registers

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('watchdog','caller','asic','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    headers=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    rd=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint,C.POINTER(C.c_uint))
    wr=C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint,C.c_uint)
    delay=C.CFUNCTYPE(None,C.c_void_p,C.c_uint)
    class IO(C.Structure):_fields_=[('context',C.c_void_p),('read',rd),('write',wr),('delay',delay)]
    assert dispatch(args.watchdog,2)['executable']=='/bin/onuinfo'
    cases=[]
    with tempfile.TemporaryDirectory() as directory:
        folder=Path(directory)
        (folder/'native.c').write_text('''#include <errno.h>
#include "rtl9617c-fttr-fpga.h"
int initialize(const struct rtl_fttr_reg_io *io,unsigned int mode){return rtl_fttr_fpga2_init(io,mode);}
''')
        subprocess.run(['cc','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror','-I'+str(headers),
                        str(folder/'native.c'),'-o',str(folder/'native.so')],check=True)
        native=C.CDLL(str(folder/'native.so'));native.initialize.argtypes=[C.POINTER(IO),C.c_uint]
        values,events,fail={},[],0
        def read(ctx,reg,out):
            out[0]=values.get(reg,0);events.append(['read',reg,out[0]])
            return -errno.EIO if len(events)==fail else 0
        def write(ctx,reg,value):
            events.append(['write',reg,value])
            if len(events)==fail:return -errno.EIO
            values[reg]=value;return 0
        io=IO(None,rd(read),wr(write),delay(lambda ctx,usec:events.append(['delay',usec])))
        for mode in (5,9):
            original=startup(args.caller,2,mode)
            calls=[e for e in original['events'] if e[0] in ('usleep','drv_reset_phy0','drv_reset_phy1',
                'drv_set_optical_delay','drv_reset_soft','drv_set_reg_otp','drv_set_pause_en')]
            expected=fpga2_registers(args.asic,calls)
            values.clear();values.update({0x40022:0xa5f0,0x30005:8});events.clear()
            assert native.initialize(C.byref(io),mode)==0 and events==expected,(events,expected)
            for fail in range(1,len(expected)+1):
                if expected[fail-1][0]=='delay':continue
                values.clear();values.update({0x40022:0xa5f0,0x30005:8});events.clear()
                assert native.initialize(C.byref(io),mode)==-errno.EIO and events==expected[:fail]
            fail=0
            cases.append(dict(mode=mode,original_registers=expected,failure_cutoffs=True))
        for invalid in (0,1,3,10):
            events.clear();assert native.initialize(C.byref(io),invalid)==-errno.EINVAL and not events
    args.output.write_text(json.dumps(dict(cases=cases,hardware_tested=False),indent=2)+'\n')
    print('PASS: FPGA2 selected original startup, both host modes, exact IO/delay order and every failed operation')
if __name__=='__main__':main()
