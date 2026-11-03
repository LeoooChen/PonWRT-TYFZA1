#!/usr/bin/env python3
"""Execute production async scan lifecycle with real fork, uloop and blob buffers."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
tree = root / 'sources/openwrt'
host = tree / 'staging_dir/host'
with tempfile.TemporaryDirectory(prefix='rpcd-async-scan-') as directory:
    work = Path(directory)
    archive = next((tree / 'dl').glob('rpcd-*.tar.zst'))
    subprocess.run(['tar', '-xf', str(archive), '--strip-components=1', '-C', str(work)], check=True)
    for name in ['132-rpcd-scan-pause.patch', '133-rpcd-async-scan.patch']:
        subprocess.run(['patch', '-p1', '--fuzz=0', '-i', str(root / 'port/host-patches' / name)],
                       cwd=work, check=True, stdout=subprocess.DEVNULL)
    text = (work / 'iwinfo.c').read_text()
    start = text.index('struct rpc_scan_context {')
    body = text[start:text.index('\nstatic void\nrpc_iwinfo_add_rateinfo', start)]
    model = r'''
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <libubox/uloop.h>
#include <libubox/blobmsg.h>
enum { UBUS_STATUS_OK, UBUS_STATUS_INVALID_ARGUMENT, UBUS_STATUS_UNKNOWN_ERROR,
 UBUS_STATUS_TIMEOUT, UBUS_STATUS_NO_DATA, UBUS_STATUS_NO_MEMORY, UBUS_STATUS_SYSTEM_ERROR };
struct ubus_context { struct { int fd; } sock; };
struct ubus_request_data { int deferred; };
struct ubus_object { int dummy; };
static struct blob_buf buf;
static int scenario, replies, completed, result, ticks;
static void ubus_defer_request(struct ubus_context *c, struct ubus_request_data *r,
 struct ubus_request_data *d) { (void)c; r->deferred=1; *d=*r; }
static void ubus_complete_deferred_request(struct ubus_context *c,
 struct ubus_request_data *r, int rv) {
 (void)c; assert(r->deferred); completed++; result=rv; uloop_end();
}
static void ubus_send_reply(struct ubus_context *c, struct ubus_request_data *r,
 struct blob_attr *b) { (void)c; assert(r->deferred && blob_pad_len(b)>100000); replies++; }
static int rpc_iwinfo_open(struct blob_attr *m) { (void)m; return scenario==4 ? UBUS_STATUS_INVALID_ARGUMENT:0; }
static void rpc_iwinfo_close(void) {}
static int rpc_iwinfo_scan_run(const char *method, struct blob_attr *m) {
 (void)method; (void)m;
 if(scenario==3) { for(;;) pause(); }
 usleep(120000);
 if(scenario==1) return UBUS_STATUS_NO_DATA;
 if(scenario==2) raise(SIGKILL);
 blob_buf_init(&buf,0);
 char *out=blobmsg_alloc_string_buffer(&buf,"data",200001); assert(out);
 memset(out,'x',200000);out[200000]=0;blobmsg_add_string_buffer(&buf);
 return 0;
}
static int test_timeout(struct uloop_timeout *t, int ms) {
 return uloop_timeout_set(t, ms==30000 ? 250:ms);
}
#define uloop_timeout_set test_timeout
'''
    checks = r'''
static struct uloop_timeout tick;
static void tick_cb(struct uloop_timeout *t) { ticks++; uloop_timeout_set(t,10); }
int main(void) {
 assert(!uloop_init()); tick.cb=tick_cb;
 for(scenario=0;scenario<=4;scenario++) {
  struct ubus_context ctx={.sock.fd=-1}; struct ubus_request_data req={0};
  replies=completed=ticks=0; result=-1;
  int rv=rpc_iwinfo_scan(&ctx,NULL,&req,"scan",NULL);
  if(scenario==4) {assert(rv==UBUS_STATUS_INVALID_ARGUMENT && !req.deferred);continue;}
  assert(rv==0 && req.deferred && rpc_scan_active);
  assert(rpc_iwinfo_scan(&ctx,NULL,&req,"scan_pause",NULL)==UBUS_STATUS_UNKNOWN_ERROR);
  pid_t pid=rpc_scan_active->process.pid;
  uloop_timeout_set(&tick,10);uloop_run();uloop_timeout_cancel(&tick);
  assert(completed==1 && !rpc_scan_active && ticks>=5);
  assert(result==(scenario==0 ? 0:scenario==1 ? UBUS_STATUS_NO_DATA:
                 scenario==2 ? UBUS_STATUS_UNKNOWN_ERROR:UBUS_STATUS_TIMEOUT));
  assert(replies==(scenario==0));
  assert(waitpid(pid,NULL,WNOHANG)==-1 && errno==ECHILD);
 }
 uloop_done();puts("PASS: async scan keeps event loop responsive; large reply, concurrent scan, invalid device, child failure/death, timeout and reaping");
}
'''
    (work / 'test.c').write_text(model + body + checks)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-fsanitize=address,undefined', '-I' + str(host / 'include'),
                    str(work / 'test.c'), '-L' + str(host / 'lib'), '-lubox',
                    '-Wl,-rpath,' + str(host / 'lib'), '-o', str(work / 'test')], check=True)
    subprocess.run([str(work / 'test')], check=True, timeout=10)
