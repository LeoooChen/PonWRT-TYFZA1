#!/usr/bin/env python3
"""Run actual ARM BCMC submission and feed its requests to linked PE WD code."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
# Reuse the existing complete monitor/TX fixture; none of its BAR test runs.
fixture = root / 'scripts/test_radio_tx_bar.py'
setup = fixture.read_text().split("code += r'''\nint main(void){", 1)[0]
env = {'__file__': str(fixture), '__name__': 'bcmc_fixture'}
exec(compile(setup, str(fixture), 'exec'), env)
args, function, driver = (env[n] for n in ('args', 'function', 'driver'))
code = env['code']
code = code.replace('assert(s->len==20);', 'assert(s->len>=24);')
code = code.replace('static unsigned find_first_zero_bit(const unsigned *p,unsigned n){(void)p;(void)n;return 0;}',
                    'static unsigned find_first_zero_bit(const unsigned *p,unsigned n){for(unsigned i=0;i<n;i++)if(!p[i])return i;return n;}')
code = code.replace('static void rtl_ax_security_tx(u8 *page,unsigned id,unsigned key,u64 pn){(void)page;(void)id;(void)key;(void)pn;assert(0);}',
                    function((driver / 'security.h').read_text(), 'rtl_ax_security_tx'))
code = code.replace('assert(reg==0x1078&&value==1);', 'assert(reg>=0x1058&&reg<=0x107c&&value<=3);')
code = code.replace('struct ieee80211_tx_info {unsigned flags;',
                    'struct ieee80211_tx_info {unsigned flags,hw_queue;')
code += r'''
typedef unsigned ieee80211_tx_result;
#define TX_CONTINUE 0
#define TX_QUEUED 1
#define NL80211_IFTYPE_AP_VLAN 4
#define TOTAL_MAX_TX_BUFFER 512
#define AP_MAX_BC_BUFFER 128
#define ieee80211_hw_check(hw,flag) false
#define atomic_read(p) (*(p))
#define ps_dbg(...) ((void)0)
struct skb_queue {unsigned count;};
struct ps_data {int num_sta_ps;struct skb_queue bc_buf;};
struct ieee80211_local {struct ieee80211_hw hw;unsigned total_ps_buffered;};
struct ieee80211_sub_if_data {struct {unsigned type,cab_queue;}vif;
 struct {struct ps_data ps;}*bss;struct {struct {struct ps_data ps;}mesh;}u;};
struct ieee80211_tx_data {struct sk_buff *skb;struct ieee80211_sub_if_data *sdata;struct ieee80211_local *local;};
static bool ieee80211_vif_is_mesh(const void *v){(void)v;return false;}
static bool ieee80211_is_probe_req(u16 f){return (f&0xfc)==0x40;}
static bool skb_queue_empty(struct skb_queue *q){return !q->count;}
static unsigned skb_queue_len(struct skb_queue *q){return q->count;}
static struct sk_buff *skb_dequeue(struct skb_queue *q){(void)q;assert(0);return NULL;}
static void skb_queue_tail(struct skb_queue *q,struct sk_buff *s){(void)q;(void)s;assert(0);}
static void purge_old_ps_buffers(struct ieee80211_local *l){(void)l;assert(0);}
'''
# Both QUEUE_CONTROL and HOST_BROADCAST_PS_BUFFERING are absent in the
# native hardware flags. Execute Linux's actual decision before submission.
linux = (env['kernel'] / 'net/mac80211/tx.c').read_text()
code += 'static ieee80211_tx_result\n' + function(linux, 'ieee80211_tx_h_multicast_ps_buf')
code += r'''
int main(void){
 static u8 memory[0x20000],pages[6*264*128],headers[384*128],ring[6*256*8];
 const struct rtl_ax_io io={.write16=write16};
 const u8 groups[3][6]={{1,0,0x5e,1,2,3},{0x33,0x33,0,1,2,3},{255,255,255,255,255,255}};
 for(unsigned pe=0;pe<2;pe++)for(unsigned group=0;group<3;group++)
 for(unsigned crypto=0;crypto<5;crypto++){
  memset(memory,0,sizeof(memory));memset(pages,0,sizeof(pages));
  struct rtl_ax_tx t={.running=true,.pe_active=pe,.pe={memory},.pages=pages,.headers=headers,.ring=ring,.pages_dma=0x05100000,.io=&io};
  struct rtl_ax_ap radio={.tx=&t,.chip=RTL_AX_8852C};struct ieee80211_hw hw={&radio};
  struct ieee80211_tx_control control={};struct vif vif={.type=NL80211_IFTYPE_AP};
  struct ieee80211_local local={.hw=hw};
  struct ieee80211_sub_if_data sdata={.vif={.type=NL80211_IFTYPE_AP}};
  typeof(*sdata.bss) bss={};sdata.bss=&bss;
  struct key key={.hw_key_idx=63,.keyidx=crypto?crypto-1:0,.tx_pn=0xffffffffULL};
  struct sk_buff skb[3]={};u8 raw[3][324]={};
  for(unsigned phase=0;phase<3;phase++){
   struct ieee80211_hdr *hdr=(void *)raw[phase];hdr->frame_control=0x0208|(crypto?0x4000:0);hdr->seq_ctrl=(phase+1)<<4;
   memcpy(hdr->addr1,groups[group],6);hdr->addr2[0]=2;hdr->addr3[0]=4;
   skb[phase]=(struct sk_buff){.data=raw[phase],.len=sizeof(raw[phase]),.priority=7,
    .info={.flags=IEEE80211_TX_CTL_NO_ACK,
    .control={.vif=&vif,.hw_key=crypto?&key:NULL}}};
   bss.ps.num_sta_ps=phase==1;
   struct ieee80211_tx_data linux_tx={.skb=&skb[phase],.sdata=&sdata,.local=&local};
   assert(ieee80211_tx_h_multicast_ps_buf(&linux_tx)==TX_CONTINUE);
   assert(!!(skb[phase].info.flags&IEEE80211_TX_CTL_SEND_AFTER_DTIM)==(phase==1));
   u8 before_frame[324];memcpy(before_frame,raw[phase],sizeof(before_frame));
   unsigned before=freed;rtl_ax_monitor_tx(&hw,&control,&skb[phase]);
   assert(freed==before&&t.submitted==phase+1&&t.submitted_ring[5]==phase+1);
   assert(t.slots[phase].ring==5&&t.slots[phase].macid==0&&!t.slots[phase].pe_sequence);
   assert(!t.hardware_pn&&!t.ampdu_submitted&&!t.pe_ethernet);
   assert(key.tx_pn==0xffffffffULL+(crypto?phase+1:0));
   assert(!memcmp(skb[phase].data,before_frame,sizeof(before_frame)));
   if(pe){
    u32 *q=(void *)(memory+0x4000+phase*128);
    assert(q[1]==(5|phase<<8|(phase+1)<<16)&&q[2]==(324|24<<16|1<<24));
    assert(q[4]==((phase+1)|BIT(13)|BIT(14)|(crypto?BIT(15):0)));
    assert(((u64)q[7]<<32|q[6])==(crypto?key.tx_pn:0));
    assert(fwrite(q,1,128,stdout)==128);
   }else{
    u8 *wd=pages+(5*264+phase)*128;
    assert((get_unaligned_le32(wd)>>16&15)==9);
    assert((get_unaligned_le32(wd+8)>>17&63)==0x11);
    assert(!(get_unaligned_le32(wd)&BIT(6))&&!!(get_unaligned_le32(wd+4)&BIT(6))==!!crypto);
    assert(get_unaligned_le32(wd+16)==(crypto?(u32)key.tx_pn<<16:0));
    assert(get_unaligned_le32(wd+20)==(crypto?key.tx_pn>>16:0));
    assert(fwrite(wd,1,128,stdout)==128);
   }
  }
 }
 /* Unicast/AP, STA group DA with AP RA, injected, mesh and management
  * retain their own queues. The AP-only policy must not classify by DA. */
 for(unsigned mode=0;mode<6;mode++)for(unsigned pe=0;pe<2;pe++){
  memset(memory,0,sizeof(memory));
  struct rtl_ax_tx t={.running=true,.pe_active=pe,.pe={memory},.pages=pages,.headers=headers,.ring=ring,.pages_dma=0x05100000,.io=&io};
  struct rtl_ax_ap radio={.tx=&t,.chip=RTL_AX_8852C,.sta_role=mode==1};struct ieee80211_hw hw={&radio};
  struct ieee80211_tx_control control={};struct vif vif={.type=mode==1?NL80211_IFTYPE_STATION:mode==3?7:NL80211_IFTYPE_AP};
  u8 raw[324]={};struct ieee80211_hdr *hdr=(void *)raw;
  hdr->frame_control=mode==1?0x0108:mode==4?0x00d0:0x0208;
  hdr->addr1[0]=(mode==0||mode==1)?2:1;hdr->addr3[0]=1;
  struct sk_buff skb={.data=raw,.len=sizeof(raw),.info={.control={.vif=mode==5?NULL:&vif},.flags=mode==2?IEEE80211_TX_CTL_INJECTED:0}};
  unsigned before=freed;rtl_ax_monitor_tx(&hw,&control,&skb);
  assert(freed==before&&t.submitted==1&&t.slots[0].ring==(mode==4?4:0));
 }
}
'''
with tempfile.TemporaryDirectory(prefix='bcmc-tx-') as tmp:
    path = Path(tmp)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    output = subprocess.check_output([str(path / 'test')])
assert len(output) == 90 * 128
sys.path.insert(0, str(root / 'analysis/python-deps'))
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_SP, UC_MIPS_REG_RA

symbols = {fields[2]: int(fields[0], 16) for line in subprocess.check_output(
    ['llvm-nm-18', str(args.pe / 'pe-wd.elf')], text=True).splitlines() if len(fields := line.split()) == 3}
firmware = (args.pe / 'pe-tx.bin').read_bytes()
for recipe in range(15):
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    base, mailbox, ring, pages = 0x04000000, 0x04002000, 0x05000000, 0x05100000
    for address, size in ((0x1fc00000, 0x10000), (base, 0x20000), (ring, 0x10000), (pages, 0x32000)):
        u.mem_map(address, size)
    u.mem_write(0x1fc00000, firmware)
    def put(address, value):
        u.mem_write(address, struct.pack('<I', value))
    put(mailbox + 11 * 4, ring)
    put(mailbox + 12 * 4, pages)
    for phase in range(3):
        index = recipe * 3 + phase
        arm = output[index * 128:(index + 1) * 128]
        request = output[(index + 45) * 128:(index + 46) * 128]
        u.mem_write(base + 0x4000, request)
        for register, value in ((UC_MIPS_REG_A0, mailbox), (UC_MIPS_REG_A1, base),
                                (UC_MIPS_REG_A2, base + 0x4000), (UC_MIPS_REG_A3, base + 0x7000),
                                (UC_MIPS_REG_SP, base + 0x1fff0)):
            u.reg_write(register, value | 0xa0000000)
        u.reg_write(UC_MIPS_REG_RA, 0x9fc01ff0)
        u.emu_start(symbols['pe_build_tx'], 0x9fc01ff0, count=100000)
        wd = bytes(u.mem_read(pages + (5 * 264 + phase) * 128, 128))
        assert wd == arm, (recipe, phase, wd.hex(), arm.hex())
        txbd = struct.unpack('<II', u.mem_read(ring + (5 * 256 + phase) * 8, 8))
        assert txbd == (0x40000000 | 70, pages + (5 * 264 + phase) * 128)
print('BCMC:90 actual ARM/PE submissions across PS transitions, four GTK IDs/PN carry/open, '
      '12 AP/STA/injected/mesh/management controls and45 linked PE WD/WP/TXBD matches passed')
