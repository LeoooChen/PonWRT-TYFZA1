#!/usr/bin/env python3
"""Execute actual FE transaction/NI bind and withdrawal for same-MAC peers on two devices."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[1]
f=root/'scripts/test_wifi_offload_transaction.py';ns={'__file__':str(f)}
text=f.read_text();exec(compile(text[:text.index("cases = r'''")],str(f),'exec'),ns)
common,function=ns['common'],ns['function']
common=common.replace('static int ni_uplink_start(struct rtl_ax_tx *tx){assert(tx==active_tx);return 0;}', 'static unsigned starts;static int uplink_error;static int ni_uplink_start(struct rtl_ax_tx *tx){assert(tx==active_tx);starts++;return uplink_error;}')
common=common.replace('static int rtl9617c_wifi_id(struct net_device *dev){(void)dev;return 1;}', 'static unsigned second_id=17;static struct net_device *devices[2];static int rtl9617c_wifi_id(struct net_device *dev){return dev==devices[0]?1:second_id;}')
common=common.replace('static int rtl_ax_rx_uplink_enable(void *rx,unsigned id,unsigned wifi){(void)rx;assert(id==1&&wifi==1);return 0;}', 'static unsigned rx_id[128];static int rtl_ax_rx_uplink_enable(void *rx,unsigned id,unsigned wifi){(void)rx;rx_id[id]=wifi;return 0;}')
common=common.replace('static void rtl_ax_rx_uplink_disable(void *rx,unsigned id){(void)rx;assert(id==1);}', 'static void rtl_ax_rx_uplink_disable(void *rx,unsigned id){(void)rx;rx_id[id]=0;}')
common=common.replace('static void rtl_ax_rx_uplink_retire(void *rx,unsigned id){(void)rx;assert(id==1);}', 'static void rtl_ax_rx_uplink_retire(void *rx,unsigned id){(void)rx;rx_id[id]=0;}')
common=common.replace('static bool rtl_ax_rx_uplink_active(void *rx,unsigned id){(void)rx;(void)id;return false;}', 'static bool rtl_ax_rx_uplink_active(void *rx,unsigned id){(void)rx;return rx_id[id]!=0;}')
common=common.replace('static int rtl_ax_tx_mcast_unbind_vif(struct rtl_ax_tx *tx,struct net_device *d){(void)tx;(void)d;return 0;}', 'static int detach_error;static int rtl_ax_tx_mcast_unbind_vif(struct rtl_ax_tx *tx,struct net_device *d){if(detach_error)return detach_error;for(unsigned i=0;i<12;i++)if(tx->mcast_dev[i]==d){dev_put(d);tx->mcast_dev[i]=NULL;}return 0;}')
# Existing low-level model checks age/hash cache ownership; retain it with own helper.
fe=ns['read']('ethernet/realtek/rtl9617c-fe.c');ni=ns['read']('wireless/realtek/rtl_ax_ap/ni-test.h')
common=common.replace('static int detach_error;', 'static void dev_put(struct net_device *);static int detach_error;',1)
code=common+''.join(function(fe,n) for n in ('fe_flow_port','fe_port','fe_wifi_client','fe_wifi_peer','rtl_fe_wifi_peer_route'))
code+='''
static int rtl9617c_wifi_peer_route(struct net_device *dev,const u8 *mac,unsigned route){return rtl_fe_wifi_peer_route(active_fe,dev,mac,route);}
'''
code+=''.join(function(ni,n) for n in ('ni_peer_bind','ni_peer_put','ni_peer_withdraw','ni_peer_routes_withdraw','rtl_ax_tx_ni_route','rtl_ax_tx_ni_peer','ni_retire_peer','rtl_ax_tx_ni_retire_peer','rtl_ax_tx_ni_retire_ba','ni_vif_release','rtl_ax_tx_vif_release','rtl_ax_tx_ni_cleanup'))
code+=r'''
int main(void){
 for(unsigned failure=0;failure<=3;failure++) {
  struct rtl_fe fe={};struct net_device dev[2]={};struct rtl_fe_port port[2]={};
  struct rtl_fe_flow flow[2]={};u32 actions[2][12]={};u8 memory[0x80000]={};
  struct rtl_ax_tx tx={.pe_active=true,.pe.memory=memory,.ni={.active=true,.cpu=3},.bcmc_enabled=true};
  const u8 mac[6]={2,1,2,3,4,5};devices[0]=&dev[0];devices[1]=&dev[1];active_fe=&fe;active_tx=&tx;
  init(&fe.ports);init(&fe.flows);init(&fe.wifi_peers);fe.memory=actions;
  tx.ni.route_dev=&dev[0];dev_hold(&dev[0]);
  for(unsigned i=0;i<2;i++){
   port[i].netdev=&dev[i];port[i].wifi_id=i?17:1;port[i].destination=i?18:16;list_add_tail(&port[i].list,&fe.ports);
   flow[i].output=&port[i];flow[i].installed=true;flow[i].hash_index=i;flow[i].crc16=100+i;
   memcpy(flow[i].destination,mac,6);list_add_tail(&flow[i].list,&fe.flows);
   memcpy(memory+0xe400+(i+1)*8,mac,6);put_unaligned_le32(BIT(18),memory+0xd000+(i+1)*32+16);
   tx.peer_authorized[i+1]=true;ages[i]=1;rtl_fe_action_set(actions[i],RTL_FE_MCGID,port[i].destination);
  }
  fail_stage=0;assert(rtl_ax_tx_ni_peer(&tx,&dev[0],1)==1);
  uplink_error=-ENOMEM;assert(rtl_ax_tx_ni_peer(&tx,&dev[1],2)==-ENOMEM);
  assert(dev[1].references==1&&tx.ni.peer_dev[2]==&dev[1]&&!tx.ni.route_peers[2]);
  assert(!rtl_ax_tx_ni_retire_peer(&tx,2)&&!dev[1].references);
  uplink_error=0;
  fail_index=1;fail_stage=failure;int ret=rtl_ax_tx_ni_peer(&tx,&dev[1],2);
  assert(ret==(failure?-ETIMEDOUT:1));assert(dev[0].references==2&&dev[1].references==1);
  assert(tx.ni.peer_dev[1]==&dev[0]&&tx.ni.peer_dev[2]==&dev[1]);
  assert(rx_id[1]==1&&rx_id[2]==(failure?0:17));
  assert(get_unaligned_le32(memory+0x11a04)==1&&get_unaligned_le32(memory+0x11a08)==17);
  fail_stage=0;assert(rtl_ax_tx_ni_peer(&tx,&dev[1],2)==(failure?1:0));assert(rx_id[2]==17);
  assert(rtl_ax_tx_ni_peer(&tx,&dev[0],2)==-ESTALE&&dev[1].references==1);
  second_id=18;assert(rtl_ax_tx_ni_peer(&tx,&dev[1],2)==-ESTALE&&get_unaligned_le32(memory+0x11a08)==17);second_id=17;
  memory[0xe410+5]^=1;assert(rtl_ax_tx_ni_peer(&tx,&dev[1],2)==-ESTALE);memory[0xe410+5]^=1;
  /* The old binding survives FE delete failure and uses its captured MAC. */
  fail_stage=failure;fail_index=1;ret=rtl_ax_tx_ni_retire_peer(&tx,2);
  assert(ret==(failure?-ETIMEDOUT:0));assert(fe_wifi_peer(&fe,&port[0],mac)&&rx_id[1]==1);
  if(failure){assert(dev[1].references==1&&tx.ni.peer_dev[2]==&dev[1]);fail_stage=0;assert(!rtl_ax_tx_ni_retire_peer(&tx,2));}
  assert(!dev[1].references&&!tx.ni.peer_dev[2]&&!fe_wifi_peer(&fe,&port[1],mac));
  assert(fe_wifi_peer(&fe,&port[0],mac)->destination==19);
  assert(rtl_ax_tx_ni_peer(&tx,&dev[1],2)==1);
  /* TX BA withdrawal retains the netdev for RX; later full release owns it. */
  assert(!rtl_ax_tx_ni_retire_ba(&tx,2)&&dev[1].references==1&&rx_id[2]==17);
  assert(!rtl_ax_tx_ni_retire_peer(&tx,2)&&!dev[1].references&&!rx_id[2]);
  /* Full radio teardown withdraws every remaining device independently. */
  assert(rtl_ax_tx_ni_peer(&tx,&dev[1],2)==1);fail_stage=failure;fail_index=1;
  ret=rtl_ax_tx_ni_route(&tx,NULL,0);assert(ret==(failure?-ETIMEDOUT:0));
  if(failure){assert(dev[0].references==2&&dev[1].references==1);fail_stage=0;assert(!rtl_ax_tx_ni_route(&tx,NULL,0));}
  assert(!dev[0].references&&!dev[1].references&&!tx.ni.peer_dev[1]&&!tx.ni.peer_dev[2]);
  assert(!fe_wifi_peer(&fe,&port[0],mac)&&!fe_wifi_peer(&fe,&port[1],mac));memset(rx_id,0,sizeof(rx_id));
 }
 /* The root ring reference migrates while another interface remains live. */
 {
  struct net_device a={},b={};struct rtl_ax_tx tx={.pe_active=true,.ni={.route_dev=&a},.mcast_dev={&a,&b}};
  dev_hold(&a);dev_hold(&a);dev_hold(&b);active_tx=&tx;
  detach_error=-EIO;assert(rtl_ax_tx_vif_release(&tx,&a)==-EIO);
  assert(a.references==3&&b.references==1&&tx.ni.retired_vif[0]==&a&&tx.ni.route_dev==&a);
  assert(rtl_ax_tx_vif_release(&tx,&a)==-EIO&&a.references==3);
  detach_error=0;assert(!rtl_ax_tx_ni_cleanup(&tx));
  assert(!a.references&&b.references==2&&tx.ni.route_dev==&b&&!tx.ni.retired_vif[0]);
  dev_put(&b);dev_put(&b);
 }
 puts("Actual FE/NI two-device same-MAC ownership: publish/withdraw faults, exact refs, stale reassignment, BA-retained RX and global withdrawal PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='ni-peer-owner-') as tmp:
 p=Path(tmp);(p/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-fsanitize=address,undefined','-I'+str(root/ns['base']/'ethernet/realtek'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
