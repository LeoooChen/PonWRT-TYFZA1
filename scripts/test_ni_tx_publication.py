#!/usr/bin/env python3
"""Check asynchronous VP8 publication and buffer ownership against stock.

MMIO consumption is modeled, not a simulation of the DMA engine internals.
--stock also executes the board module's two asynchronous publication tails.
No assertion here claims recovery from a physical descriptor fault.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", action="store_true")
parser.add_argument("--stock", type=Path)
args = parser.parse_args()
drivers = root / "port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek"
relative = str((drivers / "rtl9617c-dma.c").relative_to(root))
source = subprocess.check_output(["git", "show", "c20354c:" + relative], cwd=root, text=True) if args.before else (root / relative).read_text()
header = (drivers / "rtl9617c-dma.h").read_text()


def function(text, name):
    start = re.search(r"^.*\b" + name + r"\([^;]*?\)\s*\{", text, re.M).start()
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + "\n"


if args.stock:
    assert hashlib.sha256(args.stock.read_bytes()).hexdigest() == "16840ba70afc0ae700c2d63c57b8248475a67154eef89536d90028d4fe6486d7", "instruction offsets require the identified TYFZA1 module"
    sys.path.insert(0, str(root / "tools"))
    from arm64_module_oracle import ModuleOracle
    from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn.arm64_const import (UC_ARM64_REG_X20, UC_ARM64_REG_X22,
        UC_ARM64_REG_X23, UC_ARM64_REG_X24, UC_ARM64_REG_X25,
        UC_ARM64_REG_X26, UC_ARM64_REG_X28, UC_ARM64_REG_PC)

    def boundary(name, values):
        raise AssertionError((name, values))

    oracle = ModuleOracle(args.stock,
        ["__rtk_ni_start_xmit", "__ca_ni_start_xmit_buf", "aal_ni_set_tx_write_ptr"],
        boundary, external_symbols={"cpu_number": 0x5007100, "vabits_actual": 0x5007200,
            "memstart_addr": 0x5007300, "kimage_voffset": 0x5007400})
    mmio, queues = 0x6000000, 0x7000000
    oracle.uc.mem_map(mmio, 0x1000)
    oracle.uc.mem_map(queues, 0x200000)
    oracle.word(oracle.symbols["g_dma_reg_iobase"][0], mmio)
    oracle.word(oracle.symbols["ca_ni_debug"][0], 0)
    events = []

    def instruction(uc, address, size, data):
        opcode = oracle.unpack(address, "I")[0]
        if opcode in (0xd5033abf, 0xd50332bf):
            events.append(("barrier", opcode))

    def write(uc, access, address, size, value, data):
        if mmio <= address < mmio + 0x1000:
            events.append(("write", address - mmio, size, value))

    oracle.uc.hook_add(UC_HOOK_CODE, instruction)
    oracle.uc.hook_add(UC_HOOK_MEM_WRITE, write)
    import struct
    # Start at the barrier after descriptor construction; stop after WP write.
    # The original instructions and relocated producer-write helper execute.
    for name, start, end, registers in [
        ("__rtk_ni_start_xmit", 0xa70dc, 0xa7104,
         {UC_ARM64_REG_X24: 8, UC_ARM64_REG_X28: 0, UC_ARM64_REG_X25: queues}),
        ("__ca_ni_start_xmit_buf", 0xabaac, 0xabae0,
         {UC_ARM64_REG_X23: 8, UC_ARM64_REG_X22: 0, UC_ARM64_REG_X20: queues,
          UC_ARM64_REG_X26: oracle.symbols["ca_ni_debug"][0]})]:
        base = oracle.functions[name] - (0xa6b90 if name == "__rtk_ni_start_xmit" else 0xab630)
        assert oracle.unpack(base + start, "I")[0] == 0xd5033abf
        for producer in (0, 1, 32, 63):
            oracle.uc.mem_write(queues + 8 * 0x14280 + 68, struct.pack("<H", producer))
            oracle.uc.mem_write(queues + 8 * 0x14280 + 76, struct.pack("<I", 0xf7001618))
            for register, value in registers.items():
                oracle.uc.reg_write(register, value)
            events.clear()
            oracle.uc.emu_start(base + start, base + end, count=100)
            assert oracle.uc.reg_read(UC_ARM64_REG_PC) == base + end
            assert events == [("barrier", 0xd5033abf), ("barrier", 0xd50332bf),
                              ("write", 0x618, 4, producer)], events
        print(f"stock {name}: four VP8 producers, barriers then WP only", flush=True)

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rtl9617c-wifi-format.h"
typedef uint32_t u32,__le32;typedef uint64_t u64;typedef uintptr_t dma_addr_t;typedef int netdev_tx_t;
#define __iomem
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define max(a,b) ((a)>(b)?(a):(b))
#define READ_ONCE(v) (v)
#define cpu_to_le32(v) (v)
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 1
#define ETH_HLEN 14
#define CHECKSUM_PARTIAL 1
#define VP_BASE 0x100
#define VP_STRIDE 0xa0
#define VP_WRITE 0x18
#define VP_READ 0x1c
#define VP_MISC_IRQ 0x68
struct net_device {bool stopped;struct {u64 tx_packets,tx_bytes;}stats;};
struct sk_buff {unsigned len,ip_summed;bool consumed,gso,vlan;unsigned char data[2048];};
struct rtl9617c_ni_frame {unsigned prefix_length,header_high,header_low;bool raw_payload;const void *prefix;};
static unsigned char regs[0x1000];
static unsigned barriers,writes,empty_acks,last_producer;
static u32 load(const void *p){u32 v;memcpy(&v,p,4);return v;}
static void put_unaligned_le32(u32 v,void *p){memcpy(p,&v,4);}
static u32 readl(void *p){return load(p);}
static void writel(u32 v,void *p){
 unsigned offset=(unsigned char *)p-regs;writes++;
 if(offset==0x618){assert(barriers==1);last_producer=v;put_unaligned_le32(v,p);
  /* A fresh completion can arrive between publication and a later ACK. */
  put_unaligned_le32(5,regs+0x668);
 }else{assert(offset==0x668 && v==1);empty_acks++;put_unaligned_le32(load(p)&~v,p);}
}
#define dma_wmb() (barriers++)
static int skb_copy_bits(struct sk_buff *s,unsigned off,void *p,unsigned n){assert(off+n<=s->len);memcpy(p,s->data+off,n);return 0;}
static void dev_consume_skb_any(struct sk_buff *s){assert(!s->consumed);s->consumed=true;}
static bool netif_queue_stopped(struct net_device *d){return d->stopped;}
static void netif_stop_queue(struct net_device *d){d->stopped=true;}
static void netif_wake_queue(struct net_device *d){d->stopped=false;}
static bool skb_is_gso(struct sk_buff *s){return s->gso;}
static bool skb_vlan_tag_present(struct sk_buff *s){return s->vlan;}
static u64 ktime_get_ns(void){return 0;}
'''
types = header[header.index("#define RTL_DMA_DEPTH"):header.index("/* HP=11")]
cases = r'''
int main(void){
 struct net_device dev={};struct rtl_dma_tx tx={.vp=8,.memory_dma=0x04000000,.registers=regs};
 const unsigned bytes=RTL_DMA_COUNT*(sizeof(*tx.descriptors)+RTL_DMA_BUFFER_SIZE);
 unsigned char *snapshot=malloc(bytes);tx.memory=calloc(1,bytes);assert(snapshot&&tx.memory);tx.descriptors=tx.memory;
 unsigned busy=0,submitted=0,wraps=0;unsigned char prefix[16];memset(prefix,0x5a,sizeof(prefix));
 for(unsigned n=0;n<4096;n++){
  /* Consume in bounded bursts, retaining the two fetched payloads. */
  if(n%3==0)put_unaligned_le32(tx.write,regs+0x61c);
  unsigned producer=tx.write,read=tx.read;
  memcpy(snapshot,tx.memory,bytes);barriers=writes=empty_acks=0;
  struct sk_buff skb={.len=n%2?14:1522};memset(skb.data,n&255,sizeof(skb.data));
  struct rtl9617c_ni_frame frame={.prefix=prefix,.prefix_length=16,.raw_payload=true};
  if(n%3==1)skb.len=1984; /* Maximum OMCI body plus MIC. */
  int ret=n%3==2?rtl_dma_tx_wifi(&tx,&dev,&skb,1+n%60):rtl_dma_tx_frame(&tx,&skb,&dev,n%3==1?&frame:NULL);
  assert(ret==0 && skb.consumed && last_producer==((producer+1)&63));
#ifdef EXPECT_BEFORE
  assert(writes==2 && empty_acks==1 && load(regs+0x668)==4);
#else
  assert(writes==1 && empty_acks==0 && load(regs+0x668)==5);
#endif
  assert(barriers==1);submitted++;wraps+=tx.write==0;
  for(unsigned i=read;i!=producer;i=(i+1)&63){
   assert(!memcmp(snapshot+i*8,tx.memory+i*8,8));
   unsigned offset=64*8+i*2048;assert(!memcmp(snapshot+offset,tx.memory+offset,2048));
  }
  unsigned offset=64*8+producer*2048,pad=n%3==1?32:n%3==2?0:16;
  assert(load(&tx.descriptors[producer].address)==tx.memory_dma+offset);
  unsigned length=n%3==1?skb.len+16:max(skb.len,60U);
  assert(load(&tx.descriptors[producer].control)==(n%3==2?
   rtl_wifi_lookup_control(1+n%60,length):(0x61000002U|((length+16)<<8))));
  assert(!memcmp(tx.memory+offset+pad,skb.data,skb.len));
  if(n%3==1)assert(!memcmp(tx.memory+offset+16,prefix,16));
 }
 /* Fill with frozen consumer, then verify BUSY neither publishes nor consumes. */
 while(rtl_dma_tx_available(&tx)){
  struct sk_buff skb={.len=64};barriers=writes=empty_acks=0;
  assert(rtl_dma_tx_frame(&tx,&skb,&dev,NULL)==NETDEV_TX_OK && skb.consumed);
 }
 for(unsigned i=0;i<8;i++){
  struct sk_buff skb={.len=64};memcpy(snapshot,tx.memory,bytes);barriers=writes=0;
  int ret=i%2?rtl_dma_tx_wifi(&tx,&dev,&skb,1):rtl_dma_tx_frame(&tx,&skb,&dev,NULL);
  assert(ret==(i%2?-EBUSY:NETDEV_TX_BUSY) && !skb.consumed && !writes && !barriers);
  assert(!memcmp(snapshot,tx.memory,bytes));busy++;
 }
 put_unaligned_le32(tx.write,regs+0x61c);rtl_dma_tx_complete(&tx,&dev);
 assert(((tx.write-tx.read)&63)==2 && !dev.stopped);
 printf("native: %u mixed LAN/OMCI/lookup submissions, %u wraps, %u BUSY checks; prefetch retained\n",submitted,wraps,busy);
#ifdef EXPECT_BEFORE
 puts("before: WP followed by EMPTY ACK; a newly raised EMPTY is cleared");
#else
 puts("after: stock WP-only publication; EMPTY and error status preserved");
#endif
 free(snapshot);free(tx.memory);
}
'''
data = common + types + function(header, "rtl_dma_tx_available")
data += "".join(function(source, name) for name in ("rtl_dma_vp", "rtl_dma_tx_complete", "rtl_dma_tx_frame", "rtl_dma_tx_wifi")) + cases
(root / "artifacts").mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix="ni-tx-publication-", dir=root / "artifacts") as directory:
    work = Path(directory)
    (work / "test.c").write_text(data)
    command = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g", "-fsanitize=address,undefined", "-I", str(drivers)]
    if args.before:
        command.append("-DEXPECT_BEFORE")
    subprocess.run(command + [str(work / "test.c"), "-o", str(work / "test")], check=True)
    subprocess.run([str(work / "test")], check=True)
