#!/usr/bin/env python3
"""Check production MIB-to-conntrack byte accounting and idle timestamps."""
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
source=(root/'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-fe.c').read_text()
start=re.search(r'^static int fe_stats\(',source,re.M).start()
end=source.index('{',start)+1;depth=1
while depth:
    depth+=(source[end]=='{')-(source[end]=='}');end+=1
common=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
typedef uint64_t u64;typedef uint32_t u32;
#define ETH_HLEN 14
#define FLOW_ACTION_HW_STATS_DELAYED 1
static unsigned long jiffies=100;
struct rtl_fe {u64 bytes;u32 packets;};
struct rtl_fe_flow {bool published;unsigned int counter;u64 bytes;u32 packets;unsigned long lastused;};
struct flow_cls_offload {struct {u64 bytes,packets;unsigned long lastused;} stats;};
struct rtl_dq_io {struct rtl_fe *fe;};
static struct rtl_dq_io fe_io(struct rtl_fe *fe){return (struct rtl_dq_io){fe};}
static int rtl_fe_tx_counter_read(struct rtl_dq_io *io,unsigned int counter,unsigned int *words){
 assert(counter==31);words[0]=io->fe->bytes;words[1]=io->fe->bytes>>32;words[2]=io->fe->packets;return 0;
}
#define flow_stats_update(s,b,p,d,u,h) do{assert((d)==0 && (h)==1);(s)->bytes+=(b);(s)->packets+=(p);(s)->lastused=(u);}while(0)
'''
test=r'''
int main(void){
 struct rtl_fe fe={.bytes=5355,.packets=22};
 struct rtl_fe_flow flow={.published=true,.counter=31,.bytes=5000,.packets=17,.lastused=80};
 struct flow_cls_offload cls={};
 assert(!fe_stats(&fe,&flow,&cls));
 assert(cls.stats.packets==5 && cls.stats.bytes==285 && cls.stats.lastused==100);
 jiffies=120;assert(!fe_stats(&fe,&flow,&cls));
 assert(cls.stats.packets==5 && cls.stats.bytes==285 && cls.stats.lastused==100);
 flow.packets=UINT32_MAX-2;fe.packets=2;flow.bytes=6000;fe.bytes=6355;
 assert(!fe_stats(&fe,&flow,&cls));
 assert(cls.stats.packets==10 && cls.stats.bytes==570 && cls.stats.lastused==120);
 flow.published=false;assert(fe_stats(&fe,&flow,&cls)==-ENOENT);
 puts("Native FE stats:wire355 bytes/five packets ->IP285 bytes;idle lastused unchanged;packet counter wrap passed");
}
'''
with tempfile.TemporaryDirectory(dir=root/'artifacts',prefix='fe-stats-') as work:
    path=Path(work)/'stats.c';binary=Path(work)/'stats'
    path.write_text(common+source[start:end]+test)
    subprocess.run(['cc','-fsanitize=address,undefined','-g',str(path),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
