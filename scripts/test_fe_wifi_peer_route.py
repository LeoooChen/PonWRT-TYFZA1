#!/usr/bin/env python3
"""Run the native peer-route code against a checked FE transaction model."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (driver / 'rtl9617c-fe.c').read_text()


def function(name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include "rtl9617c-fe-format.h"
typedef uint8_t u8; typedef uint32_t u32;
#define ETH_ALEN 6
#define GFP_KERNEL 0
#define RTL_FE_ACTION_OFFSET 0
#define RTL_FE_ACTION_BYTES 48
#define kzalloc(n,f) calloc(1,n)
#define kfree(p) free(p)
#define ether_addr_equal(a,b) (!memcmp(a,b,6))
#define ether_addr_copy(a,b) memcpy(a,b,6)
struct list_head {struct list_head *next,*prev;};
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry(p,h,m) for(struct list_head *c=(h)->next;c!=(h) && ((p)=container_of(c,__typeof__(*(p)),m));c=c->next)
#define list_for_each_entry_safe(p,n,h,m) for(struct list_head *c=(h)->next,*nc=c->next;c!=(h) && ((p)=container_of(c,__typeof__(*(p)),m),(n)=container_of(nc,__typeof__(*(n)),m),1);c=nc,nc=c->next)
static void init(struct list_head *h){h->next=h->prev=h;}
static void list_add_tail(struct list_head *n,struct list_head *h){n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n;}
static void list_del(struct list_head *n){n->prev->next=n->next;n->next->prev=n->prev;}
struct net_device {int id;};
struct rtl_fe_port {struct list_head list;struct net_device *netdev;unsigned int wifi_id,destination;bool retired;};
struct rtl_fe_wifi_peer {struct list_head list;struct rtl_fe_port *port;u8 address[6];unsigned int destination;};
struct rtl_fe_flow {struct list_head list;struct rtl_fe_port *output;u8 destination[6];unsigned int hash_index,crc16;bool installed,deleting;};
struct rtl_fe {struct list_head ports,flows,wifi_peers;int lock;void *memory;};
struct rtl_dq_io {struct rtl_fe *fe;};
static struct rtl_dq_io fe_io(struct rtl_fe *fe){return (struct rtl_dq_io){fe};}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static unsigned int phase,active_index,transactions;
static int rtl_fe_main_age(struct rtl_dq_io *io,unsigned int i,unsigned int age){
 assert(io->fe->lock);
 if(!age){assert(!phase);phase=1;active_index=i;}
 else {assert(phase==3 && i==active_index);phase=0;transactions++;}
 return 0;
}
static int rtl_fe_cache_command(struct rtl_dq_io *io,unsigned int i,unsigned int crc,unsigned int op){
 assert(io->fe->lock && phase==1 && i==active_index && crc==i+100 && op==1);phase=2;return 0;
}
static void dma_wmb(void){assert(phase==2);phase=3;}
'''
test = r'''
int main(void){
 struct rtl_fe fe={}; struct net_device a={},b={},missing={};
 struct rtl_fe_port ports[2]={{.netdev=&a,.wifi_id=1,.destination=16},{.netdev=&b,.wifi_id=2,.destination=16}};
 struct rtl_fe_flow flows[4]={};u32 memory[4][12],original[4][12],expected[12];
 const u8 mac[6]={2,1,2,3,4,5},other[6]={2,1,2,3,4,6};
 init(&fe.ports);init(&fe.flows);init(&fe.wifi_peers);fe.memory=memory;
 for(unsigned int i=0;i<2;i++)list_add_tail(&ports[i].list,&fe.ports);
 for(unsigned int i=0;i<4;i++){
  flows[i].output=&ports[i==2];flows[i].installed=true;flows[i].deleting=i==3;
  flows[i].hash_index=i;flows[i].crc16=i+100;memcpy(flows[i].destination,i==1?other:mac,6);
  list_add_tail(&flows[i].list,&fe.flows);
  memset(memory[i],0x55+i,48);rtl_fe_action_set(memory[i],RTL_FE_MCGID,16);
 }
 memcpy(original,memory,sizeof(memory));
 assert(rtl_fe_wifi_peer_route(&fe,&missing,mac,19)==-ENODEV);
 assert(!rtl_fe_wifi_peer_route(&fe,&a,mac,19));assert(transactions==1);
 memcpy(expected,original[0],48);rtl_fe_action_set(expected,RTL_FE_MCGID,19);
 assert(!memcmp(memory[0],expected,48) && !memcmp(memory[1],original[1],144));
 assert(fe_wifi_peer(&fe,&ports[0],mac) && !fe_wifi_peer(&fe,&ports[1],mac));
 assert(!rtl_fe_wifi_peer_route(&fe,&a,mac,false));assert(transactions==2);
 assert(!memcmp(memory,original,sizeof(memory)) && fe.wifi_peers.next==&fe.wifi_peers);
 assert(!rtl_fe_wifi_peer_route(&fe,&a,mac,18));
 memcpy(expected,original[0],48);rtl_fe_action_set(expected,RTL_FE_MCGID,18);
 assert(!memcmp(memory[0],expected,48) && fe_wifi_peer(&fe,&ports[0],mac)->destination==18);
 fe_wifi_forget_port(&fe,&ports[0]);
 assert(!fe_wifi_peer(&fe,&ports[0],mac));
 puts("FE peer route: exact peer/AP matching, NAT bytes preserved, age/cache withdrawal ordering, restore and removal passed");
}
'''
with tempfile.TemporaryDirectory(prefix='fe-peer-route-', dir=root / 'artifacts') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(common + ''.join(function(n) for n in
        ('fe_port', 'fe_wifi_peer', 'fe_wifi_forget_port', 'rtl_fe_wifi_peer_route')) + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-I', str(driver), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
