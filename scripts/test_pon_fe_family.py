#!/usr/bin/env python3
"""Execute PON invalidation and FE policy replacement across protocol families."""
from pathlib import Path
import re,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
drivers=root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
pon_source=(drivers/'rtl9617c-pon-path.c').read_text()
fe_source=(drivers/'rtl9617c-fe.c').read_text()
def function(source,name):
 start=re.search(r'^(?:static )?(?:int|void) '+name+r'\(',source,re.M).start()
 end=source.index('{',start)+1;depth=1
 while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]+'\n'
model=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-pon-path.h"
#define GFP_KERNEL 0
#define kvmalloc(n,f) malloc(n)
#define kvfree(p) free(p)
#define rcu_access_pointer(p) (p)
#define lockdep_assert_held(p) assert(*(p))
struct net_device {bool carrier;};
struct rtl_fe_port {struct net_device *netdev;};
struct rtl_fe_flow {struct rtl_fe_port *input,*output;};
struct rtl_fe {int lock;struct rtl_fe_port port;struct rtl_itu_paths *itu_paths;struct net_device *itu_netdev;};
/* This fixture has no installed flows; policy identity is the observed state. */
#define list_for_each_entry_safe(f,n,h,m) for(f=NULL,n=NULL;f;f=NULL)
static struct rtl_fe_port *fe_port(struct rtl_fe *fe,struct net_device *dev){assert(fe->port.netdev==dev);return &fe->port;}
static int fe_delete(struct rtl_fe *fe,struct rtl_fe_flow *flow){(void)fe;(void)flow;assert(0);return 0;}
static void mutex_lock(int *lock){assert(!*lock);*lock=1;}
static void mutex_unlock(int *lock){assert(*lock);*lock=0;}
struct rtl9617c_pon {int lock,mode,itu_paths_error;bool itu_paths_ready;unsigned long long itu_path_epoch;struct net_device *data_devices[1];void *ni_dev;};
static void netif_carrier_off(struct net_device *dev){dev->carrier=false;}
'''
bridge=r'''
static int rtl9617c_ni_itu_paths(void *context,struct net_device *dev,const struct rtl_itu_paths *paths)
{return rtl_fe_set_itu_paths(context,dev,paths);}
'''
test=r'''
int main(void){
 setvbuf(stdout,NULL,_IOLBF,0);
 for(int mode=RTL_PON_GPON;mode<RTL_PON_MODE_COUNT;mode++){
  struct net_device dev={.carrier=true};
  struct rtl_fe fe={.port={&dev}};
  struct rtl_itu_paths paths={.count=1,.path={{.gem_id=4000,.index=8,.tcont=1,.pbits=255,.downstream_pbits=255,.vlan=65535}}};
  assert(!rtl_fe_set_itu_paths(&fe,&dev,&paths));
  struct rtl9617c_pon pon={.lock=1,.mode=mode,.itu_paths_ready=true,.data_devices={&dev},.ni_dev=&fe};
  INVALIDATE
  assert(!pon.itu_paths_ready && !dev.carrier && !pon.itu_paths_error);
  printf("mode=%d FE_retains_ITU=%d paths=%s\n",mode,fe.itu_netdev==&dev,fe.itu_paths?"present":"null");
  assert(fe.itu_netdev==&dev && fe.itu_paths && !fe.itu_paths->count);
  assert(!rtl_itu_path_flow(fe.itu_paths,65535));
  assert(!rtl_itu_path_downstream(fe.itu_paths,65535,255));
  assert(!rtl_fe_set_itu_paths(&fe,&dev,&paths));
  assert(rtl_itu_path_flow(fe.itu_paths,65535)->index==8);
  RELEASE_EPON
  assert(!fe.itu_netdev && !fe.itu_paths);
 }
 puts("Actual PON/NI FE policy handoff: ITU invalidation retains family with no business mapping; EPON transition releases ITU identity");
}
'''
updated='int rtl_pon_itu_paths_invalidate' in pon_source
test=test.replace('INVALIDATE','assert(!rtl_pon_itu_paths_invalidate(&pon,mode));' if updated else 'rtl_pon_itu_paths_invalidate(&pon);')
test=test.replace('RELEASE_EPON','assert(!rtl_pon_itu_paths_invalidate(&pon,RTL_PON_EPON_D10G));' if updated else 'assert(!rtl_fe_set_itu_paths(&fe,&dev,NULL));')
with tempfile.TemporaryDirectory(prefix='pon-fe-family-',dir=root/'artifacts') as temp:
 work=Path(temp);c=work/'test.c';binary=work/'test'
 c.write_text(model+function(fe_source,'rtl_fe_set_itu_paths')+bridge+function(pon_source,'rtl_pon_itu_paths_invalidate')+test)
 subprocess.run(['cc','-O1','-Wall','-Wextra','-Werror','-Wno-unused-but-set-variable','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(drivers),str(c),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
