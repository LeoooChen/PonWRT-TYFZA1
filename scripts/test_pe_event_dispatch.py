#!/usr/bin/env python3
"""Exercise production shared-timer dispatch for RX-only and TX events."""
import argparse
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
root = Path(__file__).resolve().parents[1]
parser.add_argument('--source', type=Path, default=root /
    'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/pe-event.h')
args = parser.parse_args()
source = args.source.read_text()
start = source.index('static irqreturn_t pe_event_interrupt(')
end = source.index('\nstatic void pe_event_release(', start)
fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdbool.h>
typedef uint32_t u32;
typedef int irqreturn_t;
#define IRQ_HANDLED 1
#define READ_ONCE(x) (x)
#define PE_PROBE_MAILBOX 0x2000
struct rtl_ax_rx { unsigned id; };
struct rtl_ax_tx { struct { unsigned char *memory; } pe;
 struct rtl_ax_rx *rx; unsigned key_flush_wait; struct { unsigned ps_work; } ni; };
static struct rtl_ax_tx *pe_event_radios[2];
static unsigned char timer[36];
static void *pe_event_timer = timer;
static unsigned pe_event_interrupts, rx_ack[2], rx_wake[2], tx_wake[2], ps[2];
static bool inject;
static u32 get_unaligned_le32(const void *p) { u32 v; memcpy(&v,p,4); return v; }
static void put_unaligned_le32(u32 v,void *p) {
 memcpy(p,&v,4);
 if (inject && p == pe_event_radios[0]->pe.memory+PE_PROBE_MAILBOX+264) {
  u32 next=v+1; memcpy(pe_event_radios[0]->pe.memory+PE_PROBE_MAILBOX+276,&next,4); inject=false;
 }
}
static void writel(u32 v,void *p) { put_unaligned_le32(v,p); }
static void rtl_ax_rx_pe_event_ack(struct rtl_ax_rx *rx) { rx_ack[rx->id]++; }
static void rtl_ax_rx_pe_wake(struct rtl_ax_rx *rx) { rx_wake[rx->id]++; }
static void wake_up_all(unsigned *p) { tx_wake[*p]++; }
static void schedule_work(unsigned *p) { ps[*p]++; }
'''
cases = r'''
static void record(unsigned id,u32 sent,u32 ack) {
 put_unaligned_le32(sent,pe_event_radios[id]->pe.memory+PE_PROBE_MAILBOX+276);
 put_unaligned_le32(ack,pe_event_radios[id]->pe.memory+PE_PROBE_MAILBOX+264);
}
int main(void) {
 unsigned char memory[2][0x2200]={{0}};
 struct rtl_ax_rx rx[2]={{0},{1}};
 struct rtl_ax_tx tx[2]={ {.pe={memory[0]},.rx=&rx[0],.key_flush_wait=0,.ni={0}},
  {.pe={memory[1]},.rx=&rx[1],.key_flush_wait=1,.ni={1}} };
 pe_event_radios[0]=&tx[0]; pe_event_radios[1]=&tx[1];
 record(0,7,7); record(1,9,9);
 assert(pe_event_interrupt(0,0)==IRQ_HANDLED);
 assert(rx_ack[0]==1 && rx_ack[1]==1 && rx_wake[0]==1 && rx_wake[1]==1);
 assert(ps[0]==0 && ps[1]==0); /* RX-only event must not queue either TX scan. */
 record(0,8,7); pe_event_interrupt(0,0);
 assert(ps[0]==1 && ps[1]==0);
 assert(get_unaligned_le32(memory[0]+PE_PROBE_MAILBOX+264)==8);
 record(1,10,9); pe_event_interrupt(0,0);
 assert(ps[0]==1 && ps[1]==1);
 record(0,9,8); record(1,11,10); pe_event_interrupt(0,0);
 assert(ps[0]==2 && ps[1]==2); /* Simultaneous TX events reach both radios. */
 record(0,0,UINT32_MAX); pe_event_interrupt(0,0); assert(ps[0]==3);
 record(0,1,0); inject=true; pe_event_interrupt(0,0);
 assert(ps[0]==4);
 assert(get_unaligned_le32(memory[0]+PE_PROBE_MAILBOX+264)!=
        get_unaligned_le32(memory[0]+PE_PROBE_MAILBOX+276));
 pe_event_interrupt(0,0); assert(ps[0]==5); /* Arrival after ACK survives. */
 pe_event_radios[1]=0; record(0,3,2); pe_event_interrupt(0,0); assert(ps[0]==6);
 assert(tx_wake[0]==pe_event_interrupts && rx_wake[0]==pe_event_interrupts);
 assert(get_unaligned_le32(timer+4)==0 && get_unaligned_le32(timer+12)==0 &&
        get_unaligned_le32(timer+20)==1);
}
'''
with tempfile.TemporaryDirectory(prefix='pe-event-dispatch-') as tmp:
    path = Path(tmp)
    (path/'test.c').write_text(fixture + source[start:end] + cases)
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
                    '-fsanitize=address,undefined',str(path/'test.c'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
print('Shared PE event: RX-only, TX-only, dual radio, wrap and racing publication PASS')
