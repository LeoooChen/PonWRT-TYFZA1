#!/usr/bin/env python3
"""Execute owning-MACID group drain and compare HIQ commands with target output."""
import argparse,ctypes as C,json,subprocess,tempfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];d=r/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,required=True);a=p.parse_args()
def function(file,name):
 s=(d/file).read_text();start=s.index('int '+name+'(');end=s.index('\n}\n',start)+3;return s[start:end]
code=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
#include "rtl9617c-seq.h"
#define BIT(n) (1U<<(n))
#define RTL_MC_PE_BCMC_COUNT 12
#define RTL_AX_H2C_CMD 0
#define IEEE80211_NUM_TIDS 16
struct rtl_ax_ap {unsigned unused;};struct pci_dev {struct rtl_ax_ap *radio;};struct rtl_ax_tx {void *bcmc_dev[12];struct pci_dev *pdev;unsigned bcmc_domain_retiring,bcmc_domains;};
#define pci_get_drvdata(p) ((p)->radio)
static int fault,step;static unsigned waited,root;static u8 wire[24];
static void rtl_ax_store32(u8 *p,unsigned v){memcpy(p,&v,4);}
static int rtl_ax_command(struct rtl_ax_ap *r,unsigned q,unsigned cat,unsigned cls,unsigned fn,const u8 *p,unsigned len,bool ack){
 (void)r;assert(q==0&&cat==1&&cls==9&&fn==27&&len==24&&!ack);memcpy(wire,p,24);assert(step==1);step=2;return fault==2?-EIO:0;
}
static int rtl_ax_tx_pause(struct rtl_ax_tx *t){(void)t;assert(!step);step=1;return fault==1?-ETIMEDOUT:0;}
static void rtl_ax_tx_open(struct rtl_ax_tx *t){(void)t;assert(step==2);step=3;}
static int rtl_ax_tx_wait_station(struct rtl_ax_tx*t,unsigned id,unsigned tid){(void)t;assert((step==3||step==4)&&tid==16);step=4;waited=id;return fault==3?-ETIMEDOUT:0;}
static bool rtl_ax_tx_station_pending(struct rtl_ax_tx *t,unsigned id,unsigned tid){(void)t;(void)id;assert(tid==16);return true;}
static void rtl_ax_tx_bcmc_close_domain(struct rtl_ax_tx *t,unsigned domain){(void)t;assert(domain<12);root++;}
'''+function('pci.c','rtl_ax_mac_drop_group')+function('tx.c','rtl_ax_tx_group_stop')+r'''
void run(unsigned id,unsigned port,unsigned mbid,int error,u8 *out){
 struct rtl_ax_ap radio={};struct pci_dev pci={&radio};struct rtl_ax_tx tx={.pdev=&pci};fault=error;step=0;waited=255;root=0;
 int ret=rtl_ax_tx_group_stop(&tx,id,port,mbid);
 assert(root==1);
 assert(ret==(error==1||error==3?-ETIMEDOUT:error==2?-EIO:0));
 assert(step==(error==1?1:error==2?3:4));assert(waited==(error==1||error==2?255:error==3?0:id));memcpy(out,wire,24);
}
void invalid(void){struct rtl_ax_tx tx={};step=0;
 assert(rtl_ax_tx_group_stop(&tx,128,0,0)==-EINVAL&&!step);
 assert(rtl_ax_tx_group_stop(&tx,1,1,1)==-EINVAL&&!step);
 assert(rtl_ax_tx_group_stop(&tx,1,5,0)==-EINVAL&&!step);
 assert(rtl_ax_tx_group_stop(&tx,1,0,8)==-EINVAL&&!step);}
'''
with tempfile.TemporaryDirectory() as folder:
 c=Path(folder)/'test.c';so=Path(folder)/'test.so';c.write_text(code)
 subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC','-I'+str(r/'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),str(c),'-o',str(so)],check=True)
 lib=C.CDLL(str(so));out=C.create_string_buffer(24);lib.run.argtypes=[C.c_uint]*3+[C.c_int,C.c_void_p];count=0
 for row in json.loads(a.original.read_text())['drops']:
  for fail in range(4):
   lib.run(41,row['port'],row['mbid'],fail,out)
   if fail!=1:assert out.raw==bytes.fromhex(row['wire'])
   count+=1
 lib.run(0,0,0,0,out);lib.invalid()
 c.write_text(code+'\nint main(void){u8 out[24];for(unsigned f=0;f<4;f++)run(41,0,7,f,out);invalid();return 0;}\n')
 subprocess.run(['cc','-O1','-fsanitize=address,undefined','-I'+str(r/'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),str(c),'-o',str(Path(folder)/'test')],check=True);subprocess.run([str(Path(folder)/'test')],check=True)
print(f'Group drain: {count} original HIQ commands and pause/drop/wait faults retain the owning MACID; root delegation and invalid identity passed')
