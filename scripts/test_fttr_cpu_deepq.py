#!/usr/bin/env python3
"""Compare original virtual-port DEEPQ selection with the native FTTR DMA header."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X12, UC_ARM64_REG_SP, UC_ARM64_REG_PC


def function(text, name):
    start = re.search(r'^.*\b' + name + r'\([^;]*?\)\s*\{', text, re.M).start()
    end = text.index('{', start) + 1; depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}'); end += 1
    return text[start:end] + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ni', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert hashlib.sha256(args.ni.read_bytes()).hexdigest() == '16840ba70afc0ae700c2d63c57b8248475a67154eef89536d90028d4fe6486d7'
    device = 0x6000000
    def boundary(name, a):
        if name == 'alloc_etherdev_mqs':
            assert a[0] == 0x1008; oracle.uc.mem_write(device, bytes(0x2000)); return device
        if name == 'aal_ni_hv_glb_internal_port_id_cfg_get':
            oracle.uc.mem_write(a[1], b'\0'); return 0
        if name in ('snprintf', 'netif_napi_add'): return 0
        raise AssertionError(name)
    oracle = ModuleOracle(args.ni, ['ca_ni_virtual_instance_alloc', '__ca_ni_start_xmit_by_specific_vp'],
        boundary, external_symbols={'cpu_number': 0x5007100, 'vabits_actual': 0x5007200,
            'memstart_addr': 0x5007300, 'kimage_voffset': 0x5007400})
    oracle.uc.mem_map(device, 0x2000)
    oracle.uc.mem_write(oracle.scratch, b'fixture\0')
    assert oracle.call('ca_ni_virtual_instance_alloc', 0, oracle.scratch) == device
    private = device + 0x8c0
    assert oracle.unpack(private + 3904, 'B')[0] & 1
    # Execute the original header assignment block after allocation. It reads
    # the real private DEEPQ bit and inserts it into Header A bit62.
    base = oracle.functions['__ca_ni_start_xmit_by_specific_vp'] - 0xa9730
    start, end = base + 0xa9cf0, base + 0xa9d08
    assert oracle.unpack(start, 'I')[0] == 0xf947a181
    stack = oracle.scratch + 0xa000
    cases = []
    for seed in (0, 1 << 13, 0x92345678):
        oracle.uc.mem_write(stack + 328, struct.pack('<I', seed))
        oracle.uc.reg_write(UC_ARM64_REG_X12, private)
        oracle.uc.reg_write(UC_ARM64_REG_SP, stack)
        oracle.uc.emu_start(start, end, count=10)
        assert oracle.uc.reg_read(UC_ARM64_REG_PC) == end
        result = oracle.unpack(stack + 328, 'I')[0]
        assert result == seed | (1 << 30)
        cases.append({'input_high': seed, 'output_high': result})
    drivers = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    xram, dma, header = [(drivers / name).read_text() for name in
        ('rtl9617c-xram.c', 'rtl9617c-dma.c', 'rtl9617c-dma.h')]
    source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32,__le32;typedef uint64_t u64;typedef uintptr_t dma_addr_t;typedef int netdev_tx_t;
#define __iomem
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define max(a,b) ((a)>(b)?(a):(b))
#define READ_ONCE(v) (v)
#define cpu_to_le32(v) (v)
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define VP_BASE 0x100
#define VP_STRIDE 0xa0
#define VP_WRITE 0x18
#define VP_READ 0x1c
struct device { int unused; };
struct net_device {void *data;bool stopped;struct {u64 tx_packets,tx_bytes;} stats;};
struct sk_buff {unsigned len;bool consumed;unsigned char data[2048];};
struct rtl9617c_ni_frame {unsigned prefix_length,header_high,header_low;bool raw_payload;const void *prefix;};
struct rtl_xram {struct {struct device *dev;} p6;};
struct rtl_fttr_port {struct rtl_xram *shared;};
#define netdev_priv(d) ((d)->data)
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static u32 readl(void *p){u32 v;memcpy(&v,p,4);return v;}
static void writel(u32 v,void *p){memcpy(p,&v,4);}
#define dma_wmb() ((void)0)
static int skb_copy_bits(struct sk_buff *s,unsigned off,void *p,unsigned n){assert(off+n<=s->len);memcpy(p,s->data+off,n);return 0;}
static void dev_consume_skb_any(struct sk_buff *s){assert(!s->consumed);s->consumed=true;}
static bool netif_queue_stopped(struct net_device *d){return d->stopped;}
static void netif_stop_queue(struct net_device *d){d->stopped=true;}
static void netif_wake_queue(struct net_device *d){d->stopped=false;}
'''
    source += header[header.index('#define RTL_DMA_DEPTH'):header.index('/* HP=11')]
    source += function(header, 'rtl_dma_tx_available')
    source += ''.join(function(dma, name) for name in ('rtl_dma_vp', 'rtl_dma_tx_complete', 'rtl_dma_tx_frame'))
    source += '''
static struct rtl_dma_tx tx;
static netdev_tx_t rtl9617c_ni_transmit(struct device *device,struct net_device *dev,
 struct sk_buff *skb,const struct rtl9617c_ni_frame *frame) {
 (void)device;return rtl_dma_tx_frame(&tx,skb,dev,frame);
}
'''
    source += function(xram, 'rtl_fttr_xmit')
    source += r'''
int main(void) {
 u32 regs[0x1000/4]={0};struct rtl_xram x={0};struct rtl_fttr_port p={&x};
 struct net_device dev={.data=&p};unsigned bytes=RTL_DMA_COUNT*(sizeof(*tx.descriptors)+RTL_DMA_BUFFER_SIZE);
 tx=(struct rtl_dma_tx){.registers=regs,.vp=8,.memory=calloc(1,bytes),.memory_dma=0x4000000};
 assert(tx.memory);tx.descriptors=tx.memory;
 for(unsigned i=0;i<3;i++) {
  const unsigned lengths[]={64,1518,1996};struct sk_buff skb={.len=lengths[i]};memset(skb.data,0xa5,skb.len);
  assert(rtl_fttr_xmit(&skb,&dev)==NETDEV_TX_OK && skb.consumed);
  unsigned off=RTL_DMA_COUNT*sizeof(*tx.descriptors)+i*RTL_DMA_BUFFER_SIZE;
  assert(readl(tx.memory+off+8)==(BIT(13)|BIT(30)));
  assert(readl(tx.memory+off+12)==((16<<9)|(6<<3)|BIT(29)|(skb.len<<15)));
  assert(!memcmp(tx.memory+off+16,skb.data,skb.len));
 }
 free(tx.memory);puts("Native FTTR CPU packets retain P6 DEEPQ for management, VLAN and maximum frames");
}
'''
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name); (folder / 'test.c').write_text(source)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
            str(folder / 'test.c'), '-o', str(folder / 'test')], check=True)
        subprocess.run([str(folder / 'test')], check=True)
    args.output.write_text(json.dumps({'original': cases,
        'scope': 'Executed original virtual-port allocation and DEEPQ header assignment; production native fttr_xmit plus DMA packing. Descriptor/MMIO modeled; no physical port14 traffic or packet retirement claim.'}, indent=2) + '\n')


if __name__ == '__main__': main()
