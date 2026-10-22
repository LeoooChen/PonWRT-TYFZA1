#!/usr/bin/env python3
"""Execute production CTC-to-NI PM mapping and all five hardware MIB slots."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
ni = (drivers / 'rtl9617c-xram.c').read_text()
pon = (drivers / 'rtl9617c-pon.c').read_text()

def function(source, name):
    start = re.search(r'^(?:static )?(?:int|void|ssize_t) ' + name + r'\(', source, re.M).start()
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
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define READ_ONCE(v) (v)
#define BIT(n) (1U<<(n))
#define RTL_NI_MIB_COUNTERS 52
#define RTL_PON_PM_RECORD_BYTES (8+37*8)
#define HZ 100
#define dev_err_ratelimited(...) ((void)0)
struct mutex {};
struct work_struct {};
struct delayed_work {struct work_struct work;};
struct device {void *data;struct device *parent;};
struct net_device {struct device dev;};
struct rtl_xram {
 struct net_device *netdev,*ports[5];unsigned int registered_ports;
 struct delayed_work mib_work;struct mutex mib_lock;
 unsigned long long mib_total[5][RTL_NI_MIB_COUNTERS];
};
struct rtl9617c_pon {struct device *ni_dev;struct mutex lock,control_lock;uint64_t epon_mib_generation;};
struct file {};struct bin_attribute {};struct kobject {struct device *dev;};
struct rtl_dq_io {void *context;void *read,*write,*indirect;};
static int rtl_mib_read,rtl_mib_write,rtl_mib_indirect;
static bool external_phy;
static unsigned int seen,last_port,reads;
static struct rtl_xram *worker_priv;
static void *dev_get_drvdata(struct device *d){return d->data;}
static void *netdev_priv(void *p){return p;}
static struct device *kobj_to_dev(struct kobject *k){return k->dev;}
static void mutex_lock(struct mutex *m){}
static void mutex_unlock(struct mutex *m){}
static bool of_machine_is_compatible(const char *s){return external_phy;}
#define to_delayed_work(w) (w)
#define container_of(p,t,m) worker_priv
static void schedule_delayed_work(struct delayed_work *w,int delay){}
static int rtl_ni_mib_read(const struct rtl_dq_io *io,unsigned int p,unsigned long long *raw){
 seen|=BIT(p);last_port=p;reads++;
 for(int i=0;i<52;i++)raw[i]=p*100+i;
 return 0;
}
static void rtl_ni_mib_pm(unsigned long long *total,const unsigned long long *raw,unsigned long long *out){
 for(int i=0;i<52;i++)total[i]=raw[i];
 for(int i=0;i<37;i++)out[i]=total[i];
}
static int rtl_pon_epon_pm(struct rtl9617c_pon *p,unsigned long long *c){
 last_port=99;for(int i=0;i<37;i++)c[i]=999;return 0;
}
static void put_unaligned_be64(uint64_t n,char *p){for(int i=0;i<8;i++)p[i]=n>>(56-8*i);}
'''
# These are pointers in the real IO callback structure.
shim = shim.replace('static int rtl_mib_read,rtl_mib_write,rtl_mib_indirect;',
                    'static void *rtl_mib_read,*rtl_mib_write,*rtl_mib_indirect;')
mib_slots = re.search(r'mib_total\[(\d+)\]\[RTL_NI_MIB_COUNTERS\]', ni).group(1)
shim = shim.replace('mib_total[5][RTL_NI_MIB_COUNTERS]', f'mib_total[{mib_slots}][RTL_NI_MIB_COUNTERS]')
code = shim + function(ni, 'rtl9617c_ni_pm_read') + function(ni, 'rtl_mib_work') + function(pon, 'pm_counters_read')
code += r'''
int main(void){
 struct rtl_xram priv={0};struct device ni_dev={.data=&priv};
 struct net_device netdev={.dev={.parent=&ni_dev}};priv.netdev=&netdev;
 struct rtl9617c_pon pon={.ni_dev=&ni_dev};
 struct device pon_dev={.data=&pon};struct kobject kobj={.dev=&pon_dev};
 char out[RTL_PON_PM_RECORD_BYTES];unsigned long long counters[37];worker_priv=&priv;
 for(int board=0;board<2;board++){
  external_phy=board;priv.registered_ports=board?0x17:0xf;
  for(unsigned int uni=1;uni<=4;uni++){
   unsigned int expected=board && uni==1?4:4-uni;
   assert(pm_counters_read(NULL,&kobj,NULL,out,uni*sizeof(out),sizeof(out))==sizeof(out));
   assert(last_port==expected && priv.mib_total[expected][0]==expected*100);
  }
  seen=0;rtl_mib_work(NULL);assert(seen==priv.registered_ports);
  priv.registered_ports&=~BIT(1);seen=0;rtl_mib_work(NULL);assert(seen==priv.registered_ports);
 }
 assert(pm_counters_read(NULL,&kobj,NULL,out,0,sizeof(out))==sizeof(out) && last_port==99);
 assert(pm_counters_read(NULL,&kobj,NULL,out,sizeof(out)*5,sizeof(out))==-EINVAL);
 assert(pm_counters_read(NULL,&kobj,NULL,out,1,sizeof(out))==-EINVAL);
 assert(rtl9617c_ni_pm_read(&ni_dev,4,counters)==0);
 assert(rtl9617c_ni_pm_read(&ni_dev,5,counters)==-EINVAL);
 assert(priv.mib_total[3][0]==300 && priv.mib_total[4][0]==400);
 puts("Both boards: CTC jack order, NI0..4 counters, independent totals and missing-port sampling passed");
}
'''
with tempfile.TemporaryDirectory(prefix='lan-pm-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-sign-compare',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
