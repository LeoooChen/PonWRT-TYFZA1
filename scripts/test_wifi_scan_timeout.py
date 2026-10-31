#!/usr/bin/env python3
"""Execute iwinfo's patched event wait with bounded, interrupted and abort cases."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='scan-timeout-') as tmp:
    work = Path(tmp)
    archive = next((root / 'sources/openwrt/dl').glob('libiwinfo-*.tar.zst'))
    subprocess.run(['tar', '-xf', str(archive), '--strip-components=1', '-C', str(work)], check=True)
    for patch in sorted((root / 'port/host-patches').glob('*iwinfo*.patch')):
        subprocess.run(['patch', '-p1', '--fuzz=0', '-i', str(patch)], cwd=work,
                       check=True, stdout=subprocess.DEVNULL)
    text = (work / 'iwinfo_nl80211.c').read_text()
    start = text.index('static int __nl80211_wait(')
    body = text[start:text.index('\n#define nl80211_wait', start)]
    code = r'''
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <poll.h>
#include <time.h>
#include <linux/nl80211.h>
struct nl80211_event_conveyor {unsigned wait[16];int recv;};
struct nl_cb {int dummy;};
struct {void *nl_sock;} state,*nls=&state;
static struct nl_cb callback;
static struct nl80211_event_conveyor *event;
static int scenario,polls,receives,frees;
static long long elapsed;
#define NL_CB_DEFAULT 0
#define NL_CB_SEQ_CHECK 1
#define NL_CB_CUSTOM 2
#define NL_CB_VALID 3
#define nl80211_msg_error NULL
#define nl80211_wait_seq_check NULL
#define nl80211_wait_cb NULL
static int nl_socket_get_fd(void *s){(void)s;return 42;}
static int nl80211_subscribe(const char *f,const char *g){(void)f;(void)g;return 0;}
static struct nl_cb *nl_cb_alloc(int x){(void)x;return &callback;}
static void nl_cb_err(struct nl_cb *c,int a,void *f,void *v){(void)c;(void)a;(void)f;(void)v;}
static void nl_cb_set(struct nl_cb *c,int a,int b,void *f,void *v){
 (void)c;(void)b;(void)f;if(a==NL_CB_VALID)event=v;
}
static void nl_cb_put(struct nl_cb *c){assert(c==&callback);frees++;}
static void nl_recvmsgs(void *s,struct nl_cb *c){
 (void)s;(void)c;receives++;
 if(scenario==0)event->recv=NL80211_CMD_NEW_SCAN_RESULTS;
 if(scenario==1)event->recv=NL80211_CMD_SCAN_ABORTED;
}
static int fake_clock(int id,struct timespec *t){
 assert(id==CLOCK_MONOTONIC);t->tv_sec=elapsed/1000;t->tv_nsec=(elapsed%1000)*1000000;return 0;
}
static int fake_poll(struct pollfd *p,unsigned n,int timeout){
 assert(n==1&&p->fd==42&&timeout>0&&timeout<=10000);polls++;elapsed+=1000;
 if(scenario==2)return 0;
 if(scenario==3){errno=EINTR;return -1;}
 p->revents=scenario==4?POLLHUP:POLLIN;return 1;
}
#define clock_gettime fake_clock
#define poll fake_poll
'''
    checks = r'''
int main(void){
 const int expected[]={0,-ECANCELED,-ETIMEDOUT,-ETIMEDOUT,-EIO,-ETIMEDOUT};
 for(scenario=0;scenario<6;scenario++){
  elapsed=polls=receives=frees=0;
  assert(__nl80211_wait("nl80211","scan",NL80211_CMD_NEW_SCAN_RESULTS,NL80211_CMD_SCAN_ABORTED,0)==expected[scenario]);
  assert(frees==1&&polls<=10);
 }
 puts("iwinfo wait: completion, abort, timeout, EINTR, socket failure and unrelated events passed");
}
'''
    (work / 'test.c').write_text(code + body + checks)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True)
