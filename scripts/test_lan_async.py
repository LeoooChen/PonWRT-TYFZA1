#!/usr/bin/env python3
"""Exercise production LAN registration with real threads and modeled RTNL."""
from pathlib import Path
import subprocess
import tempfile
from test_lan_probe_isolation import shim, function

# Reuse fault injection, but replace inline work/completions and RTNL with pthreads.
start = shim.index('static struct work_struct *pending_work;')
end = shim.index('struct device_node {', start)
threading = r'''
static pthread_mutex_t rtnl = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t gate = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
static _Thread_local int rtnl_owned;
static pthread_t worker, opener, remover;
static int open_attempt, open_waiting, opened, worker_created, worker_finished;
static int remove_started, remove_passed, remove_mode;
static void rtnl_lock(void){pthread_mutex_lock(&rtnl);rtnl_owned=1;}
static void rtnl_unlock(void){assert(rtnl_owned);rtnl_owned=0;pthread_mutex_unlock(&rtnl);}
#define init_completion(c) ((c)->done=0)
static void complete_all(struct completion *c){
 pthread_mutex_lock(&gate);c->done=1;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
}
static void wait_for_completion(struct completion *c){
 assert(rtnl_owned);
 pthread_mutex_lock(&gate);open_waiting=1;pthread_cond_broadcast(&changed);
 while(!c->done)pthread_cond_wait(&changed,&gate);
 pthread_mutex_unlock(&gate);
}
static void *run_work(void *arg){
 struct work_struct *work=arg;
 pthread_mutex_lock(&gate);
 while(!(remove_mode?remove_started:open_waiting))pthread_cond_wait(&changed,&gate);
 pthread_mutex_unlock(&gate);
 work->func(work);
 pthread_mutex_lock(&gate);worker_finished=1;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
 return NULL;
}
static bool schedule_work(struct work_struct *work){
 worker_created=1;assert(!pthread_create(&worker,NULL,run_work,work));return true;
}
static void flush_work(struct work_struct *work){(void)work;assert(!pthread_join(worker,NULL));}
'''
code = shim[:start] + threading + shim[end:]
code = code.replace('#include <assert.h>', '#include <assert.h>\n#include <pthread.h>')
code = code.replace('unsigned int registered_ports,port;', 'unsigned int registered_ports,port,engine_users;')
start = code.index('static int register_netdevice(')
end = code.index('static void unregister_netdev(', start)
code = code[:start] + 'static int register_netdevice(struct net_device *d);\n' + code[end:]
code += r'''
static unsigned int engine_ports;
static int rtl_engine_start(struct rtl_xram *priv){
 assert(rtnl_owned);
 engine_ports=0;
 for(unsigned int i=0;i<5;i++)if(priv->ports[i])engine_ports|=BIT(i);
 /* No synchronous port may be omitted by the first scheduler/QM setup. */
 assert((engine_ports & (board_ports==5?7:15))==(board_ports==5?7:15));
 if(board_ports==5 && stage>=1 && stage<=7)assert(!(engine_ports&BIT(4)));
 return 0;
}
'''
code += function('rtl_engine_get')
code += r'''
static void *open_first(void *arg){
 pthread_mutex_lock(&gate);open_attempt=1;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
 rtnl_lock();assert(!rtl_engine_get(arg));opened=1;rtnl_unlock();return NULL;
}
static int register_netdevice(struct net_device *d){
 assert(rtnl_owned);
 if(stage==8 && d->lan.port==4)return -EIO;
 assert(d->phydev);active|=BIT(d->lan.port);
 if(d->lan.port==0){
  pthread_mutex_lock(&gate);
  assert(!pthread_create(&opener,NULL,open_first,d->lan.shared));
  while(!open_attempt)pthread_cond_wait(&changed,&gate);
  pthread_mutex_unlock(&gate);
 }
 return 0;
}
'''
code += '\n'.join(function(n) for n in ('rtl_unregister_lan', 'rtl_sgmii_power_off',
                                       'rtl_lan_number', 'rtl_register_lan', 'rtl_lan4_work', 'rtl_register_lans'))
# Execute the production removal barrier; per-port teardown is covered below and
# by test_lan_probe_isolation, without mocking unrelated DMA/IRQ subsystems.
remove = function('rtl_remove')
barrier = remove[remove.index('\tif (priv->lan4_phy)'):remove.index('\tcancel_delayed_work_sync')]
code += r'''
static void *remove_pending(void *arg){
 struct rtl_xram *priv=arg;
 pthread_mutex_lock(&gate);remove_started=1;pthread_cond_broadcast(&changed);pthread_mutex_unlock(&gate);
''' + barrier + r'''
 assert(worker_finished);remove_passed=1;return NULL;
}
int main(int argc,char **argv){
 assert(argc==4);board_ports=atoi(argv[1]);stage=atoi(argv[2]);remove_mode=atoi(argv[3]);fail_port=4;
 struct net_device primary={.dev_addr={0,0x50,0xc2,1,2,3}};
 struct rtl_xram priv={.netdev=&primary,.qm_rx=true};struct device parent={0};
 for(int i=0;i<5;i++)nodes[i].port=i;
 init_completion(&priv.lan4_ready);rtl_register_lans(&priv,&parent);
 if(remove_mode)assert(!pthread_create(&remover,NULL,remove_pending,&priv));
 assert(!pthread_join(opener,NULL));
 if(remove_mode)assert(!pthread_join(remover,NULL));
 else if(worker_created)flush_work(&priv.lan4_work);
 assert(opened && priv.engine_users==1 && priv.lan4_ready.done);
 unsigned int expected=board_ports==5?23:15;
 if(board_ports==5 && stage>=1 && stage<=8)expected&=~BIT(4);
 assert(active==expected && priv.registered_ports==expected && fe_ports==expected);
 assert(!node_refs && (!remove_mode || remove_passed));
 for(unsigned int i=0;i<5;i++)assert(priv.qm.lan[i]==priv.ports[i]);
 if(board_ports==5 && !stage)assert(engine_ports&BIT(4));
 for(int i=0;i<5;i++)rtl_unregister_lan(&priv,i);
 assert(!active && !fe_ports && !allocated);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='lan-async-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-pthread', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-sign-compare',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    cases = [(4, 0, 0)] + [(5, fault, remove) for fault in range(10) for remove in (0, 1)]
    for board, fault, remove in cases:
        subprocess.run([str(path / 'test'), str(board), str(fault), str(remove)],
                       check=True, timeout=5)
    print(f'{len(cases)} threaded early-open, LAN4 failure and pending-removal cases passed')
