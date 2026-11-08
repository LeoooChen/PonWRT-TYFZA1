#!/usr/bin/env python3
"""Lost RPQ must retain key/STA ownership without sleeping on every retry."""
import argparse
import ast
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision', help='Run the same contract on an older source revision')
args = parser.parse_args()

def read(name):
    p = driver / name
    return (subprocess.check_output(['git', 'show', args.revision + ':' + str(p.relative_to(root))], cwd=root, text=True)
            if args.revision else p.read_text())

def function(source, name):
    start = re.search(r'^(?:static )?(?:int|bool|void) ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

# Reuse the existing CAM/DMAC fault model; execute real pending predicates too.
fixture = ast.parse((root / 'scripts/test_radio_key_cleanup.py').read_text())
common = next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'common' for t in n.targets))
for name in ('rtl_ax_tx_flush_key', 'rtl_ax_tx_key_pending', 'rtl_ax_drop_station'):
    common = common.replace(function(common, name), '')
common = common.replace(' int tx,rx,cleanup_work;', ' struct rtl_ax_tx *tx;int rx,cleanup_work;')
common = common.replace('int tx,unsigned', 'struct rtl_ax_tx *tx,unsigned')
common = common.replace('struct rtl_ax_ap {', 'struct rtl_ax_tx;\nstruct rtl_ax_ap {')
common = common.replace('struct rtl_ax_security_role security_roles[128];',
    'struct rtl_ax_security_role security_roles[128]; unsigned chip; void *ap_vif;'
    'struct {unsigned stopping,flushing,aggregation[16];u8 address[6];}tx_ba[128];')
preamble = r'''
#define RTL_AX_TX_ENTRIES 256
#define RTL_AX_8852C 0x52
#define max_t(type,a,b) ((type)(a)>(type)(b)?(type)(a):(type)(b))
static unsigned ba_done;
static void ieee80211_stop_tx_ba_cb_irqsafe(void *vif,u8 *address,unsigned tid){(void)vif;(void)address;(void)tid;ba_done++;}
static void rtl_ax_station_cmac_connected(u8 *p,unsigned id,bool five,unsigned aggregate){(void)id;(void)five;(void)aggregate;memset(p,0,68);}
static int rtl_ax_tx_ni_retire_ba(struct rtl_ax_tx *tx,unsigned id){(void)tx;(void)id;return 0;}
#define PE_PROBE_MAILBOX 0x2000
#define spin_lock_irqsave(p,f) ((void)(p),(f)=0)
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
#define dma_wmb() ((void)0)
#define msecs_to_jiffies(n) (n)
static unsigned waits,wait_ms,drops;
#define wait_event_timeout(q,predicate,ms) ({ (void)(q);waits++;wait_ms+=(ms);(predicate)?1:0; })
struct rtl_ax_tx {bool pe_active;int lock,key_flush_wait;struct {void *memory;}pe;
 bool pending[256];struct {unsigned macid,tid,key_index;bool encrypted;}slots[256];};
static u32 get_unaligned_le32(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static void rtl_ax_station_drop(u8 *data,unsigned id,unsigned ac){(void)data;(void)id;(void)ac;drops++;}
static unsigned rtl_ax_tx_dma_channel(unsigned tid,unsigned band){(void)band;return tid&3;}
'''
common = common.replace('assert(cat==1 && !receive && done);', 'assert(cat==1 && !receive && (cls==9 || done));')
common = common.replace('if(cls==5){', 'if(cls==5 && func==2)return 0;\n if(cls==5){')
common = common.replace('if(cls==8||cls==6)return 0;', 'if(cls==8||cls==6||cls==9)return 0;')
tx, monitor = read('tx.c'), read('monitor.h')
code = common + preamble
for name in ('rtl_ax_tx_pe_pending', 'rtl_ax_tx_key_pending', 'rtl_ax_tx_waiters',
             'rtl_ax_tx_flush_key', 'rtl_ax_tx_station_pending', 'rtl_ax_tx_wait_station'):
    code += function(tx, name)
