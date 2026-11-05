#!/usr/bin/env python3
"""Execute the native RX BA callbacks with explicit firmware/PE boundaries."""
from pathlib import Path
import hashlib
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = Path('port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap')


def read(name):
    path = driver / name
    if '--before' in sys.argv:
        return subprocess.check_output(['git', 'show', f'cc338cb:{path}'], cwd=root, text=True)
    return (root / path).read_text()


def function(text, name):
    start = text.index('static int ' + name + '(')
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


source, pci = read('monitor.h'), read('pci.c')
remove = function(source, 'rtl_ax_rx_ba_remove')
action = function(source, 'rtl_ax_ampdu_action')
cases = action[action.index('\tcase IEEE80211_AMPDU_RX_START:'):action.rindex('\n\t}')]
record = re.search(r'struct \{[^\n]+\} rx_ba\[[^;]+;', pci)[0]
# The baseline ignores cam; the fixture uses it only after a successful ninth
# session, which the baseline cannot establish. Keep the actual array bound.
if 'cam;' not in record:
    record = record.replace('macid, tid;', 'macid, tid, cam;')
common = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define RTL_AX_H2C_CMD 0
#define IEEE80211_AMPDU_RX_START 1
#define IEEE80211_AMPDU_RX_STOP 2
#define HZ 100
#define dev_info(...) ((void)0)
#define wiphy_delayed_work_queue(...) (retries++)
struct rtl_ax_ap {
RECORD
 unsigned rx_ba_started, rx_ba_stopped; int rx_ba_result;
 void *tx, *rx;
};
struct ieee80211_ampdu_params { unsigned tid, ssn, buf_size; };
struct rtl_ax_station { unsigned macid; };
static u16 windows[128][16];
static int fail_peer, fail_command;
static unsigned commands, retires, retries, offloads;
static u32 words[2];
static int rtl_ax_tx_rx_retire(void *tx, unsigned id) {
 retires++; return (int)id == fail_peer ? -EBUSY : 0;
}
static void rtl_ax_rx_ba_window(void *rx,unsigned id,unsigned tid,unsigned n) { windows[id][tid]=n; }
static void rtl_ax_store32(void *p,u32 n) { memcpy(p,&n,4); }
static int rtl_ax_command(struct rtl_ax_ap *r,int a,int b,int c,int d,void *p,unsigned n,int ack) {
 assert(n==8);memcpy(words,p,8);commands++;return fail_command ? -EIO : 0;
}
static void rtl_ax_offload_ready(struct rtl_ax_ap *r) { offloads++; }
'''.replace('RECORD', record)
wrapper = r'''
static int act(struct rtl_ax_ap *radio,unsigned macid,unsigned tid,int op) {
 struct ieee80211_ampdu_params p={.tid=tid,.ssn=123,.buf_size=64},*params=&p;
 struct rtl_ax_station s={.macid=macid},*station=&s;
 unsigned i,j,cam,used;int ret;u8 ba[8];
 switch(op) {
CASES
 default:return -EINVAL;
 }
}
static struct rtl_ax_ap r;
static unsigned find(unsigned id,unsigned tid) {
 for(unsigned i=0;i<ARRAY_SIZE(r.rx_ba);i++)
  if(r.rx_ba[i].valid&&r.rx_ba[i].macid==id&&r.rx_ba[i].tid==tid)return i;
 assert(0);return 0;
}
static void reset(void) {
 memset(&r,0,sizeof(r));memset(windows,0,sizeof(windows));
 fail_peer=-1;fail_command=commands=retires=retries=offloads=0;
}
static void full(void) {
 reset();for(unsigned tid=1;tid<8;tid++)assert(!act(&r,1,tid,1));
 assert(!act(&r,2,6,1));assert(commands==8);
}
static void unique(void) {
 unsigned used=0;
 for(unsigned i=0;i<ARRAY_SIZE(r.rx_ba);i++)if(r.rx_ba[i].valid&&r.rx_ba[i].cam<8) {
  assert(!(used&BIT(r.rx_ba[i].cam)));used|=BIT(r.rx_ba[i].cam);
 }
}
int main(void) {
 unsigned old,now,n,cam;
 /* Actual full table: phone has seven QoS TIDs, Mac has TID6. */
 full();old=find(1,1);cam=r.rx_ba[old].cam;
 assert(!act(&r,2,0,1));now=find(2,0);assert(windows[2][0]==64);
 assert(r.rx_ba[old].valid&&r.rx_ba[old].cam==8&&windows[1][1]==64);
 assert(r.rx_ba[now].cam==cam&&commands==9);unique();
 n=commands;assert(!act(&r,1,1,2));assert(commands==n&&windows[1][1]==0);
 assert(r.rx_ba[now].valid&&windows[2][0]==64);
 assert(!act(&r,2,0,2));assert(commands==n+1&&words[1]==(cam+8U)<<28);
 /* Static capacity never rejects an additional nonzero software session. */
 full();n=commands;assert(!act(&r,3,3,1));now=find(3,3);
 assert(r.rx_ba[now].cam==8&&commands==n&&windows[3][3]==64);unique();
 fail_peer=3;assert(act(&r,3,3,2)==-EBUSY);
 assert(r.rx_ba[now].deleting&&windows[3][3]==64&&commands==n);
 fail_peer=-1;assert(!rtl_ax_rx_ba_remove(&r,now));
 assert(!r.rx_ba[now].valid&&windows[3][3]==0&&commands==n);
 /* Do not steal another TID0, or mutate a peer whose PE retirement fails. */
 reset();for(unsigned id=1;id<=8;id++)assert(!act(&r,id,0,1));
 n=commands;assert(act(&r,9,0,1)==-ENOSPC&&commands==n);unique();
 full();old=find(1,1);cam=r.rx_ba[old].cam;fail_peer=1;n=commands;
 assert(act(&r,2,0,1)==-EBUSY&&commands==n);
 assert(r.rx_ba[old].cam==cam&&windows[1][1]==64);unique();
 /* Ambiguous firmware failure retains the CAM until acknowledged deletion;
  * the displaced session keeps its software reorder window. */
 full();old=find(1,1);fail_command=1;n=commands;
 assert(act(&r,2,0,1)==-EIO&&commands==n+1&&retries==1);
 now=find(2,0);assert(r.rx_ba[now].deleting&&windows[2][0]==0);
 assert(r.rx_ba[old].cam==8&&windows[1][1]==64);unique();
 assert(rtl_ax_rx_ba_remove(&r,now)==-EIO&&r.rx_ba[now].valid);
 fail_command=0;assert(!rtl_ax_rx_ba_remove(&r,now));
 assert(!r.rx_ba[now].valid&&windows[1][1]==64);unique();
 /* Exercise many simultaneous software sessions, then release every TID. */
 reset();for(unsigned id=1;id<32;id++)for(unsigned tid=1;tid<8;tid++)assert(!act(&r,id,tid,1));
 for(unsigned id=1;id<=8;id++)assert(!act(&r,id,0,1));
 unique();
 for(unsigned id=1;id<32;id++)for(unsigned tid=0;tid<8;tid++)assert(!act(&r,id,tid,2));
 for(unsigned i=0;i<ARRAY_SIZE(r.rx_ba);i++)assert(!r.rx_ba[i].valid);
 for(unsigned id=1;id<32;id++)for(unsigned tid=0;tid<8;tid++)assert(!windows[id][tid]);
 puts("RX BA: table exhaustion, TID0 priority, dynamic stop, retirement/firmware failures and 225-session cleanup passed");
}
'''.replace('CASES', cases)
with tempfile.TemporaryDirectory(prefix='rx-ba-sessions-') as directory:
    path = Path(directory)
    (path / 'case.c').write_text(common + remove + wrapper)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-unused-variable',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    str(path / 'case.c'), '-o', str(path / 'case')], check=True)
    subprocess.run([str(path / 'case')], check=True)
print('Native monitor.h SHA256:', hashlib.sha256(source.encode()).hexdigest())
print('Firmware commands, PE retirement and OS scheduling are explicit test boundaries; no RF claim.')
