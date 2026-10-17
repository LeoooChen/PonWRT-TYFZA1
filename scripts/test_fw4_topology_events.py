#!/usr/bin/env python3
"""Replay late bridge topology changes through netifd and firewall4 hotplug."""
from pathlib import Path
import re, subprocess, json, os, tempfile, shutil
root=Path(__file__).resolve().parents[1]
temporary=tempfile.TemporaryDirectory(prefix='fw4-topology-',dir=root/'artifacts')
out=Path(temporary.name)
for package in ('netifd','firewall4'):
    makefile=(root/'sources/openwrt/package/network/config'/package/'Makefile').read_text()
    date=re.search(r'^PKG_SOURCE_DATE:=(.+)$',makefile,re.M)[1].replace('-','.')
    revision=re.search(r'^PKG_SOURCE_VERSION:=(.+)$',makefile,re.M)[1][:8]
    name=f'{package}-{date}~{revision}'
    subprocess.run(['tar','--zstd','-xf',str(root/'sources/openwrt/dl'/(name+'.tar.zst')),'-C',str(out)],check=True)
    for side in ('before','after'):
        (out/side).mkdir(exist_ok=True)
        shutil.copytree(out/name,out/side/package)
    patch='120-netifd-interface-topology.patch' if package=='netifd' else '121-firewall4-interface-topology.patch'
    subprocess.run(['patch','-p1','--fuzz=0','-i',str(root/'port/host-patches'/patch)],cwd=out/'after'/package,stdout=subprocess.DEVNULL,check=True)
original=out/'before/netifd'
def function(text,name):
 m=re.search(r'(?:static void\s+|void\s+)'+name+r'\([^;]*?\)\s*\{',text);assert m,name
 start=m.start();pos=m.end();depth=1
 while depth:
  if text[pos]=='{':depth+=1
  if text[pos]=='}':depth-=1
  pos+=1
 return text[start:pos]