code += function(monitor, 'rtl_ax_drop_station')
code += function(monitor, 'rtl_ax_tx_ba_finish')
code += function(monitor, 'rtl_ax_security_cleanup')
code += function(monitor, 'rtl_ax_set_key')
code += r'''
int main(void){
 u8 memory[0x20000]={};struct rtl_ax_tx tx={.pe_active=true,.pe={memory}};
 struct rtl_ax_ap r={.tx=&tx};struct ieee80211_hw hw={&r,0};struct ieee80211_sta sta={{{7}}};
 reset();
 /* GTK deletion can fail without an NI peer retirement: keep the real ARM WP. */
 struct ieee80211_key_conf key=new_key(1,false);
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,0,&key));
 tx.pending[0]=true;tx.slots[0].encrypted=true;tx.slots[0].key_index=key.hw_key_idx;tx.slots[0].macid=7;
 assert(rtl_ax_set_key(&hw,DISABLE_KEY,0,0,&key)==-ETIMEDOUT);
 unsigned initial_waits=waits;assert(initial_waits==1 && r.security_deleting[0]);
 for(unsigned i=0;i<100;i++){
  assert(rtl_ax_security_cleanup(&r));
  assert(r.security_ids[0] && hardware_cam[0] && r.security_unlink[0]);
 }
 printf("key retry waits=%u elapsed_budget_ms=%u\n",waits-initial_waits,wait_ms-1000);
 assert(waits==initial_waits); // baseline adds 100 seconds under wiphy
 /* Neither an unrelated key nor another station is stalled by that owner. */
 struct ieee80211_key_conf next=new_key(0,true);
 assert(!rtl_ax_set_key(&hw,SET_KEY,0,&sta,&next) && next.hw_key_idx==1);
 tx.pending[0]=false;
 assert(!rtl_ax_security_cleanup(&r) && !r.security_ids[0] && !hardware_cam[0]);
 assert(r.security_ids[1] && hardware_cam[1]);
 /* Real PE owner metadata gates key and STA cleanup until its completion bit. */
 put_unaligned_le32(0x6000000,memory+0xc400);
 put_unaligned_le32(7<<8|1<<17,memory+0xc404);
 assert(rtl_ax_tx_key_pending(&tx,1));
 assert(!rtl_ax_tx_key_pending(&tx,2));
 unsigned before=waits;
 for(unsigned i=0;i<100;i++){
  assert(rtl_ax_drop_station(&r,7,IEEE80211_NUM_TIDS)==-EAGAIN);
  assert(get_unaligned_le32(memory+0xc400)==0x6000000);
 }
 assert(waits==before && drops==400);
 r.tx_ba[7].stopping=1;
 for(unsigned i=0;i<100;i++)assert(rtl_ax_tx_ba_finish(&r,7,0)==-EAGAIN);
 assert(waits==before && r.tx_ba[7].stopping && !ba_done);
 assert(!rtl_ax_drop_station(&r,8,IEEE80211_NUM_TIDS));
 put_unaligned_le32(BIT(31)|7<<8|1<<17,memory+0xc404);
 assert(!rtl_ax_drop_station(&r,7,IEEE80211_NUM_TIDS));
 assert(!rtl_ax_tx_key_pending(&tx,1));
 assert(!rtl_ax_tx_ba_finish(&r,7,0) && !r.tx_ba[7].stopping && ba_done==1);
 /* Blocking wait API remains for first deletion and BA SSN setup. */
 put_unaligned_le32(7<<8|1<<17,memory+0xc404);
 assert(rtl_ax_tx_wait_station(&tx,7,IEEE80211_NUM_TIDS)==-ETIMEDOUT);
 assert(!get_unaligned_le32(memory+PE_PROBE_MAILBOX+312));
 puts("PASS: 100 lost ARM-key, PE-STA and BA completion retries never sleep; ownership and unrelated clients survive; late RPQ resumes cleanup");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-cleanup-') as directory:
    p = Path(directory)
    # security.h needs the transport shim defined by the existing key fixture.
    split = common.index('struct rtl_ax_ap {')
    code = code[:split] + '#include "' + str(driver / 'security.h') + '"\n' + code[split:]
    (p / 'check.c').write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-Wno-misleading-indentation', '-Wno-unused-parameter', '-fsanitize=address,undefined', str(p / 'check.c'), '-o', str(p / 'check')], check=True)
    subprocess.run([str(p / 'check')], check=True, timeout=10)
