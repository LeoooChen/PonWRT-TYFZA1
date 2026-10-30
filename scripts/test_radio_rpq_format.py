#!/usr/bin/env python3
"""Compare native RPQ queue, WP ID and status decoding with actual factory C/XB code."""
from pathlib import Path
import ctypes, re, subprocess, sys, tempfile
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'tools'))
from arm64_module_oracle import ModuleOracle
source=(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()
body=source[source.index('int rtl_ax_tx_report('):]
lines=[re.search(r'\t\tunsigned int '+name+r' = [^\n]*',body)[0] for name in ('id','qsel','ring')]
channels=re.search(r'static const u8 rtl_ax_tx_channels\[[^\n]*',source)[0]
code='#include <stdint.h>\ntypedef uint8_t u8;\n#define RTL_AX_TX_RINGS 6\n'+channels+'\nvoid decode(unsigned report,unsigned *out){\n'+'\n'.join(lines)+'\nout[0]=ring<RTL_AX_TX_RINGS?rtl_ax_tx_channels[ring]:255;out[1]=id;out[2]=status;}\n'
names=[p+'_'+c for c in ('8852ce','8192xbe') for p in ('hal_handle_rx_report','hal_qsel_to_tid','hal_mapping_hw_tx_chnl')]
o=ModuleOracle(root/'analysis/private/live-files/lib/modules/5.10.70/kernel/drivers/net/wireless/realtek/g6_wifi_driver/rtk_wifi6.ko',names,lambda n,a:0 if n=='printk' else (_ for _ in ()).throw(AssertionError(n)))
a=o.scratch;frame=a+0x1000;out=a+0x2000;o.word(a+8,a+0x3000)
with tempfile.TemporaryDirectory()as tmp:
 p=Path(tmp);(p/'decode.c').write_text(code)
 subprocess.run(['cc','-shared','-fPIC','-O2','-Wall','-Wextra','-Werror',str(p/'decode.c'),'-o',str(p/'decode.so')],check=True)
 native=ctypes.CDLL(str(p/'decode.so'));native.decode.argtypes=[ctypes.c_uint,ctypes.POINTER(ctypes.c_uint)]
 total=0
 for chip in ('8852ce','8192xbe'):
  for qsel in (*range(8),17,18):
   for ind in (0,1):
    for status in (0,1,7):
     report=1|qsel<<8|status<<13|(0x137|ind<<14)<<16
     o.uc.mem_write(frame,report.to_bytes(4,'little'));o.uc.mem_write(out,bytes(32));o.word(a+0xff00,out+24)
     assert o.call('hal_handle_rx_report_'+chip,a,frame,4,out,out+4,out+8,out+12,out+16)==4
     actual=(ctypes.c_uint*3)();native.decode(report,actual)
     expected=[o.unpack(out+4,'B')[0],o.unpack(out+8,'H')[0],o.unpack(out+24,'B')[0]]
     assert list(actual)==expected,(chip,qsel,ind,status,list(actual),expected)
     total+=1
 print('Native RPQ decode matches actual factory C/XB functions:',total,'vectors')
