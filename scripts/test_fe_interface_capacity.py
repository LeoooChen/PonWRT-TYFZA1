#!/usr/bin/env python3
"""Exercise production EIF ownership past64 flows and last-owner retry."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
native = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe.c').read_text()
def function(name):
    start = re.search(r'^static int ' + name + r'\(', native, re.M).start()
    end = native.index('{', start) + 1
    depth = 1
    while depth:
        depth += (native[end] == '{') - (native[end] == '}')
        end += 1
    return native[start:end]

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#define FE_COUNTERS 64
#define BIT(n) (1U<<(n))
struct rtl_fe_interface {unsigned int users,saved,source_mac;bool counted;};
struct rtl_fe_flow {unsigned int source_mac,counter;bool counted,interface_owned;};
struct rtl_fe {unsigned long counters[1];struct rtl_fe_interface interfaces[64];
 unsigned int table[64],data;bool fail_read,fail_write;};
static unsigned int find_first_zero_bit(unsigned long *bits,unsigned int size){
 for(unsigned int i=0;i<size;i++)if(!(bits[i/64]&(1UL<<(i%64))))return i;
 return size;
}
static void __set_bit(unsigned int i,unsigned long *b){b[i/64]|=1UL<<(i%64);}
static void __clear_bit(unsigned int i,unsigned long *b){b[i/64]&=~(1UL<<(i%64));}
static unsigned int fe_read(struct rtl_fe *fe,unsigned int reg){assert(reg==0x3004);return fe->data;}
static void fe_write(struct rtl_fe *fe,unsigned int reg,unsigned int value){assert(reg==0x3004);fe->data=value;}
static int fe_access(struct rtl_fe *fe,unsigned int reg,unsigned int value){
 assert(reg==0x3000);unsigned int index=value&63;
 if(value&BIT(30)) {
  if(fe->fail_write){fe->fail_write=false;return -ETIMEDOUT;}
  fe->table[index]=fe->data;
 } else {
  if(fe->fail_read){fe->fail_read=false;return -ETIMEDOUT;}
  fe->data=fe->table[index];
 }
 return 0;
}
'''
test = r'''
int main(void){
 struct rtl_fe fe={};struct rtl_fe_flow *flows=calloc(4096,sizeof(*flows));assert(flows);
 for(unsigned int i=0;i<64;i++)fe.table[i]=0x50000000+i;
 for(unsigned int i=0;i<4096;i++) {
  flows[i].source_mac=i%15;
  assert(!fe_interface_get(&fe,&flows[i]));
  assert(fe.interfaces[flows[i].counter].source_mac==i%15);
  assert(flows[i].counted==(i<64));
  assert(fe.table[flows[i].counter]==(BIT(18)|((i%15+1)<<19)));
 }
 for(unsigned int i=0;i<64;i++)assert(!fe_interface_put(&fe,&flows[i]));
 /* Reusing an EIF for exact counting must not restore it while shared flows remain. */
 struct rtl_fe_flow replacement={.source_mac=0};
 assert(!fe_interface_get(&fe,&replacement) && replacement.counted);
 assert(!fe_interface_put(&fe,&replacement));
 for(unsigned int i=64;i<4096;i++)assert(!fe_interface_put(&fe,&flows[i]));
 assert(!fe.counters[0]);
 for(unsigned int i=0;i<64;i++)assert(!fe.interfaces[i].users && fe.table[i]==0x50000000+i);
 struct rtl_fe_flow failed={.source_mac=3};fe.fail_read=true;
 assert(fe_interface_get(&fe,&failed)==-ETIMEDOUT && !failed.interface_owned && !fe.counters[0]);
 fe.fail_write=true;
 assert(fe_interface_get(&fe,&failed)==-ETIMEDOUT && failed.interface_owned);
 fe.fail_write=true;
 assert(fe_interface_put(&fe,&failed)==-ETIMEDOUT && fe.interfaces[failed.counter].users==1);
 assert(!fe_interface_put(&fe,&failed) && !fe.counters[0]);
 free(flows);
 puts("Native EIF:4096 flows/15 MACs,64 exact MIB owners,shared lifetime,counter reuse and restore retry passed");
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='fe-interface-') as work:
    path = Path(work) / 'interfaces.c'
    binary = Path(work) / 'interfaces'
    path.write_text(common + function('fe_interface_get') + function('fe_interface_put') + test)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', str(path), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/fe-interface-capacity.json').write_text(json.dumps({
    'production_source_sha256': hashlib.sha256(native.encode()).hexdigest(),
    'flows': 4096, 'source_MACs': 15, 'exact_MIB_owners': 64,
    'shared_interface_refcounts_and_last_owner_restore': True,
    'counter_reassignment_without_cross_flow_accounting': True,
    'failed_register_restore_retains_owner_until_retry': True,
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
