#!/usr/bin/env python3
"""Exercise actual sink rule rollback and safe endpoint release on failures."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'tools/iperf-pon/iperf_pon_sink.c').read_text()


def function(name):
    at = source.index(name + '(')
    start = source.rfind('\n', 0, at) + 1
    end = source.index('{', at) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <arpa/inet.h>
typedef uint64_t u64;
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define ETH_P_IP 0x800
#define cpu_to_be16(x) htons(x)
#define TC_SETUP_CLSFLOWER 1
#define THIS_MODULE ((void *)1)
enum flow_cls_command {FLOW_CLS_REPLACE,FLOW_CLS_DESTROY,FLOW_CLS_STATS};
struct rule {struct {void *key;} match;};
struct flow_cls_offload {enum flow_cls_command command;unsigned long cookie;struct rule *rule;
 struct {int protocol;bool skip_sw;} common;struct {u64 pkts,bytes;} stats;};
struct callback {int (*cb)(int,struct flow_cls_offload *,void *);void *cb_priv;};
struct device {void *parent;};struct net_device {struct device dev;};
struct kernel_param {int unused;};
static int key[4];static bool flow_installed[4],attached;static u64 packets,bytes,flow_packets[4];
static struct rule rule_object,*rule=&rule_object;
static struct net_device lan_object,*lan=&lan_object,*sink=&lan_object,*bridge=&lan_object,*radio=&lan_object;
static int identity=3,release_lock,locked,released,detaches,destroy_failure=-1,replace_failure=-1,detach_failure;
static void mutex_lock(int *x){} static void mutex_unlock(int *x){}
static void rtnl_lock(void){assert(!locked);locked=1;}static void rtnl_unlock(void){assert(locked);locked=0;}
static int kstrtobool(const char *s,bool *v){if(!strcmp(s,"1")){*v=true;return 0;}return -1;}
static int callback_fn(int type,struct flow_cls_offload *cls,void *priv){
 int i=((int *)cls->cookie)-key;assert(type==1 && i>=0 && i<4);
 if(cls->command==FLOW_CLS_REPLACE && i==replace_failure)return -EIO;
 if(cls->command==FLOW_CLS_DESTROY && i==destroy_failure)return -EIO;
 return 0;
}
static struct callback cb={callback_fn,0},*callback=&cb;
static int rtl9617c_ni_wifi_detach(void *p,int id){detaches++;return detach_failure?-EAGAIN:0;}
static void rtl9617c_ni_fe_remove_port(void *p,struct net_device *d){}
static void unbind(void){}static void kfree(void *p){}static void sink_gro_flush(void){}
static void netdev_rx_handler_unregister(struct net_device *d){}
static void unregister_netdevice(struct net_device *d){}static void free_netdev(struct net_device *d){}
static void dev_put(struct net_device *d){}static void module_put(void *p){released++;}
'''

checks = r'''
int main(void){
 replace_failure=2;
 assert(command(FLOW_CLS_REPLACE)==-EIO);
 assert(!flow_installed[0] && !flow_installed[1] && !flow_installed[2]);
 replace_failure=-1;assert(!command(FLOW_CLS_REPLACE));
 attached=true;destroy_failure=2;
 assert(release_set("1",0)==-EIO && attached && !released && !detaches && !locked);
 assert(!flow_installed[0] && !flow_installed[1] && flow_installed[2] && flow_installed[3]);
 destroy_failure=-1;detach_failure=1;
 assert(release_set("1",0)==-EAGAIN && attached && !released && detaches==1 && !locked);
 assert(!flow_installed[2] && !flow_installed[3]);
 detach_failure=0;
 assert(!release_set("1",0) && !attached && released==1 && detaches==2 && !locked);
 assert(!release_set("1",0) && released==1);
 assert(release_set("0",0)==-EINVAL);
}
'''

with tempfile.TemporaryDirectory(prefix='iperf-pon-release-') as directory:
    p = Path(directory)
    (p / 'test.c').write_text(prefix + function('command') + function('release_set') + checks)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
print('iperf sink: partial flow rollback and retry after rule/NI drain failure passed')
