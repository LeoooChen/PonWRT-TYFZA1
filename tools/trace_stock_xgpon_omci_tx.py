#!/usr/bin/env python3
"""Compare real OMCI framing and native DMA Header A with the original DAL."""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from arm64_module_oracle import ModuleOracle
from unicorn.arm64_const import UC_ARM64_REG_X5

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
module = root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-rtk.ko'


def function(file, name):
    source = (drivers / file).read_text()
    start = re.search(r'^(?:static )?netdev_tx_t ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "rtl9617c-pon-mode.h"
#include "rtl9617c-pon-omci-format.h"
typedef uint8_t u8;typedef uint32_t u32;typedef int netdev_tx_t;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U>>(31-(h)))&(~0U<<(l)))
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define RTL_DMA_COUNT 64
#define RTL_DMA_BUFFER_SIZE 2048
#define VP_WRITE 0
#define VP_MISC_IRQ 4
#define GFP_ATOMIC 0
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define READ_ONCE(a) (a)
#define dma_wmb() ((void)0)
#define cpu_to_le32(a) (a)
struct sk_buff {unsigned int len;u8 data[2100];};
struct net_device {void *priv;struct {unsigned int tx_dropped;} stats;};
struct rtl9617c_pon {enum rtl_pon_mode mode;void *ni_dev;};
struct rtl9617c_ni_frame {u32 header_high,header_low;const u8 *prefix;unsigned int prefix_length;bool raw_payload;};
struct descriptor {u32 address,control;};
struct rtl_dma_tx {
 unsigned int write,port;u8 *memory;u32 memory_dma;
 struct descriptor *descriptors;unsigned int lengths[64];struct net_device *owners[64];
};
u8 dma_memory[64*2048+64*8+32];
static struct descriptor descriptors[64];
static struct rtl_dma_tx tx={.memory=dma_memory,.descriptors=descriptors};
static struct rtl9617c_pon pon,*pon_pointer=&pon;
static struct net_device device={.priv=&pon_pointer};
static u8 dma_registers[8];
#define netdev_priv(d) ((d)->priv)
#define netif_carrier_ok(d) ((void)(d),true)
#define netif_stop_queue(d) ((void)(d))
static void *rtl_dma_vp(struct rtl_dma_tx *p) {(void)p;return dma_registers;}
static void rtl_dma_tx_complete(struct rtl_dma_tx *p,struct net_device *d) {(void)p;(void)d;}
static bool rtl_dma_tx_available(struct rtl_dma_tx *p) {(void)p;return true;}
static void writel(u32 value,void *p) {memcpy(p,&value,4);}
static void put_unaligned_le32(u32 value,void *p) {memcpy(p,&value,4);}
static void put_unaligned_be32(u32 value,void *p) {u8 *b=p;b[0]=value>>24;b[1]=value>>16;b[2]=value>>8;b[3]=value;}
static int skb_copy_bits(struct sk_buff *s,unsigned int off,void *out,unsigned int n) {
 assert(off+n<=s->len);memcpy(out,s->data+off,n);return 0;
}
static struct sk_buff *skb_copy_expand(struct sk_buff *s,unsigned int head,unsigned int tail,int flags) {
 (void)head;(void)tail;(void)flags;struct sk_buff *copy=malloc(sizeof(*copy));*copy=*s;return copy;
}
static u8 *skb_put(struct sk_buff *s,unsigned int count) {u8 *p=s->data+s->len;s->len+=count;assert(s->len<=sizeof(s->data));return p;}
static void dev_consume_skb_any(struct sk_buff *s) {free(s);}
static void dev_kfree_skb_any(struct sk_buff *s) {free(s);}
static int rtl_omci_mic(struct rtl9617c_pon *p,u8 direction,const u8 *data,unsigned int bytes,u8 mic[4]) {
 (void)p;(void)data;(void)bytes;assert(direction==2);memcpy(mic,"\x12\x34\x56\x78",4);return 0;
}
'''
bridge = r'''
static netdev_tx_t rtl9617c_ni_transmit(void *ni,struct net_device *dev,struct sk_buff *skb,const struct rtl9617c_ni_frame *frame) {
 (void)ni;return rtl_dma_tx_frame(&tx,skb,dev,frame);
}
'''
wrapper = r'''
int transmit(unsigned int mode,const u8 *data,unsigned int length) {
 memset(dma_memory,0,sizeof(dma_memory));tx.write=0;device.stats.tx_dropped=0;pon.mode=mode;
 struct sk_buff *skb=calloc(1,sizeof(*skb));skb->len=length;memcpy(skb->data,data,length);
 assert(rtl_omci_transmit(skb,&device)==NETDEV_TX_OK && !device.stats.tx_dropped);
 return (descriptors[0].control>>8)&0x1fff;
}
unsigned int ordinary(unsigned int raw,unsigned int prefix) {
 static const u8 container[16]={};
 struct rtl9617c_ni_frame frame={.header_high=BIT(13),.header_low=0x20002000,
  .prefix=container,.prefix_length=prefix,.raw_payload=raw};
 struct sk_buff *skb=calloc(1,sizeof(*skb));skb->len=42;tx.write=0;
 assert(!rtl_dma_tx_frame(&tx,skb,&device,&frame));
 return *(u32*)(dma_memory+64*8+12)>>15&0x3fff;
}
'''
records = []
with tempfile.TemporaryDirectory(prefix='xgpon-omci-wire-') as directory:
    work = Path(directory)
    (work / 'native.c').write_text(fixture + function('rtl9617c-dma.c', 'rtl_dma_tx_frame') + bridge +
                                  function('rtl9617c-pon-omci.c', 'rtl_omci_transmit') + wrapper)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-I', str(drivers),
                    str(work / 'native.c'), '-o', str(work / 'native.so')], check=True)
    native = ctypes.CDLL(str(work / 'native.so'))
    native.transmit.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    native.ordinary.argtypes = [ctypes.c_uint, ctypes.c_uint]
    memory = (ctypes.c_ubyte * (64 * 2048 + 64 * 8 + 32)).in_dll(native, 'dma_memory')
    for mode in (4, 5):
        submitted = []

        def callback(name, arguments):
            if name == '__netdev_alloc_skb':
                skb, data = oracle.scratch + 0x1000, oracle.scratch + 0x2000
                oracle.uc.mem_write(skb, bytes(256))
                oracle.uc.mem_write(data, bytes(2100))
                oracle.word(skb + 200, data)
                return skb
            if name == 'ca_generate_omci_mic':
                oracle.uc.mem_write(oracle.uc.reg_read(UC_ARM64_REG_X5), b'\x12\x34\x56\x78')
            elif name == 'memcpy':
                oracle.uc.mem_write(arguments[0], bytes(oracle.uc.mem_read(arguments[1], arguments[2])))
                return arguments[0]
            elif name == '__rtk_ni_start_xmit':
                length = oracle.unpack(arguments[0] + 112, 'I')[0]
                data = oracle.unpack(arguments[0] + 200, 'Q')[0]
                submitted.append(dict(header=oracle.unpack(arguments[1], 'Q')[0], length=length,
                                      data=bytes(oracle.uc.mem_read(data, length))))
            else:
                raise RuntimeError((name, arguments))
            return 0

        oracle = ModuleOracle(module, ['dal_rt_rtl8277c_gpon_omci_tx'], callback,
                              external_symbols={'ca_rtk_debug': 0x5007000})
        oracle.uc.mem_write(oracle.symbols['gpon_init'][0], struct.pack('<I', 1))
        oracle.uc.mem_write(oracle.symbols['CA_PON_MODE'][0], bytes([mode]))
        for content in (None, 0, 32, 34, 76):
            body = bytearray(44 if content is None else 10 + content)
            body[0:4] = b'\x80\x01\x29\x0a' if content is None else b'\x80\x01\x29\x0b'
            if content is not None:
                body[8:10] = content.to_bytes(2, 'big')
            wire = len(body) + 4
            submitted.clear()
            oracle.uc.mem_write(oracle.scratch, bytes(body) + bytes(4))
            assert oracle.call('dal_rt_rtl8277c_gpon_omci_tx', wire, oracle.scratch) == 0
            original = submitted[0]
            actual_dma_bytes = native.transmit(mode, bytes(body), len(body))
            offset = 64 * 8
            high, low = struct.unpack('<II', bytes(memory)[offset + 8:offset + 16])
            packet = bytes(memory)[offset + 16:offset + actual_dma_bytes]
            # Padding is outside the valid Header A span, but the DMA buffer includes it.
            assert packet[:16 + wire] == original['data'][:16 + wire]
            assert actual_dma_bytes == original['length'] + 16
            assert high == original['header'] & 0xffffffff
            records.append(dict(mode=mode, content=content, wire_bytes=wire,
                                original_header_length=original['header'] >> 47 & 0x3fff,
                                native_header_length=low >> 15 & 0x3fff,
                                dma_payload_bytes=original['length'],
                                header_matches=low == original['header'] >> 32,
                                body_prefix_mic_and_dma_span_match=True))
    for raw, prefix, length in [(0, 0, 60), (0, 16, 76), (1, 16, 58)]:
        assert native.ordinary(raw, prefix) == length

result = dict(module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(), records=records,
              scope='Actual original DAL OMCI TX and native OMCI producer plus DMA TX function. '
                    'Allocator, MIC bytes, NI completion and submission, locks, MMIO and skb primitives are modeled. '
                    'REA __rtk_ni_start_xmit separately shows the supplied Header A is copied unchanged. '
                    'No optical transmission or cryptographic correctness claim.')
(root / 'logs/stock-xgpon-omci-wire.json').write_text(json.dumps(result, indent=2) + '\n')
for record in records:
    print(record)
assert all(r['header_matches'] for r in records), 'Native Header A uses the padded DMA length for short OMCI'
print('Baseline and short/full extended OMCI: native Header A, wire bytes and padded DMA span match the original DAL')
