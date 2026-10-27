#!/usr/bin/env python3
"""Exercise feed teardown with a modeled VP reset failure and retry."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / 'tools/iperf-pon/iperf_pon_feed.c').read_text()


def function(name):
    at = source.index('static int ' + name + '(') + len('static int ')
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
typedef uint32_t u32;
#define BIT(x) (1U << (x))
#define THIS_MODULE ((void *)1)
#define readl_poll_timeout(addr, status, cond, delay, timeout) (reset_failure ? -ETIMEDOUT : 0)
struct kernel_param {int unused;};
struct device {void *parent;};struct net_device {struct device dev;};
static struct net_device obj,*feed=&obj,*lan=&obj,*bridge=&obj,*radio=&obj;
static u32 regs[1024],saved[6]={0,11,12,13,14};
static void *dma=regs,*vp=regs+512,*memory=regs,*eq_monitor=regs+768;static int address,release_lock;
static bool active=true;static int reset_failure,unregisters,frees,unmaps,puts,releases;
static void mutex_lock(int *x){} static void mutex_unlock(int *x){}
static int kstrtobool(const char *s,bool *v){if(!strcmp(s,"1")){*v=true;return 0;}return -1;}
static u32 readl(void *p){return *(u32 *)p;}static void writel(u32 x,void *p){*(u32 *)p=x;}
static void unregister_netdev(struct net_device *d){unregisters++;}
static void free_netdev(struct net_device *d){}
static void dma_free_coherent(void *p,int n,void *m,int a){frees++;}
static void iounmap(void *p){unmaps++;}static void dev_put(struct net_device *d){puts++;}
static void module_put(void *p){releases++;}
'''
checks = r'''
int main(void){
 reset_failure=1;
 assert(release_set("1",0)==-ETIMEDOUT && active && !feed);
 assert(unregisters==1 && !frees && !unmaps && !puts && !releases);
 assert(readl(vp+8)==0 && readl(vp+12)==0);
 reset_failure=0;
 assert(!release_set("1",0) && !active);
 assert(unregisters==1 && frees==1 && unmaps==2 && puts==3 && releases==1 && !eq_monitor);
 assert(readl(vp+8)==11 && readl(vp+12)==12);
 assert(!release_set("1",0) && releases==1);
 assert(release_set("0",0)==-EINVAL);
}
'''
with tempfile.TemporaryDirectory(prefix='iperf-pon-dma-') as directory:
    p = Path(directory)
    (p / 'test.c').write_text(prefix + function('restore') + function('release_set') + checks)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                    str(p / 'test.c'), '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
print('iperf feed: DMA retained on VP reset failure, then released on retry')
