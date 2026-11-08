#!/usr/bin/env python3
"""Check real TXBD programming against both DT inbound and SoC ATU mappings."""
from pathlib import Path
import subprocess,tempfile,re
root=Path(__file__).resolve().parents[1]
base=root/'port/overlay/target/linux/rtl9617c/files-6.18'
header=base/'drivers/pci/controller/dwc/rtl9617c-pcie-sequence.h'
receive=(base/'drivers/net/wireless/realtek/rtl_ax_ap/receive.h').read_text()
source=(base/'drivers/net/wireless/realtek/rtl_ax_ap/tx.c').read_text()
start=source.index('\t\tio->write32(io->ctx, 0x1230 + channel * 8,')
end=source.index(';',start)+1
write=source[start:end]
body='struct tx_type { unsigned int ring_dma; };\nstatic void program_tx(const struct rtl_ax_io *io,unsigned int ring){struct tx_type a={.ring_dma=0x075f1000},*tx=&a;unsigned int channel=ring<4?ring:ring+4;\n'+write+'\n}\n'
dts=(root/'port/overlay/target/linux/rtl9617c/dts/rtl9617c-tyfza1-pcie.dts').read_text()
ranges=[]
for prop in re.findall(r'dma-ranges\s*=\s*(.*?);',dts,re.S):
 entries=[]
 for group in re.findall(r'<([^>]+)>',prop):
  c=[int(v,0) for v in group.split()]
  entries.append(((c[1]<<32)|c[2],(c[3]<<32)|c[4],(c[5]<<32)|c[6]))
 ranges.append(entries)
assert len(ranges)==2 and ranges[0]==ranges[1], 'Exercise both radio host mappings'
dw_table='static const unsigned long long dw_ranges[][3] = {'+','.join('{'+','.join(hex(v)+'ULL' for v in row)+'}' for row in ranges[0])+'};\n'
source=dw_table+r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "rtl9617c-pcie-sequence.h"
#define RTL_AX_BIT(n) (1U<<(n))
#define RTL_AX_HAXI_CONFIG 0x1000
#define RTL_AX_RX_ENTRIES 256
#define RTL_AX_TX_ENTRIES 256
#define lower_32_bits(v) ((unsigned int)(v))
struct rtl_ax_io {
 void *ctx;
 unsigned int (*read32)(void *,unsigned int);
 void (*write32)(void *,unsigned int,unsigned int);
 int (*poll32)(void *,unsigned int,unsigned int,unsigned int,unsigned int,unsigned int,unsigned int *);
};
static unsigned int staging[7], table[8][6], enabled, regs[0x2000/4];
static unsigned int atu_read(void *ctx,unsigned int reg) {(void)ctx;return staging[reg/4];}
static void atu_write(void *ctx,unsigned int reg,unsigned int val) {
 (void)ctx;staging[reg/4]=val;
 if (!reg && (val&(1U<<31))) {
  unsigned int entry=(val>>24)&7;enabled=val&255;
  for(unsigned int i=0;i<6;i++)table[entry][i]=staging[i+1];
 }
}
static int atu_wait(void *ctx,unsigned int reg,unsigned int mask) {
 (void)ctx;assert(!reg && mask==(1U<<31));staging[0]&=~mask;return 0;
}
static unsigned int read32(void *ctx,unsigned int reg) {(void)ctx;return regs[reg/4];}
static void write32(void *ctx,unsigned int reg,unsigned int val) {(void)ctx;regs[reg/4]=val;}
static int poll32(void *ctx,unsigned int reg,unsigned int mask,unsigned int want,unsigned int step,unsigned int timeout,unsigned int *last) {
 (void)ctx;(void)step;(void)timeout;regs[reg/4]=(regs[reg/4]&~mask)|want;*last=regs[reg/4];return 0;
}
static void __attribute__((unused)) rtl_ax_hci_pause(const struct rtl_ax_io *io) {(void)io;}
static void __attribute__((unused)) rtl_ax_update16(const struct rtl_ax_io *io,unsigned int reg,unsigned int clear,unsigned int set) {(void)io;(void)reg;(void)clear;(void)set;}
static void __attribute__((unused)) rtl_ax_update32(const struct rtl_ax_io *io,unsigned int reg,unsigned int clear,unsigned int set) {io->write32(io->ctx,reg,(io->read32(io->ctx,reg)&~clear)|set);}
static uint64_t translate(unsigned int bus,unsigned int *attributes,unsigned int *read_user) {
 unsigned int mapped=0;
 for(unsigned int i=0;i<sizeof(dw_ranges)/sizeof(dw_ranges[0]);i++)
  if(bus>=dw_ranges[i][0] && bus-dw_ranges[i][0]<dw_ranges[i][2]) {
   bus=bus-dw_ranges[i][0]+dw_ranges[i][1];mapped=1;break;
  }
 assert(mapped && "PCIe alias missing from DT inbound dma-ranges");
 unsigned int page=bus>>16;
 for(unsigned int i=0;i<8;i++) if ((enabled&(1U<<i)) && ((page^table[i][0])&table[i][1])==0) {
  *attributes=table[i][3];*read_user=table[i][4];
  return ((uint64_t)((page&~table[i][1])|table[i][2])<<16)|(bus&65535);
 }
 assert(!"unmapped PCIe bus address");return 0;
}
''' + body + r'''
int main(void) {
 struct rtl_pcie_io atu={0,atu_read,atu_write,0,atu_wait};
 struct rtl_ax_io radio={0,read32,write32,poll32};
 assert(!rtl_pcie_atu_ddr(&atu,0x2000,4));
 for(unsigned q=0;q<6;q++)program_tx(&radio,q);
 unsigned int attr,user;
 for(unsigned int q=0;q<6;q++) {
  unsigned int physical=0x075f1000+q*256*8;
  uint64_t address=translate(regs[(0x1230+(q<4?q:q+4)*8)/4],&attr,&user);
  assert(address==physical && user==0 && attr==0x07000022);
  /* DDR has a newly published pointer; an ARM cached alias retains the old one. */
  unsigned int ddr=0x06d68040,arm_cache=0x07588040;
  unsigned int device_read=(address>>32)==4?arm_cache:ddr;
  assert(device_read==ddr);
 }
 /* Packet buffers continue to use CCI, including its WriteUnique attributes. */
 for(unsigned int a=0;a<0x20000000;a+=0x10000) {
  assert(translate(a,&attr,&user)==0x400000000ULL+a);
  assert(attr==0x07ff0022 && user==0x80);
  assert(translate(a|0x80000000U,&attr,&user)==a);
  assert(attr==0x07000022 && user==0);
 }
 puts("PE-owned TXBDs: actual host/ATU setup bypasses stale ARM cache; all512MiB payload addresses retain CCI");
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(source)
 subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(header.parent),str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
