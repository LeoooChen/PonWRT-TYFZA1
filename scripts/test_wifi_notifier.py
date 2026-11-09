#!/usr/bin/env python3
"""Exercise NI ownership across netdev events and down-interface type changes."""
from pathlib import Path
import re
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-wifi.c').read_text()
if '--before' in sys.argv:source=subprocess.check_output(['git','show','e109480:port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-wifi.c'],cwd=root,text=True)

def function(name):
    match = re.search(r'^static [^;\n]*\b' + name + r'\([^;]*?\)\s*\{', source, re.M)
    start, end, depth = match.start(), match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#define NL80211_IFTYPE_STATION 2
#define NL80211_IFTYPE_AP 3
#define NL80211_IFTYPE_MONITOR 6
#define NL80211_IFTYPE_MESH_POINT 7
#define NETDEV_UP 1
#define NETDEV_DOWN 2
#define NETDEV_REGISTER 5
#define NETDEV_UNREGISTER 6
#define NOTIFY_DONE 0
#define netdev_notifier_info_to_dev(p) (p)
#define netdev_err(...) ((void)0)
#define netdev_info(...) ((void)0)
#define GFP_KERNEL 0
#define NFPROTO_NETDEV 5
#define NF_NETDEV_INGRESS 0
#define INT_MIN (-2147483647-1)
#define kzalloc(n,f) calloc(1,n)
struct notifier_block {};
struct wireless_dev {unsigned iftype;};
struct net_device {struct wireless_dev *ieee80211_ptr;unsigned refs;bool fe;};
struct endpoint {struct net_device *netdev;void (*receive)(void);bool closing;};
struct nf_hook_ops {void (*hook)(void);void *priv;struct net_device *dev;int pf,hooknum,priority;};
struct rtl_wifi_port {int list;struct nf_hook_ops hook;bool hooked;unsigned id;struct endpoint endpoint;};
static int wifi_ports;
static void wifi_receive(void){}static void rtl_wifi_ingress(void){}
static struct rtl_wifi_port *owner;
static void *wifi_ni;
static unsigned attached,detached,hooks;
static int detach_error;
static struct rtl_wifi_port *wifi_port(struct net_device *dev){return owner&&owner->endpoint.netdev==dev?owner:NULL;}
static int rtl9617c_ni_wifi_attach(void *ni,struct endpoint *ep){
 (void)ni;assert(!owner);ep->netdev->refs++;ep->netdev->fe=true;ep->closing=false;attached++;return 1;
}
static void list_add_tail(int *list,int *head){assert(head==&wifi_ports && !owner);owner=(struct rtl_wifi_port *)((char *)list - offsetof(struct rtl_wifi_port,list));}
static int nf_register_net_hook(struct net_device *dev,struct nf_hook_ops *hook){assert(dev==hook->dev);hooks++;return 0;}
#define dev_net(dev) (dev)
static void nf_unregister_net_hook(struct net_device *dev,struct nf_hook_ops *hook){assert(owner&&dev==owner->endpoint.netdev&&hook==&owner->hook);hooks--;}
static int rtl9617c_ni_wifi_detach(void *ni,unsigned id){
 (void)ni;assert(owner&&id==1&&!owner->hooked);owner->endpoint.closing=true;if(detach_error)return detach_error;
 owner->endpoint.netdev->refs--;detached++;return 0;
}
static void rtl9617c_ni_fe_remove_port(void *ni,struct net_device *dev){(void)ni;dev->fe=false;}
static void list_del(int *list){assert(list==&owner->list);owner=NULL;}
#define kfree(p) free(p)
'''
code += function('wifi_del') + function('wifi_add') + function('wifi_event')
code += r'''
int main(void){
 struct wireless_dev w={};struct net_device dev={.ieee80211_ptr=&w,.refs=1};
 for(unsigned initial=2;initial<=3;initial++)for(unsigned final=6;final<=7;final++){
  w.iftype=initial;wifi_event(NULL,NETDEV_REGISTER,&dev);
  assert(dev.refs==2&&dev.fe&&hooks==1);
  /* An interface that never went UP can change type without a DOWN event. */
  w.iftype=final;wifi_event(NULL,NETDEV_UNREGISTER,&dev);
  assert(!owner&&dev.refs==1&&!dev.fe&&!hooks);
  wifi_event(NULL,NETDEV_UNREGISTER,&dev);assert(dev.refs==1);
  w.iftype=initial;wifi_event(NULL,NETDEV_REGISTER,&dev);w.iftype=final;
  wifi_event(NULL,NETDEV_UP,&dev);assert(!owner&&!hooks&&dev.refs==1&&!dev.fe);
  wifi_event(NULL,NETDEV_DOWN,&dev);wifi_event(NULL,NETDEV_UNREGISTER,&dev);
  /* Failed drain keeps ownership, but UP must finish withdrawal before
   * publishing another ingress hook or using the retained endpoint. */
  w.iftype=initial;wifi_event(NULL,NETDEV_REGISTER,&dev);w.iftype=final;
  detach_error=-ETIMEDOUT;wifi_event(NULL,NETDEV_UP,&dev);
  assert(owner&&!hooks&&dev.refs==2&&dev.fe);
  w.iftype=initial;wifi_event(NULL,NETDEV_UP,&dev);
  assert(owner&&!hooks&&dev.refs==2&&dev.fe&&owner->endpoint.closing);
  detach_error=0;wifi_event(NULL,NETDEV_UP,&dev);
  assert(owner&&hooks==1&&dev.refs==2&&dev.fe&&!owner->endpoint.closing);
  detach_error=-ETIMEDOUT;
  w.iftype=final;wifi_event(NULL,NETDEV_DOWN,&dev);
  assert(owner&&!hooks&&dev.refs==2&&dev.fe);
  detach_error=0;wifi_event(NULL,NETDEV_UP,&dev);
  assert(!owner&&!hooks&&dev.refs==1&&!dev.fe);
  wifi_event(NULL,NETDEV_UNREGISTER,&dev);
 }
 w.iftype=NL80211_IFTYPE_AP;wifi_event(NULL,NETDEV_REGISTER,&dev);
 wifi_event(NULL,NETDEV_UP,&dev);assert(dev.refs==2&&hooks==1);
 wifi_event(NULL,NETDEV_DOWN,&dev);assert(!owner&&dev.refs==1&&dev.fe);
 wifi_event(NULL,NETDEV_UP,&dev);assert(owner&&dev.refs==2&&dev.fe);
 wifi_event(NULL,NETDEV_DOWN,&dev);wifi_event(NULL,NETDEV_UNREGISTER,&dev);assert(!dev.fe);
 w.iftype=NL80211_IFTYPE_STATION;wifi_event(NULL,NETDEV_REGISTER,&dev);w.iftype=NL80211_IFTYPE_MESH_POINT;
 detach_error=-ETIMEDOUT;wifi_event(NULL,NETDEV_UNREGISTER,&dev);
 assert(owner&&!hooks&&dev.refs==2);
 detach_error=0;wifi_event(NULL,NETDEV_UNREGISTER,&dev);
 assert(!owner&&dev.refs==1&&!dev.fe&&attached==detached);
 struct net_device lan={.refs=1};wifi_event(NULL,NETDEV_REGISTER,&lan);wifi_event(NULL,NETDEV_UNREGISTER,&lan);
 assert(lan.refs==1&&!lan.fe&&!owner);
 puts("NI notifier: AP/STA to mesh/monitor removal and UP, failed-drain AP/STA remains closed until withdrawal retry, repeated UNREGISTER, busy-drain retry, AP DOWN/UP and wired exclusion passed");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-notifier-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
