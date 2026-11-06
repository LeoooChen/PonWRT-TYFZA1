#!/usr/bin/env python3
"""Execute the real PON receive dispatcher across ITU/EPON mode changes."""
import ast
from pathlib import Path
import re
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source=(D/'rtl9617c-pon-oam.c').read_text()
start=source.index('static void rtl_oam_receive(')
end=source.index('\nstatic void rtl_oam_wake',start)
fixture=ast.parse((ROOT/'scripts/test_pon_data_path.py').read_text())
code=next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='model' for t in n.targets))
code+=r'''
struct rtl9617c_bridge_frame {unsigned multicast_host_group;};
#define SKB_EXT_RTL9617C_BRIDGE 0
static struct rtl9617c_bridge_frame host;
static const struct rtl9617c_bridge_frame *skb_ext_find(struct sk_buff *s,int id){(void)s;(void)id;return &host;}
static unsigned itu_calls;
static int rtl_omci_receive_offset(const u8 *p,unsigned n){(void)p;(void)n;return 0;}
static void rtl_pon_omci_receive(struct rtl9617c_pon *p,struct napi_struct *n,struct sk_buff *s,unsigned src){(void)p;(void)n;(void)s;(void)src;assert(0);}
static void rtl_pon_itu_data_receive(struct rtl9617c_pon *p,struct napi_struct *n,struct sk_buff *s,unsigned src){(void)p;(void)n;assert(src==(7|11<<8));itu_calls++;dev_kfree_skb_any(s);}
'''
code+=source[start:end]
code+=r'''
int main(void){
 struct rtl9617c_pon pon={.mode=RTL_PON_GPON};struct napi_struct napi;
 struct net_device owner={.running=true,.carrier=true},data={.running=true,.carrier=true};
 u8 bytes[64]={1,0,0x5e,0,0,1,2};bytes[12]=8;
 pon.oam_endpoint.netdev=&owner;pon.data_devices[11]=&data;
 host.multicast_host_group=7;
 rtl_oam_receive(&pon.oam_endpoint,&napi,packet(bytes,64),7|11<<8);
 assert(itu_calls==1&&!received&&!owner.stats.rx_dropped);
 pon.mode=RTL_PON_EPON_1G;
 rtl_oam_receive(&pon.oam_endpoint,&napi,packet(bytes,64),7|11<<8);
 assert(itu_calls==1&&!received&&owner.stats.rx_dropped==1);
 host.multicast_host_group=0;
 rtl_oam_receive(&pon.oam_endpoint,&napi,packet(bytes,64),7|11<<8);
 assert(received==1&&data.stats.rx_packets==1&&retired==3);
 puts("PON dispatcher: ITU host owner delivery, late-copy rejection after EPON switch and normal EPON LLID RX passed");
}
'''
with tempfile.TemporaryDirectory(prefix='pon-host-mode-') as temp:
 p=Path(temp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-fsanitize=address,undefined','-I'+str(D),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
