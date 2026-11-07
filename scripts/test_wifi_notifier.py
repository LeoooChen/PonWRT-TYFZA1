#!/usr/bin/env python3
"""Exercise NI ownership across netdev events and down-interface type changes."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-wifi.c').read_text()

def function(name):
    match = re.search(r'^static .*?\b' + name + r'\([^;]*?\)\s*\{', source, re.M)
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
struct notifier_block {};
struct wireless_dev {unsigned iftype;};
struct net_device {struct wireless_dev *ieee80211_ptr;unsigned refs;bool fe;};
struct rtl_wifi_port {int list,hook;bool hooked;unsigned id;struct {struct net_device *netdev;}endpoint;};
static struct rtl_wifi_port *owner;
static void *wifi_ni;
static unsigned attached,detached,hooks;
static int detach_error;
static struct rtl_wifi_port *wifi_port(struct net_device *dev){return owner&&owner->endpoint.netdev==dev?owner:NULL;}
static int wifi_add(struct net_device *dev){
 if(owner){assert(owner->endpoint.netdev==dev);if(!owner->hooked){owner->hooked=true;hooks++;}return 0;}
 owner=calloc(1,sizeof(*owner));assert(owner);owner->endpoint.netdev=dev;owner->id=1;owner->hooked=true;
 dev->refs++;dev->fe=true;attached++;hooks++;return 0;
}
#define dev_net(dev) (dev)
static void nf_unregister_net_hook(struct net_device *dev,int *hook){assert(owner&&dev==owner->endpoint.netdev&&hook==&owner->hook);hooks--;}
static int rtl9617c_ni_wifi_detach(void *ni,unsigned id){
 (void)ni;assert(owner&&id==1&&!owner->hooked);if(detach_error)return detach_error;
 owner->endpoint.netdev->refs--;detached++;return 0;
}
static void rtl9617c_ni_fe_remove_port(void *ni,struct net_device *dev){(void)ni;dev->fe=false;}
static void list_del(int *list){assert(list==&owner->list);owner=NULL;}
#define kfree(p) free(p)
'''
code += function('wifi_del') + function('wifi_event')
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
  /* Failed drain keeps FE ownership with NI; returning to AP/STA only
   * reinstalls the hook, so deleting that FE port would lose offload. */
  w.iftype=initial;wifi_event(NULL,NETDEV_REGISTER,&dev);w.iftype=final;
  detach_error=-ETIMEDOUT;wifi_event(NULL,NETDEV_UP,&dev);
  assert(owner&&!hooks&&dev.refs==2&&dev.fe);
  w.iftype=initial;wifi_event(NULL,NETDEV_UP,&dev);
  assert(owner&&hooks==1&&dev.refs==2&&dev.fe);
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
 puts("NI notifier: AP/STA to mesh/monitor removal and UP, failed-drain AP/STA reopen, repeated UNREGISTER, busy-drain retry, AP DOWN/UP and wired exclusion passed");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-notifier-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
