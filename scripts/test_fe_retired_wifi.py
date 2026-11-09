#!/usr/bin/env python3
"""Execute real FE Hash/cache deletion on retired Wi-Fi ports and late retries."""
from pathlib import Path
import subprocess,tempfile
r=Path(__file__).resolve().parents[1];f=r/'scripts/test_fe_mcast_integration.py'
ns={'__file__':str(f)};text=f.read_text();exec(compile(text[:text.index("code += r'''\nint main(void)")],str(f),'exec'),ns)
code,fn=ns['code'],ns['function']
# Keep actual action/table IO and fe_delete; provide port/list/locking for route API.
code=code.replace('struct net_device {bool running,carrier;};','struct wireless_dev {int iftype;};struct net_device {bool running,carrier;struct wireless_dev *ieee80211_ptr;};')
code=code.replace('struct rtl_fe_port {','struct rtl_fe_port {struct list_head list;',1)
code=code.replace('struct rtl_fe_wifi_peer {','struct rtl_fe_wifi_peer {struct list_head list;struct rtl_fe_port *port;',1)
code=code.replace('struct list_head flows,retries;','int lock;struct list_head ports,wifi_peers,flows,retries;',1)
code=code.replace('static struct rtl_fe_wifi_peer *fe_wifi_peer(struct rtl_fe *f,struct rtl_fe_port *p,const u8 *a){(void)f;(void)p;(void)a;return NULL;}', 'static struct rtl_fe_wifi_peer *fe_wifi_peer(struct rtl_fe *,struct rtl_fe_port *,const u8 *);')
code += r'''
#define NL80211_IFTYPE_STATION 2
#define ether_addr_equal(a,b) (!memcmp(a,b,6))
#define list_for_each_entry(p,h,m) for(struct list_head *c=(h)->next;c!=(h) && ((p)=container_of(c,__typeof__(*(p)),m));c=c->next)
static void mutex_lock(int *p){assert(!*p);*p=1;}static void mutex_unlock(int *p){assert(*p);*p=0;}
static void fe_mcast_invalidate(struct rtl_fe *f){assert(f->lock);}
'''+''.join(fn(n) for n in ('fe_port','fe_wifi_client','fe_wifi_peer','rtl_fe_wifi_peer_route'))
code+=r'''
static struct rtl_fe_flow *flow(struct rtl_fe *fe,struct rtl_fe_port *in,struct rtl_fe_port *out,unsigned index,bool deleting){
 struct rtl_fe_flow *f=calloc(1,sizeof(*f));assert(f);f->input=in;f->output=out;f->hash_index=index;f->crc16=100+index;
 f->published=f->installed=true;f->deleting=deleting;f->destination[0]=2;
 INIT_LIST_HEAD(&f->retry);list_add_tail(&f->list,&fe->flows);__set_bit(index,fe->hashes);
 return f;
}
int main(void){unsigned cases=0;
 for(unsigned retired=0;retired<2;retired++)for(unsigned where=0;where<3;where++)for(unsigned fault=0;fault<3;fault++){
  static struct rtl_fe fe;memset(&fe,0,sizeof(fe));fe.memory=calloc(1,RTL_FE_MEMORY_BYTES);assert(fe.memory);
  INIT_LIST_HEAD(&fe.ports);INIT_LIST_HEAD(&fe.flows);INIT_LIST_HEAD(&fe.retries);INIT_LIST_HEAD(&fe.wifi_peers);
  struct net_device a={},b={},absent={};const u8 mac[6]={2};
  struct rtl_fe_port old={.netdev=&a,.wifi_id=1,.destination=16,.retired=retired},other={.netdev=&b,.wifi_id=2,.destination=16},replacement={.netdev=&a,.wifi_id=3,.destination=16};
  list_add_tail(&old.list,&fe.ports);list_add_tail(&other.list,&fe.ports);
  struct rtl_fe_flow *pending=flow(&fe,where==1?&old:&other,where==0?&old:&other,0,true);
  if(where==2){pending->replica_count=1;pending->replica_ports=malloc(sizeof(*pending->replica_ports));pending->replica_ports[0]=&old;}
  struct rtl_fe_flow *foreign=flow(&fe,&other,&other,1,false);
  /* Retired generations may coexist with a new active FE port. */
  if(retired)list_add_tail(&replacement.list,&fe.ports);
  fe.age_fail=fault==1;fe.cache_fail=fault==2;
  int ret=rtl_fe_wifi_peer_route(&fe,&a,mac,0);
  assert(ret==(fault?-ETIMEDOUT:0));
  if(fault){assert(pending->published&&pending->deleting&&test_bit(0,fe.hashes)&&!fe.deletes&&!list_empty(&fe.retries));
   assert(!rtl_fe_wifi_peer_route(&fe,&a,mac,0));}
  assert(!test_bit(0,fe.hashes)&&fe.deletes==1&&list_empty(&fe.retries));
  assert(foreign->published&&test_bit(1,fe.hashes));
  for(unsigned i=0;i<fe.event_count;i++)assert(fe.events[i]!=1); /* never re-enable a deleting rule */
  unsigned before=fe.event_count;assert(!rtl_fe_wifi_peer_route(&fe,&absent,mac,0)&&fe.event_count==before);
  assert(rtl_fe_wifi_peer_route(&fe,&absent,mac,19)==-ENODEV);
  if(retired){list_del(&replacement.list);assert(rtl_fe_wifi_peer_route(&fe,&a,mac,19)==-ENODEV);assert(!rtl_fe_wifi_peer_route(&fe,&a,mac,0));}
  assert(!fe_delete(&fe,foreign));assert(list_empty(&fe.flows));free(fe.memory);cases++;
 }
 printf("Actual FE deletion: %u active/retired/input/output/replica Hash/cache faults, replacement generation, no re-publication and absent-port retirement PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='fe-retired-wifi-') as t:
 p=Path(t);(p/'test.c').write_text(code)
 subprocess.run(['cc','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-parameter','-Wno-misleading-indentation','-fsanitize=address,undefined','-I'+str(ns['drivers']),'-I'+str(r/'port/overlay/target/linux/rtl9617c/files-6.18/include'),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
