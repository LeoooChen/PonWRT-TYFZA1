#!/usr/bin/env python3
"""Check actual RX accounting and DFS traffic selection across PE handoffs."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


rx = (driver / 'rx.c').read_text()
dfs = (driver / 'dfs.h').read_text()
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;typedef int8_t s8;typedef uint16_t u16;
typedef uint32_t u32;typedef uint64_t u64;
#define READ_ONCE(x) (x)
struct rtl_ax_rx {struct {u64 data_bytes;} queues[2];
 struct {void *memory;} pe;void *rpq_memory;};
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
'''
checks = r'''
int main(void){
 u32 memory[0x2200/4]={0};
 struct rtl_ax_rx rx={.pe.memory=memory,.rpq_memory=memory};
 assert(rtl_ax_rx_traffic_bytes(&rx)==0);
 rx.queues[0].data_bytes=1000;
 assert(rtl_ax_rx_traffic_bytes(&rx)==1000);
 memory[0x2184/4]=250000000;
 assert(rtl_ax_rx_traffic_bytes(&rx)==250001000);
 /* The slot retains its counters when PE detaches and reattaches. */
 rx.rpq_memory=0;
 assert(rtl_ax_rx_traffic_bytes(&rx)==250001000);
 rx.queues[0].data_bytes+=1000;rx.rpq_memory=memory;
 assert(rtl_ax_rx_traffic_bytes(&rx)==250002000);
 /* Both byte counters wrap naturally in the watchdog's u32 delta. */
 rx.queues[0].data_bytes=UINT32_MAX-100;memory[0x2184/4]=UINT32_MAX-200;
 u32 before=rtl_ax_rx_traffic_bytes(&rx);
 rx.queues[0].data_bytes+=150;memory[0x2184/4]+=250000000;
 u32 delta=rtl_ax_rx_traffic_bytes(&rx)-before;
 assert(delta==250000150);
 struct rtl_ax_dfs state={.dynamic_setting=true,.throughput_threshold=2};
 rtl_ax_dfs_dynamic(&state,(u64)delta*8/2000000,100,0,0);
 assert(state.pw_factor==3 && state.ppb_parameter==4);
 before=rtl_ax_rx_traffic_bytes(&rx);
 delta=rtl_ax_rx_traffic_bytes(&rx)-before;
 rtl_ax_dfs_dynamic(&state,delta,100,0,0);
 assert(state.pw_factor==1 && state.ppb_parameter==5);
}
'''
with tempfile.TemporaryDirectory(prefix='rx-traffic-') as directory:
    p = Path(directory)
    structs = dfs[dfs.index('struct rtl_ax_dfs_profile'):dfs.index('static inline void rtl_ax_dfs_region')]
    source = fixture + structs + function(rx, 'u32 rtl_ax_rx_traffic_bytes(')
    source += function(dfs, 'static inline void rtl_ax_dfs_dynamic(') + checks
    (p / 'test.c').write_text(source)
    subprocess.run(['cc', '-O2', '-fsanitize=undefined', str(p / 'test.c'),
                    '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True)
print('RX traffic: ARM/PE sum, detach/reattach, counter wrap, DFS busy/idle PASS')
