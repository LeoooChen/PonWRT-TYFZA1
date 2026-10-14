#!/usr/bin/env python3
"""Execute native Wi-Fi NI submission and compare its ABI with stock instructions."""
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
source = (drivers / 'rtl9617c-dma.c').read_text()
start = source.index('int rtl_dma_tx_wifi(')
end = source.index('\nssize_t rtl_dma_tx_state', start)
body = source[start:end]
common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "rtl9617c-wifi-format.h"
#include "rtl9617c-fe-format.h"
#define RTL_DMA_COUNT 64
#define RTL_DMA_BUFFER_SIZE 2048
#define ETH_HLEN 14
#define VP_WRITE 0x18
#define VP_MISC_IRQ 0x68
#define BIT(n) (1U << (n))
#define max(a,b) ((a)>(b)?(a):(b))
#define CHECKSUM_PARTIAL 3
#define cpu_to_le32(v) (v)
struct net_device {int unused;};
struct sk_buff {unsigned int len,ip_summed; bool gso,vlan,copy_error; unsigned char data[2048];};
struct descriptor {uint32_t address,control;};
struct rtl_dma_tx {
 bool profile;uint64_t wifi_packets,wifi_copy_bytes,wifi_prepare_ns;
 void *memory;unsigned int memory_dma,read,write;
 struct descriptor *descriptors;
 struct net_device *owners[64];
 unsigned int registers[32];
};
static unsigned int consumed,barriers,writes;
#define READ_ONCE(x) (x)
typedef uint64_t u64;
static uint64_t ktime_get_ns(void){return 100;}
static void rtl_dma_tx_complete(struct rtl_dma_tx *tx,struct net_device *dev){(void)tx;(void)dev;}
static bool rtl_dma_tx_available(struct rtl_dma_tx *tx){return ((tx->read-tx->write-1)&63)>2;}
static bool skb_is_gso(struct sk_buff *skb){return skb->gso;}
static bool skb_vlan_tag_present(struct sk_buff *skb){return skb->vlan;}
static int skb_copy_bits(struct sk_buff *skb,unsigned int off,void *dest,unsigned int len){
 if(skb->copy_error)return -EFAULT;
 assert(off+len<=skb->len);memcpy(dest,skb->data+off,len);return 0;
}
static void *rtl_dma_vp(struct rtl_dma_tx *tx){return tx->registers;}
static void dma_wmb(void){barriers++;}
static void writel(unsigned int value,void *reg){assert(barriers>writes/2);memcpy(reg,&value,4);writes++;}
static void dev_consume_skb_any(struct sk_buff *skb){(void)skb;consumed++;}
'''
test = r'''
int main(void){
 unsigned char memory[64*(8+2048)];memset(memory,0xa5,sizeof(memory));
 struct rtl_dma_tx tx={.memory=memory,.memory_dma=0x03000000,.descriptors=(void *)memory};
 struct net_device dev={};struct sk_buff skb={.len=42};
 for(unsigned int i=0;i<sizeof(skb.data);i++)skb.data[i]=(i*17)^0x5a;
 for(unsigned int id=1;id<=60;id++)for(unsigned int len=14;len<=2048;len+=17){
  unsigned int slot=tx.write,off=512+slot*2048,n=len<60?60:len;
  tx.read=tx.write;skb.len=len;tx.owners[slot]=&dev;
  assert(!rtl_dma_tx_wifi(&tx,&dev,&skb,id));
  assert(tx.write==((slot+1)&63) && !tx.owners[slot]);
  assert(tx.descriptors[slot].address==tx.memory_dma+off);
  assert(tx.descriptors[slot].control==(0xa5000001U|(n<<8)|(id<<1)));
  assert(!memcmp(memory+off,skb.data,len));
  for(unsigned int p=len;p<n;p++)assert(memory[off+p]==0);
 }
 unsigned int old=tx.write,c=consumed,w=writes;
 skb.len=60;tx.read=(tx.write+3)&63;
 assert(rtl_dma_tx_wifi(&tx,&dev,&skb,1)==-EBUSY);
 tx.read=tx.write;
 assert(rtl_dma_tx_wifi(&tx,&dev,&skb,0)==-EINVAL);
 assert(rtl_dma_tx_wifi(&tx,&dev,&skb,61)==-EINVAL);
 skb.len=13;assert(rtl_dma_tx_wifi(&tx,&dev,&skb,1)==-EINVAL);
 skb.len=2049;assert(rtl_dma_tx_wifi(&tx,&dev,&skb,1)==-EINVAL);
 skb.len=60;skb.gso=true;assert(rtl_dma_tx_wifi(&tx,&dev,&skb,1)==-EOPNOTSUPP);skb.gso=false;
 skb.vlan=true;assert(rtl_dma_tx_wifi(&tx,&dev,&skb,1)==-EOPNOTSUPP);skb.vlan=false;
 skb.ip_summed=3;assert(rtl_dma_tx_wifi(&tx,&dev,&skb,1)==-EOPNOTSUPP);skb.ip_summed=0;
 skb.copy_error=true;assert(rtl_dma_tx_wifi(&tx,&dev,&skb,1)==-EFAULT);
 assert(tx.write==old && consumed==c && writes==w);
 unsigned int mask[8],original[8],key[23]={};
 rtl_fe_ipv6_mask(mask);memcpy(original,mask,sizeof(mask));rtl_fe_wifi_mask(mask);
 for(unsigned int bit=0;bit<256;bit++){
  unsigned int value=(mask[bit/32]>>(bit%32))&1;
  assert(value==((bit>=135 && bit<143)?0:((original[bit/32]>>(bit%32))&1)));
 }
 rtl_fe_key_set(key,RTL_FE_KEY_MDATA_BYTE,60);
 for(unsigned int i=0;i<23;i++)assert(key[i]==(i==19?(60U<<6):0));
 for(unsigned int id=0;id<64;id++)for(unsigned int size=0;size<16384;size++){
  unsigned int word=0xabc00000U|(id<<14)|size;
  assert(rtl_wifi_epp_id(word)==id);
  int want=(id>=1 && id<=60 && size>=30 && size<=2016)?(int)size-16:-1;
  assert(rtl_wifi_epp_length(word,2000)==want);
 }
 printf("PASS: %u DMA submissions, ring wrap/padding/ownership/backpressure; 1048576 EPP cases\n",consumed);
}
'''
record = {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
          'format_sha256': hashlib.sha256((drivers / 'rtl9617c-wifi-format.h').read_bytes()).hexdigest(),
          'epp_cases': 64 * 16384, 'sanitizers': ['ASan', 'UBSan'],
          'hardware_access': False, 'stock_instruction_cases': 0}
with tempfile.TemporaryDirectory(prefix='wifi-ni-', dir=root / 'artifacts') as work:
    path = Path(work)
    (path / 'test.c').write_text(common + body + test)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined',
                    '-I', str(drivers), str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
    if platform.system() == 'Linux' and platform.machine() == 'aarch64':
        tool = root / 'evidence/wireless-study-20261010-continuation/tools/offline_instruction_tests.py'
        spec = importlib.util.spec_from_file_location('stock_fragments', tool)
        stock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stock)
        elf = (root / 'analysis/private/live-files/lib/modules/5.10.70/extra/ca-ne.ko').read_bytes()
        assert hashlib.sha256(elf).hexdigest() == stock.EXPECTED
        fragment = stock.section(elf, '.nictrx-sec')[0x2778:0x2804]
        assembly = '''.text
