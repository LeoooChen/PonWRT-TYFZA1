#!/usr/bin/env python3
"""Execute final RX ownership handoff with mixed ARM and retained NI slots."""
import ast
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
current = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap/rx.c'
fixture = ast.parse((ROOT / 'tools/check_rx_buffer_handoff.py').read_text())
common = next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'common' for t in n.targets))
common = common.replace('#include <assert.h>', '#include <assert.h>\n#include <stdlib.h>\n#include <errno.h>')
common = common.replace('typedef uint8_t u8;', 'typedef int32_t s32; typedef uint32_t __le32; typedef uint8_t u8;')
common = common.replace('u8 *descriptors;', 'u8 *descriptors, *buffers;')
common = common.replace('bool buffer_pool_initialized;', 'bool buffer_pool_initialized, running;')
common = common.replace('unsigned irq_mask;', 'unsigned irq_mask,uplink_wifi_id;')
common = common.replace('#define RTL_AX_RX_ENTRIES', '#define BIT(n) (1U<<(n))\n#define RTL_AX_RX_ENTRIES')
common = common.replace('void (*write32)(void*,unsigned,u32);', 'void (*write32)(void*,unsigned,u32);void (*write16)(void*,unsigned,uint16_t);')
begin = common.index('static void dma_sync_single_for_device(')
end = common.index('static void write32(', begin)
common = common[:begin] + r'''
#define RTL_AX_RX_IRQS 0x00180005
static unsigned RXBD_notifications;
static void dma_sync_single_for_device(void *dev,u32 dma,unsigned bytes,int dir){
 (void)dev;(void)dma;(void)bytes;(void)dir;sync_count++;}
static void dma_sync_single_for_cpu(void *dev,u32 dma,unsigned bytes,int dir){
 (void)dev;(void)dma;(void)bytes;(void)dir;}
static void write16(void *ctx,unsigned reg,uint16_t value){
 (void)ctx;assert(reg==0x1218 && value==2);RXBD_notifications++;}
static void rtl_ax_rx_pe_drain(struct rtl_ax_rx *rx){(void)rx;}
static void rtl_ax_rx_pe_rxq_drain(struct rtl_ax_rx *rx){(void)rx;}
static void rtl_ax_rx_uplink_sync(struct rtl_ax_rx *rx){(void)rx;}
''' + common[end:]


def function(source, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


case = r'''
int main(void){
 u8 descriptors[256*8]={},memory[131072]={},states[131072]={};
 u8 *buffers=calloc(258,12288);
 struct rtl_ax_io io={.write32=write32,.write16=write16};
 struct rtl_ax_rx rx={.io=&io,.uplink_states=states,.pe_forward_dma=0x8000000,
  .queues={{descriptors,buffers,0x9000000,0x6000000,254,8190},{0,0,0,0x7000000,255,8191}}};
 for(unsigned slot=0;slot<256;slot++)put_unaligned_le32(0x6000000+slot*12288,descriptors+slot*8+4);
 rtl_ax_rx_pe_rpq_start(&rx,memory);
 put_unaligned_le32(254,memory+0x214c);put_unaligned_le32(2,memory+0x2144);
 put_unaligned_le32(1234,memory+0x2148);put_unaligned_le32(101,memory+0x216c);
 for(unsigned slot=0;slot<256;slot++)put_unaligned_le32(0xabc00000+slot,buffers+slot*12288);
 put_unaligned_le32(1U<<30,memory+0xf000+254*4);
 put_unaligned_le32((1U<<31)|98,memory+0xf000+255*4);
 put_unaligned_le32((1U<<31)|100,memory+0xf000);
 put_unaligned_le32((1U<<31)|101,memory+0xf000+4);
#ifdef CURRENT_STOP
 /* NI is not at the final frame yet: no slot, tag or DMA reference changes. */
 put_unaligned_le32(100,memory+0x216c);
 assert(rtl_ax_rx_pe_rpq_stop(&rx)==-EBUSY && rx.rpq_memory==memory && rx.rpq_owned);
 assert(get_unaligned_le32(buffers)==0xabc00000 && get_unaligned_le32(buffers+12288)==0xabc00001);
 assert(RXBD_notifications==0 && get_unaligned_le32(memory+0x214c)==254);
 put_unaligned_le32(101,memory+0x216c);
 assert(rtl_ax_rx_pe_rpq_stop(&rx)==0);
 assert(get_unaligned_le32(buffers)==0xabc00000 && get_unaligned_le32(buffers+12288)==0xabc00001);
 assert(get_unaligned_le32(descriptors+4)==0x6000000+256*12288);
 assert(get_unaligned_le32(descriptors+12)==0x6000000+257*12288);
 assert(get_unaligned_le32(memory+0x17810)==1 && get_unaligned_le32(memory+0x17818)==2);
 assert(get_unaligned_le32(memory+0x17814)==100 && get_unaligned_le32(memory+0x1781c)==101);
 rtl_ax_rx_pe_rpq_start(&rx,memory);
 assert(get_unaligned_le32(memory+0x17800)==0 && get_unaligned_le32(memory+0x17804)==0);
 assert(get_unaligned_le32(memory+0x17810)==1 && get_unaligned_le32(memory+0x17818)==2);
#else
 rtl_ax_rx_pe_rpq_stop(&rx);
 assert(get_unaligned_le32(buffers)==0 && get_unaligned_le32(buffers+12288)==0);
 assert(get_unaligned_le32(descriptors+4)==0x6000000 && get_unaligned_le32(memory+0x17810)==0);
#endif
 assert(get_unaligned_le32(buffers+254*12288)==0 && get_unaligned_le32(buffers+255*12288)==0);
 assert(rx.queues[0].consumer==2 && rx.queues[0].tag==1234 && RXBD_notifications==1);
 free(buffers);
}
'''
out = ROOT / 'artifacts/pe-stop66'
out.mkdir(exist_ok=True)
results = []
for label, path in [('before', ROOT / 'artifacts/pe-stop66-baseline/rx.c'), ('after', current)]:
    source = path.read_text()
    names = ['rtl_ax_rx_buffer_offset', 'rtl_ax_rx_pe_rpq_start']
    if label == 'after':
        names += ['rtl_ax_rx_tail_recycle', 'rtl_ax_rx_tail_hold']
    names += ['rtl_ax_rx_pe_rpq_stop']
    c = out / (label + '.c')
    c.write_text(common + ''.join(function(source, n) for n in names) +
                 ('#define CURRENT_STOP\n' if label == 'after' else '') + case)
    binary = out / label
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    results.append(dict(version=label, mixed_ARM_safe_NI_and_two_tail_slots=True,
                        NI_tail_payload_preserved=label == 'after',
                        unconsumed_NI_keeps_ownership=label == 'after',
                        retained_payloads_survive_following_resume=label == 'after',
                        RXBD_255_to_0_wrap=True))
print(json.dumps(dict(scope='Actual current and baseline ARM stop/start functions with DMA/IRQ and empty exception-drain callbacks. No physical DMA timing or hardware teardown test.', results=results), indent=2))