def enum(text,name):return re.search(r'enum '+name+r' \{.*?\};',text,re.S)[0]
records=[]
for side in ('before','after'):
 src=out/side/'netifd';work=out/side/'event-check';work.mkdir(exist_ok=True)
 h=(src/'interface.h').read_text();c=(src/'interface.c').read_text();e=(src/'interface-event.c').read_text()
 code='''#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <assert.h>
#include <libubox/list.h>
#define D(...) do {} while (0)
'''+ '\n'.join(enum(h,n) for n in ('interface_event','interface_state','interface_update_flags'))+enum((original/'device.h').read_text(),'device_event')+'''
struct device { bool external, sys_present; const char *ifname; };
struct device_user { struct device *dev; };
struct interface { struct list_head hotplug_list; enum interface_event hotplug_ev; enum interface_update_flags hotplug_updated,updated; const char *name; struct device_user main_dev,l3_dev; bool renew; enum interface_state state; void *proto; };
struct uloop_process { bool pending; pid_t pid; };
static struct uloop_process task;
static struct interface *current;
static enum interface_event current_ev;
static LIST_HEAD(pending);
static char *hotplug_cmd_path;
static const char *eventnames[]={ [IFEV_UP]="ifup",[IFEV_UPDATE]="ifupdate",[IFEV_DOWN]="ifdown" };
static unsigned int renews;
#define PROTO_CMD_RENEW 1
static void interface_queue_event(struct interface *, enum interface_event);
static void task_complete(struct uloop_process *,int);
static void interface_event(struct interface *i,enum interface_event e) { interface_queue_event(i,e); }
static void interface_proto_event(void *p,int c,bool b) { (void)p;(void)c;(void)b;renews++; }
static void interface_set_available(struct interface *i,bool b){(void)i;(void)b;}
static void interface_set_enabled(struct interface *i,bool b){(void)i;(void)b;}
static void interface_set_main_dev(struct interface *i,void *d){(void)i;(void)d;}
static void interface_set_link_state(struct interface *i,bool b){(void)i;(void)b;}
static bool device_link_active(void *d){(void)d;return true;}
static void netifd_ubus_interface_event(struct interface *i,bool b){(void)i;(void)b;}
static void netifd_ubus_interface_notify(struct interface *i,bool b){(void)i;(void)b;}
static void uloop_process_add(struct uloop_process *p){p->pending=true;}
'''
 for name in ('run_cmd','call_hotplug','task_complete','interface_queue_event'):code+=function(e,name)+'\n'
 code+=function(c,'interface_main_dev_cb')+r'''
static void drain(void) {
 int status;
 task.pending=false;call_hotplug();
 while(current) { assert(waitpid(task.pid,&status,0)==task.pid);assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);task.pending=false;task_complete(NULL,0); }
}
int main(int argc,char **argv) {
 assert(argc==2);hotplug_cmd_path=argv[1];
 struct device bridge={.ifname="br-lan"};
 struct interface i={.name="lan",.state=IFS_UP,.main_dev={&bridge},.l3_dev={&bridge}};
 INIT_LIST_HEAD(&i.hotplug_list);
 /* Late add and remove occur behind an already running hotplug task. */
 for(int n=0;n<2;n++) {
  task.pending=true;
  interface_main_dev_cb(&i.main_dev,DEV_EVENT_TOPO_CHANGE);
  /* Another update must not erase a queued topology reason. */
  i.updated=0;interface_event(&i,IFEV_UPDATE);
  drain();
 }
 /* Setup has no standalone topology reload; its pending ifup covers it. */
 i.state=IFS_SETUP;task.pending=true;
 interface_main_dev_cb(&i.main_dev,DEV_EVENT_TOPO_CHANGE);
 assert(list_empty(&pending));
 i.state=IFS_UP;i.updated=IUF_ADDRESS;interface_event(&i,IFEV_UP);
 interface_main_dev_cb(&i.main_dev,DEV_EVENT_TOPO_CHANGE);
 assert(i.hotplug_ev==IFEV_UP);drain();
 /* Preserve DHCP's existing renewal, while notifying the firewall. */
 i.renew=true;task.pending=true;interface_main_dev_cb(&i.main_dev,DEV_EVENT_TOPO_CHANGE);
 assert(renews==1);drain();
 return 0;
}
'''
 (work/'events.c').write_text(code)
 hook=(out/side/'firewall4/root/etc/hotplug.d/iface/20-firewall').read_text().replace('/etc/init.d/firewall',str(work/'firewall'))
 (work/'hook').write_text(hook)
 (work/'firewall').write_text('#!/bin/sh\n[ "$1" = enabled ]\n')
 (work/'fw4').write_text('#!/bin/sh\ncase "$2" in network) [ "$3" = lan ];; reload) echo reload >> "$RELOAD_LOG";; *) exit 1;; esac\n')
 (work/'logger').write_text('#!/bin/sh\nexit 0\n')
 (work/'hotplug').write_text('#!/bin/sh\nprintf "%s|%s|%s\\n" "$ACTION" "$IFUPDATE_TOPOLOGY" "$IFUPDATE_ADDRESSES" >> "$EVENT_LOG"\nexec sh "'+str(work/'hook')+'"\n')
 for f in ('firewall','fw4','logger','hotplug'):(work/f).chmod(0o755)
 for f in ('reloads','events'):(work/f).write_text('')
 subprocess.run(['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-I'+str(root/'sources/openwrt/staging_dir/host/include'),str(work/'events.c'),'-o',str(work/'check')],check=True)
 env=dict(os.environ,PATH=str(work)+':'+os.environ['PATH'],RELOAD_LOG=str(work/'reloads'),EVENT_LOG=str(work/'events'))
 subprocess.run([str(work/'check'),str(work/'hotplug')],env=env,check=True)
 events=(work/'events').read_text().splitlines();reloads=len((work/'reloads').read_text().splitlines())
 assert reloads==(1 if side=='before' else 4),(side,events,reloads)
 records.append(dict(side=side,hotplug_events=events,firewall_reloads=reloads))
print(json.dumps(dict(scope='Actual netifd callback/queue/dispatch and firewall hotplug. Real fork/exec, modeled protocol/ubus/fw4 execution boundaries. No network namespace or live firewall changes.',records=records),indent=2))

temporary.cleanup()
