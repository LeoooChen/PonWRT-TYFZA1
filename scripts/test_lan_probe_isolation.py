#!/usr/bin/env python3
"""Execute production LAN registration with failures at each per-port stage."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-xram.c').read_text()

def function(name):
    start = re.search(r'^static (?:void|int) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef unsigned char u8;
#define ETH_ALEN 6
#define ETH_DATA_LEN 1500
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define IS_ERR(p) ((intptr_t)(p)<0)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define PHY_INTERFACE_MODE_SGMII 1
#define PHY_INTERFACE_MODE_GMII 0
#define rcu_assign_pointer(p,v) ((p)=(v))
#define SET_NETDEV_DEV(d,p) ((void)0)
#define dev_warn(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define spin_lock_irqsave(l,f) ((f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(f))
struct device_node {unsigned int port;};
struct device {struct device_node *of_node;};
struct phy {int dummy;};
struct phy_device {struct {struct device dev;} mdio;unsigned int phy_id;};
struct rtl_xram;
struct rtl_lan {struct rtl_xram *shared;unsigned int port;};
struct net_device {
 struct rtl_lan lan;struct phy_device *phydev;char name[16];
 void *netdev_ops,*ethtool_ops;int max_mtu;u8 dev_addr[6];
};
struct tyfza1_led {void *output;int requested;struct {char *name;int max_brightness;} cdev;};
struct rtl_xram {
 struct net_device *netdev,*ports[5];unsigned int registered_ports,port;
 struct {struct net_device *lan[5];} qm;
 struct phy *sgmii;struct tyfza1_led phy_led;
 bool phy_led_registered,qm_rx,native_groups_created,wifi_started;
 int tx_lock;void *fe;
};
static struct device_node nodes[5];
static struct phy_device phys[5];
static struct phy sgmii;
static int rtl_netdev_ops,rtl_ethtool_ops;
static int board_ports,fail_port,stage,current_port;
static unsigned int active,removed,fe_ports,allocated,power,actions,node_refs;
static void *netdev_priv(struct net_device *d){return &d->lan;}
static void rtl_adjust_link(struct net_device *d){}
static struct device_node *of_parse_phandle(struct device_node *n,const char *s,unsigned int i){
 unsigned int p=!strcmp(s,"phy-handle")?0:i;
 if(p>=(unsigned int)board_ports || (board_ports==5 && p==3))return NULL;
 current_port=p;node_refs++;return &nodes[p];
}
static void of_node_put(struct device_node *n){assert(node_refs);node_refs--;}
static struct phy *devm_phy_get(struct device *d,const char *s){return stage==1?(void *)(intptr_t)-EIO:&sgmii;}
static int phy_power_on(struct phy *p){if(stage==2)return -EIO;power++;return 0;}
static void phy_power_off(void *p){assert(power);power--;}
static int devm_add_action_or_reset(struct device *d,void (*f)(void *),void *p){
 if(stage==3){f(p);return -EIO;}actions++;return 0;
}
static void devm_release_action(struct device *d,void (*f)(void *),void *p){assert(actions);actions--;f(p);}
static struct net_device *alloc_etherdev(unsigned int size){
 if(stage==4 && current_port==fail_port)return NULL;
 allocated++;return calloc(1,sizeof(struct net_device));
}
static void free_netdev(struct net_device *d){assert(allocated);allocated--;free(d);}
static void ether_addr_copy(u8 *a,const u8 *b){memcpy(a,b,6);}
static void eth_hw_addr_set(struct net_device *d,const u8 *a){memcpy(d->dev_addr,a,6);}
static struct phy_device *of_phy_find_device(struct device_node *n){
 return stage==5 && current_port==fail_port?NULL:&phys[n->port];
}
static int phy_connect_direct(struct net_device *d,struct phy_device *p,void (*fn)(struct net_device *),int mode){
 if(stage==6 && current_port==fail_port)return -ETIMEDOUT;
 d->phydev=p;return 0;
}
static void put_device(struct device *d){}
static void phy_disconnect(struct phy_device *p){}
static int rtl_fe_add_port(void *fe,struct net_device *d,int p,int q,int r){
 if(stage==7 && current_port==fail_port)return -EIO;
 fe_ports|=BIT(d->lan.port);return 0;
}
static void rtl_fe_remove_port(void *fe,struct net_device *d){fe_ports&=~BIT(d->lan.port);}
static int register_netdev(struct net_device *d){
 if(stage==8 && current_port==fail_port)return -EIO;
 assert(d->phydev);active|=BIT(d->lan.port);return 0;
}
static void unregister_netdev(struct net_device *d){
 assert(active&BIT(d->lan.port));active&=~BIT(d->lan.port);removed|=BIT(d->lan.port);
}
static void synchronize_net(void){}
static int tyfza1_led_register(struct device *d,struct tyfza1_led *l,void *n){return stage==9?-EIO:0;}
static void tyfza1_led_unregister(struct tyfza1_led *l){}
static void *rtl_phy_led_output;
'''
code = shim + '\n'.join(function(n) for n in ('rtl_unregister_lan', 'rtl_sgmii_power_off', 'rtl_register_lan', 'rtl_register_lans'))
# Run the actual post-registration probe block independently of shared DMA setup.
probe = function('rtl_probe')
services = probe[probe.index('\tret = sysfs_create_groups('):probe.index('\tschedule_delayed_work(')]
code += r'''
static int service_failure;
static int sysfs_create_groups(void *d,void *g){return service_failure==1?-EIO:0;}
static int rtl_wifi_start(struct device *d){return service_failure==2?-EIO:0;}
static void *rtl_native_groups;
static void services(struct rtl_xram *priv,struct device *dev){int ret;
'''+services+r'''
}
int main(void){
 unsigned int cases=0;
 for(board_ports=4;board_ports<=5;board_ports++)
 for(fail_port=0;fail_port<board_ports;fail_port++)
 for(stage=0;stage<=9;stage++) {
  struct net_device primary={.dev_addr={0,0x50,0xc2,1,2,3}};
  struct rtl_xram priv={.netdev=&primary,.qm_rx=true};
  struct device parent={0};
  active=removed=fe_ports=allocated=power=actions=node_refs=0;
  for(int p=0;p<5;p++)nodes[p].port=p;
  rtl_register_lans(&priv,&parent);
  unsigned int expected=(1U<<board_ports)-1;
  if(board_ports==5)expected&=~BIT(3);
  if(stage>=1 && stage<=3 && board_ports==5)expected&=~BIT(4);
  if(stage>=4 && stage<=8 && !(stage==4 && fail_port==0))expected&=~BIT(fail_port);
  assert(active==expected && priv.registered_ports==expected && fe_ports==expected);
  assert(!removed && !node_refs);
  for(int p=0;p<board_ports;p++){
   assert(!!priv.ports[p]==!!(expected&BIT(p)));
   assert(priv.qm.lan[p]==priv.ports[p]);
   if(priv.ports[p]){
    char name[16];snprintf(name,sizeof(name),"lan%u",p==4?4:p+1);
    assert(!strcmp(priv.ports[p]->name,name));
    assert(priv.ports[p]->dev_addr[5]==3+p);
   }
  }
  if(board_ports==5 && !(expected&BIT(4)))assert(!power && !actions);
  if(stage==9)assert(!priv.phy_led_registered);
  for(service_failure=0;service_failure<=2;service_failure++){
   priv.dma_tx=true;priv.native_groups_created=priv.wifi_started=false;
   services(&priv,&parent);
   assert(active==expected && !removed);
   assert(priv.native_groups_created==(service_failure!=1));
   assert(priv.wifi_started==(service_failure!=2));
  }
  for(int p=0;p<board_ports;p++)rtl_unregister_lan(&priv,p);
  assert(!active && !fe_ports && !allocated && !priv.registered_ports);
  cases++;
 }
 printf("%u per-port fault cases, both boards, optional services and cleanup passed\n",cases);
}
'''
code = code.replace('bool phy_led_registered,qm_rx,native_groups_created,wifi_started;', 'bool phy_led_registered,qm_rx,dma_tx,native_groups_created,wifi_started;')
# The production code uses the device kobject only as a sysfs API argument.
code = code.replace('struct device {struct device_node *of_node;};', 'struct device {struct device_node *of_node;int kobj;};')
with tempfile.TemporaryDirectory(prefix='lan-isolation-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-sign-compare',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
