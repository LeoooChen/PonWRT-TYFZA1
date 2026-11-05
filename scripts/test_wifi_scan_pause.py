#!/usr/bin/env python3
"""Run the patched RPC recovery transaction with real local IPC and faults."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='wifi-scan-pause-') as tmp:
    work = Path(tmp)
    archive = next((root / 'sources/openwrt/dl').glob('rpcd-*.tar.zst'))
    subprocess.run(['tar', '-xf', str(archive), '--strip-components=1', '-C', str(work)], check=True)
    subprocess.run(['patch', '-p1', '--fuzz=0', '-i', str(root / 'port/host-patches/132-rpcd-scan-pause.patch')], cwd=work, check=True, stdout=subprocess.DEVNULL)
    text = (work / 'iwinfo.c').read_text()
    start = text.index('static int\nrpc_scan_paused(')
    body = text[start:text.index('\nstatic int', start + 1)]
    model = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/mman.h>
struct state {int scenario, disabled, enables, scans;} *state;
static int rpc_scan_hostapd_open(char *directory) {(void)directory;return dup(STDIN_FILENO);}
static int rpc_scan_hostapd(int fd, const char *command) {
 (void)fd;
 if (!strcmp(command,"STATUS")) return state->scenario==4 ? -1:0;
 if (!strcmp(command,"DISABLE")) {state->disabled=1;return state->scenario==2 ? -1:0;}
 assert(!strcmp(command,"ENABLE"));state->enables++;
 if (state->scenario==5 && state->enables==1) return -1;
 if (state->scenario==6) return -1;
 state->disabled=0;return 0;
}
static int scan(const char *name,char *results,int *length) {
 (void)name;(void)results;state->scans++;assert(state->disabled);*length=1;
 if (state->scenario==3) _exit(0); /* rpcd dies after stopping hostapd */
 return state->scenario==1 ? -1:0;
}
static const char *ifname="phy0-ap0";
static struct {int (*scanlist)(const char*,char*,int*);} ops={scan}, *iw=&ops;
/* Test uses no filesystem control socket. */
static int no_unlink(const char *path){(void)path;return 0;}
#define unlink no_unlink
#define rmdir no_unlink
'''
    checks = r'''
int main(void) {
 state=mmap(NULL,sizeof(*state),PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0);
 assert(state!=MAP_FAILED);
 for(int scenario=0;scenario<=6;scenario++) {
  *state=(struct state){.scenario=scenario};
  pid_t worker=fork();assert(worker>=0);
  if(!worker) {
   bool failed=false;int length=0;char result[8];
   int ret=rpc_scan_paused(result,&length,&failed);
   assert(failed==(scenario==6));
   assert(ret==((scenario==0||scenario>=5) ? 0:-1));
   _exit(0);
  }
  int status;assert(waitpid(worker,&status,0)==worker && status==0);
  for(int n=0;scenario==3 && state->disabled && n<200;n++) usleep(10000);
  assert(state->disabled==(scenario==6));
  assert(state->enables==(scenario>=5 ? 3:scenario==4 ? 0:1));
  assert(state->scans==((scenario==2||scenario==4) ? 0:1));
 }
 puts("RPC scan: success, scan failure, lost DISABLE reply, process death and already-disabled AP and restore failures passed");
}
'''
    (work / 'test.c').write_text(model + body + checks)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True, timeout=20)
