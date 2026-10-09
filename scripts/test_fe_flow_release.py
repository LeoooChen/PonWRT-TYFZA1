#!/usr/bin/env python3
"""Exercise production flow teardown with shared MACs and a failed CAM restore."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
native = (drivers / 'rtl9617c-fe.c').read_text()
def function(name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{',native,re.M).start()
    end = native.index('{',start)+1
    depth = 1
    while depth:
        depth += (native[end]=='{')-(native[end]=='}')
        end += 1
    return native[start:end]+'\n'

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include "rtl9617c-fe-pipeline.h"
typedef uint8_t u8;typedef uint16_t u16;typedef uint32_t u32;
typedef uint32_t __le32;typedef uint64_t u64;
#define ETH_ALEN 6
#define BIT(n) (1U<<(n))
#define dma_wmb() ((void)0)
#define kfree(p) free(p)
struct list_head {struct list_head *next,*prev;};
static void init_list(struct list_head *h){h->next=h->prev=h;}
static void list_add_tail(struct list_head *n,struct list_head *h){n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n;}
#define list_for_each_entry_safe(p,n,h,m) for(struct list_head *cursor=(h)->next,*next_cursor=cursor->next;cursor!=(h) && ((p)=container_of(cursor,__typeof__(*(p)),m));cursor=next_cursor,next_cursor=cursor->next)
static void list_del(struct list_head *n){n->prev->next=n->next;n->next->prev=n->prev;}
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry(p,h,m) for(struct list_head *cursor=(h)->next;cursor!=(h) && ((p)=container_of(cursor,__typeof__(*(p)),m));cursor=cursor->next)
static void __clear_bit(unsigned int bit,unsigned long *words){words[bit/64]&=~(1UL<<(bit%64));}
static void set_bit(unsigned int bit,unsigned long *words){words[bit/64]|=1UL<<(bit%64);}
static bool test_bit(unsigned int bit,unsigned long *words){return (words[bit/64]>>(bit%64))&1;}
static bool ether_addr_equal(const u8 *a,const u8 *b){return !memcmp(a,b,6);}
struct rtl_fe;
struct net_device {};
struct rtl_fe_port {struct list_head list;struct rtl_fe *fe;struct net_device *netdev;unsigned int source,destination,llid;bool retired;};
struct rtl_fe_mac {unsigned int users,saved[5];u8 address[6];};
struct rtl_fe_interface {unsigned int users,saved,source_mac;bool counted;};
struct rtl_fe {
 struct list_head flows,ports;
 void *memory;
 struct rtl_fe_mac source_macs[15];
 struct rtl_fe_interface interfaces[64];
 unsigned long hashes[RTL_FE_HASH_ENTRIES/64],counters[1];
 u64 deletes;
 unsigned int registers[0x4000/4],ages[RTL_FE_HASH_ENTRIES/32][4],cam[5];
 unsigned int deletes_FDB,restores_CAM,restores_IF,cache_invalidates;
 bool fail_CAM;
};
static unsigned int fe_read(void *context,unsigned int reg){return ((struct rtl_fe *)context)->registers[reg/4];}
static void fe_write(void *context,unsigned int reg,unsigned int value){((struct rtl_fe *)context)->registers[reg/4]=value;}
static int fe_access(void *context,unsigned int reg,unsigned int value){
 struct rtl_fe *fe=context;
 if(reg==0x3928){
  unsigned int row=value&(RTL_FE_HASH_ENTRIES/32-1);
  for(unsigned int i=0;i<4;i++) {
   if(value&BIT(30))fe->ages[row][i]=fe_read(fe,0x3938-i*4);
   else fe_write(fe,0x3938-i*4,fe->ages[row][i]);
  }
 } else if(reg==0x38b0)fe->cache_invalidates++;
 else if(reg==0x1ca0){assert((value&255)==0x85);fe->deletes_FDB++;fe_write(fe,0x1c2c,5);}
 else if(reg==0x3200){
  if(fe->fail_CAM){fe->fail_CAM=false;return -ETIMEDOUT;}
  fe->restores_CAM++;
  for(unsigned int i=0;i<5;i++)fe->cam[i]=fe_read(fe,0x3214-i*4);
 } else if(reg==0x3000)fe->restores_IF++;
 else assert(0);
 return 0;
}
static struct rtl_dq_io fe_io(struct rtl_fe *fe){return (struct rtl_dq_io){fe,fe_read,fe_write,fe_access};}
'''
test = r'''
int main(void){
 struct rtl_fe fe={};init_list(&fe.flows);init_list(&fe.ports);
 struct rtl_fe_port *port=calloc(1,sizeof(*port));assert(port);port->retired=true;list_add_tail(&port->list,&fe.ports);
 fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);
 fe.source_macs[0].users=2;
 for(unsigned int i=0;i<5;i++)fe.source_macs[0].saved[i]=0x51000000+i;
 struct rtl_fe_flow *a=calloc(1,sizeof(*a)),*b=calloc(1,sizeof(*b));assert(a&&b);
 *a=(struct rtl_fe_flow){.hash_index=1280,.counter=31,.source_mac=0,.crc16=0x5141,
  .destination_created=true,.published=true,.installed=true,.counted=true,.source_owned=true,.destination_owned=true,.interface_owned=true,
  .destination={2,0x77,0x66,0x55,0x44,0x33}};
 a->input=port;
 *b=*a;b->hash_index=65344;b->counter=30;b->crc16=0xff4b;
 fe.interfaces[31]=(struct rtl_fe_interface){.users=1,.counted=true};
 fe.interfaces[30]=(struct rtl_fe_interface){.users=1,.counted=true};
 list_add_tail(&a->list,&fe.flows);list_add_tail(&b->list,&fe.flows);
 set_bit(a->hash_index,fe.hashes);set_bit(b->hash_index,fe.hashes);
 set_bit(a->counter,fe.counters);set_bit(b->counter,fe.counters);
 ((u32 *)fe.memory)[1280]=0x12345678;((u32 *)fe.memory)[65344]=0x87654321;
 fe_collect_ports(&fe);assert(fe.ports.next==&port->list);
 assert(!fe_delete(&fe,a));
 fe_collect_ports(&fe);assert(fe.ports.next==&port->list);
 assert(fe.source_macs[0].users==1 && !fe.deletes_FDB && !fe.restores_CAM);
 assert(!test_bit(1280,fe.hashes) && test_bit(65344,fe.hashes));
 fe.fail_CAM=true;
 assert(fe_delete(&fe,b)==-ETIMEDOUT);
 fe_collect_ports(&fe);assert(fe.ports.next==&port->list);
 assert(fe.deletes_FDB==1 && fe.source_macs[0].users==1);
 assert(!b->published && !b->destination_owned && b->source_owned && b->interface_owned);
 assert(test_bit(65344,fe.hashes) && test_bit(30,fe.counters));
 assert(!((u32 *)fe.memory)[65344]);
 assert(!fe_delete(&fe,b));
 fe_collect_ports(&fe);assert(fe.ports.next==&fe.ports);
 assert(fe.source_macs[0].users==0 && fe.deletes_FDB==1 && fe.restores_CAM==1);
 assert(fe.cache_invalidates==2 && fe.restores_IF==2 && fe.deletes==2);
 for(unsigned int i=0;i<5;i++)assert(fe.cam[i]==0x51000000+i);
 assert(fe.flows.next==&fe.flows && !test_bit(65344,fe.hashes) && !test_bit(30,fe.counters));
 free(fe.memory);
 puts("Native FE teardown:shared FDB/source MAC,last-owner restore,failed CAM retry and DMA/hash/counter ownership passed");
}
'''
parts = [common,re.search(r'struct rtl_fe_flow \{.*?\n\};',native,re.S)[0]]
parts += [function(n) for n in ('fe_collect_ports','fe_source_put','fe_interface_put','fe_destination_put','fe_delete')]
parts.append(test)
with tempfile.TemporaryDirectory(dir=root/'artifacts',prefix='fe-release-') as work:
    source=Path(work)/'release.c';binary=Path(work)/'release'
    source.write_text('\n'.join(parts))
    subprocess.run(['cc','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',
        '-I'+str(drivers),str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
(root/'logs/fe-flow-release.json').write_text(json.dumps({
    'production_source_sha256':hashlib.sha256(native.encode()).hexdigest(),
    'shared_MAC_last_owner_release':True,'retired_port_retained_until_failed_flow_cleanup_retries_finish':True,'CAM_restore_failure_retried_without_double_release':True,
    'hash_and_counter_slots_retained_until_release_completed':True,
    'sanitizers':['ASan','UBSan'],'hardware_fault_injection':False,
},indent=2)+'\n')
