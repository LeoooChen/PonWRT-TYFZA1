#!/usr/bin/env python3
"""Check production scatter DMA ownership and WP bytes against stock encoders."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
sys.path.insert(0, str(root / 'tools'))
from arm64_module_oracle import ModuleOracle


def function(name):
    text = (driver / 'tx.c').read_text()
    start = text.rfind('\n', 0, text.index(name + '(')) + 1
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


header = '\n'.join(line for line in (driver / 'tx-format.h').read_text().splitlines()
                   if not line.startswith('#include'))
common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
typedef unsigned long dma_addr_t;
#define min(a,b) ((a)<(b)?(a):(b))
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
#define GFP_ATOMIC 0
#define DMA_TO_DEVICE 1
static void rtl_ax_store32(u8 *p,u32 v){memcpy(p,&v,4);}
static u32 rtl_ax_ap_read32(const u8 *p){u32 v;memcpy(&v,p,4);return v;}
'''
common += header + '\n'
mapping = r'''
struct scatterlist {unsigned int length;dma_addr_t dma;};
struct sk_buff {bool nonlinear;unsigned int len,count,lengths[11];u8 *data;};
struct pci_dev {int dev;};
struct rtl_ax_tx {struct pci_dev *pdev;int *dma_device;};
struct rtl_ax_tx_mapping {struct scatterlist sg[RTL_AX_TX_WP_ADDRESS_MAX];unsigned int count,dma_count;};
struct rtl_ax_tx_slot {dma_addr_t dma;unsigned int bytes;struct rtl_ax_tx_mapping *mapping;};
static unsigned int allocated,mapped,linearized,unmapped_count;
static bool fail_alloc,fail_map,merge;
static struct scatterlist *active_sg;
static void *kmalloc(size_t n,int flags){(void)flags;if(fail_alloc)return NULL;allocated++;return malloc(n);}
static void kfree(void *p){assert(allocated);allocated--;free(p);}
static bool skb_is_nonlinear(struct sk_buff *s){return s->nonlinear;}
static void sg_init_table(struct scatterlist *s,unsigned int n){memset(s,0,n*sizeof(*s));}
static int skb_to_sgvec(struct sk_buff *s,struct scatterlist *sg,int offset,unsigned int len){
 assert(!offset && len==s->len);if(s->count>10)return -EMSGSIZE;
 for(unsigned int i=0;i<s->count;i++)sg[i].length=s->lengths[i];return s->count;
}
static unsigned int dma_map_sg(int *dev,struct scatterlist *sg,unsigned int n,int direction){
 (void)dev;assert(direction==DMA_TO_DEVICE);if(fail_map)return 0;
 assert(!mapped);active_sg=sg;mapped++;
 if(merge){for(unsigned int i=1;i<n;i++)sg[0].length+=sg[i].length;n=1;}
 for(unsigned int i=0;i<n;i++)sg[i].dma=0x100000+i*0x10000;return n;
}
static void dma_unmap_sg(int *dev,struct scatterlist *sg,unsigned int n,int direction){
 (void)dev;assert(direction==DMA_TO_DEVICE && mapped==1 && active_sg==sg);
 mapped--;unmapped_count=n;active_sg=NULL;
}
#define for_each_sg(list,sg,n,i) for((i)=0;(i)<(n) && ((sg)=&(list)[i],1);(i)++)
#define sg_dma_len(sg) ((sg)->length)
static int skb_linearize(struct sk_buff *s){s->nonlinear=false;linearized++;return 0;}
static dma_addr_t dma_map_single(int *dev,u8 *p,unsigned int n,int direction){
 (void)dev;(void)p;assert(n && direction==DMA_TO_DEVICE);if(fail_map)return 0;assert(!mapped);mapped++;return 0x900000;
}
static bool dma_mapping_error(int *dev,dma_addr_t dma){(void)dev;return !dma;}
static void dma_unmap_single(int *dev,dma_addr_t dma,unsigned int n,int direction){
 (void)dev;assert(dma==0x900000 && n && direction==DMA_TO_DEVICE && mapped==1);mapped--;
}
'''
mapping += function('rtl_ax_tx_unmap') + function('rtl_ax_tx_map')
mapping += r'''
int main(void){
 struct pci_dev pci={};struct rtl_ax_tx tx={.pdev=&pci,.dma_device=&pci.dev};u8 data[16384]={};
 for(unsigned int scenario=0;scenario<7;scenario++){
  struct sk_buff skb={.nonlinear=scenario!=0,.len=6000,.count=4,.lengths={1500,1500,1500,1500},.data=data};
  struct rtl_ax_tx_slot slot={.bytes=skb.len};bool compact=false;
  merge=scenario==2;fail_alloc=scenario==5;fail_map=scenario==6;
  if(scenario==3){skb.count=10;for(unsigned int i=0;i<10;i++)skb.lengths[i]=i<5?2048:100;skb.len=10740;slot.bytes=skb.len;}
  if(scenario==4)skb.count=11;
  int result=rtl_ax_tx_map(&tx,&skb,&slot,&compact,false);
  if(scenario>=5){assert(result==-ENOMEM && !allocated && !mapped);continue;}
  assert(result==(scenario==1?4:scenario==3?6:3));
  assert(compact==(scenario==3 || scenario==4));
  if(slot.mapping){assert(slot.mapping->count==4 && slot.mapping->dma_count==(merge?1:4));}
  /* The RPQ consumer copies ownership before a producer can reuse the slot. */
  struct rtl_ax_tx_slot completed=slot;memset(&slot,0,sizeof(slot));
  rtl_ax_tx_unmap(&tx,&completed);assert(!allocated && !mapped);
  if(scenario==1 || scenario==2)assert(unmapped_count==4);
 }
 assert(linearized==2);puts("Scatter DMA ownership: original vs merged nents, WP overflow compaction, failed mapping and RPQ slot reuse passed");
 fail_alloc=false;fail_map=false;merge=false;
 for(unsigned count=9;count<=10;count++){
  struct sk_buff skb={.nonlinear=true,.len=count*100,.count=count,.data=data};
  for(unsigned i=0;i<count;i++)skb.lengths[i]=100;
  struct rtl_ax_tx_slot slot={.bytes=skb.len};bool compact=false;
  assert(rtl_ax_tx_map(&tx,&skb,&slot,&compact,true)==(count==9?9:1));
  assert(compact==(count==10));rtl_ax_tx_unmap(&tx,&slot);
  assert(!mapped && !allocated);
 }
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-scatter-') as directory:
    work = Path(directory)
    source, binary = work / 'ownership.c', work / 'ownership'
    source.write_text(common + mapping)
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    source.write_text(common + r'''
int main(int argc,char **argv){u8 page[128];unsigned int entries=0;memset(page,0xa5,sizeof(page));
 for(int i=1;i<argc;i++)entries+=rtl_ax_tx_wp_addresses(page+64+entries*6,0x100000+i*0x10000,atoi(argv[i]),i==argc-1);
 assert(entries<=10);for(unsigned int i=64+entries*6;i<128;i++)assert(page[i]==0xa5);
 fwrite(page+64,6,entries,stdout);return 0;}
''')
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', str(source), '-o', str(binary)], check=True)
    families = ('8852ce', '8192xbe')
    names = ['_hal_fill_wp_addr_info_' + chip for chip in families]
    oracle = ModuleOracle(root / 'evidence/wireless-study-20261009-104746/evidence/stock/modules/rtk_wifi6.ko', names, lambda n, a: 0)
    vectors = 0
    for lengths in ((1,), (2047,), (2048,), (4094,), (11454,), (1500, 1504, 1504, 1504), (40, 2047, 2048, 4096)):
        actual = subprocess.check_output([str(binary), *map(str, lengths)])
        for chip in families:
            expected = bytearray()
            for segment, length in enumerate(lengths, 1):
                address = 0x100000 + segment * 0x10000
                while length:
                    fragment = min(length, 2047)
                    oracle.uc.mem_write(oracle.scratch, bytes(32))
                    oracle.uc.mem_write(oracle.scratch + 8, struct.pack('<I', address))
                    oracle.uc.mem_write(oracle.scratch + 16, struct.pack('<H', fragment))
                    oracle.uc.mem_write(oracle.scratch + 64, bytes(6))
                    oracle.call('_hal_fill_wp_addr_info_' + chip, 0, oracle.scratch + 64, oracle.scratch, 0, 0, 0, int(segment == len(lengths) and length == fragment))
                    expected.extend(oracle.uc.mem_read(oracle.scratch + 64, 6))
                    address += fragment
                    length -= fragment
            assert actual == expected, (chip, lengths)
            vectors += 1
    (root / 'logs/radio-scatter-dma-test.json').write_text(json.dumps({'stock_WP_vectors': vectors, 'production_DMA_ownership_scenarios': 7, 'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False}, indent=2) + '\n')
    print(f'Scatter WP: {vectors} original C/XB multi-address vectors match, including 2047-byte boundary and final-segment flag')