.global test_desc
.type test_desc, %function
test_desc:
 stp x19, x20, [sp, #-48]!
 stp x21, x25, [sp, #16]
 stp x28, x30, [sp, #32]
 mov x20, x0
 mov w6, w1
 mov x19, xzr
 mov x21, xzr
 mov x25, xzr
 mov x28, x2
 mov w4, wzr
''' + stock.bytes_asm([fragment]) + '''
 ldp x21, x25, [sp, #16]
 ldp x28, x30, [sp, #32]
 ldp x19, x20, [sp], #48
 ret
.section .note.GNU-stack,"",@progbits
'''
        (path / 'stock.S').write_text(assembly)
        (path / 'abi.c').write_text('#include "rtl9617c-wifi-format.h"\n'
                                  'unsigned int native(unsigned int id,unsigned int len)'
                                  '{return rtl_wifi_lookup_control(id,len);}\n')
        subprocess.run(['cc', '-shared', '-fPIC', '-I', str(drivers), str(path / 'abi.c'),
                        str(path / 'stock.S'), '-o', str(path / 'abi.so')], check=True)
        lib = ctypes.CDLL(str(path / 'abi.so'))
        lib.test_desc.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
        lib.test_desc.restype = lib.native.restype = ctypes.c_uint
        scratch = ctypes.create_string_buffer(256)
        for id in range(1, 61):
            cfg = bytearray(16)
            struct.pack_into('<H', cfg, 0, id)
            cfg[9], cfg[11], cfg[12] = 25, 18, 18
            for length in (60, 64, 1500, 1518, 2048):
                original = lib.test_desc(ctypes.create_string_buffer(bytes(cfg)), length, scratch)
                assert original == lib.native(id, length)
                record['stock_instruction_cases'] += 1
        record['stock_module_sha256'] = stock.EXPECTED
        record['stock_fragment_sha256'] = hashlib.sha256(fragment).hexdigest()
(root / 'logs/wifi-ni-contract.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
