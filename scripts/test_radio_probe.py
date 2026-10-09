#!/usr/bin/env python3
"""Run the production PCI probe with original firmware data and modeled MMIO."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
native = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
text = (native / 'pci.c').read_text()
study = root / json.loads((root / 'manifests/wireless-study-current.json').read_text())['snapshot']
images = json.loads((study / 'evidence/derived/stock-wifi-firmware-manifest.json').read_text())['firmwares']

def function(name):
    start = re.search(r'^static [^\n]+ ' + name + r'\([^;{]*\)\s*\{', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fw.h"
#include "power.h"
#include "download.h"
typedef uint32_t u32;
#define __iomem
#define GFP_KERNEL 0
#define DMA_BIT_MASK(n) ((1ULL<<(n))-1)
#define IORESOURCE_MEM 1
#define __ffs(n) __builtin_ctz(n)
#define IS_ERR(p) ((uintptr_t)(p)>(uintptr_t)-4096)
#define PTR_ERR(p) ((long)(p))
struct device {};
struct pci_dev {struct device dev;void *data;};
struct pci_device_id {};
struct firmware {const unsigned char *data;size_t size;};
static unsigned char memory[65536],efuse[1536];
static int fault,fw_requests,fw_releases,off_requests;
static void *allocation;
static unsigned int selected_bar;
static const char *requested;
enum {ENABLE=1,MASK,ALLOC,BARS,BAR_LENGTH,MAP,POWER,EFUSE_READ,MISSING_FW,TRUNCATED_FW,WRONG_FW,WDE_TIMEOUT,PLE_TIMEOUT};
static int pcim_enable_device(struct pci_dev *d){return fault==ENABLE?-EIO:0;}
static void pci_clear_master(struct pci_dev *d){}
static int dma_set_mask_and_coherent(struct device *d,unsigned long long mask){
 assert(mask==0xffffffffULL);return fault==MASK?-EIO:0;
}
static void *devm_kzalloc(struct device *d,size_t n,int flags){
 if(fault==ALLOC)return NULL;allocation=calloc(1,n);return allocation;
}
static int pci_select_bars(struct pci_dev *d,int type){assert(type==IORESOURCE_MEM);return fault==BARS?0:4;}
static unsigned long pci_resource_len(struct pci_dev *d,int bar){assert(bar==2);return fault==BAR_LENGTH?128:sizeof(memory);}
static void *pcim_iomap_region(struct pci_dev *d,int bar,const char *name){
 selected_bar=bar;assert(bar==2);return fault==MAP?(void *)(intptr_t)-EBUSY:memory;
}
static void pci_set_drvdata(struct pci_dev *d,void *p){d->data=p;}
static void *pci_get_drvdata(struct pci_dev *d){return d->data;}
static unsigned char readb(void *p){return *(unsigned char *)p;}
static unsigned short readw(void *p){unsigned short n;memcpy(&n,p,2);return n;}
static unsigned int readl(void *p){
 unsigned int n;memcpy(&n,p,4);size_t reg=(unsigned char *)p-memory;
 if(reg==RTL_AX_POWER){n&=~(RTL_AX_BIT(8)|RTL_AX_BIT(9));if(fault==POWER)n&=~RTL_AX_BIT(17);}
 if(reg==RTL_AX_XTAL)n&=~RTL_AX_BIT(31);
 if(reg==0x8d00)n=fault==WDE_TIMEOUT?0:3;
 if(reg==0x9100)n=fault==PLE_TIMEOUT?0:3;
 if(reg==RTL_AX_EFUSE){unsigned int address=(n>>16)&0x7ff;assert(address<sizeof(efuse));
  n=(n&~255U)|efuse[address];if(fault!=EFUSE_READ)n|=RTL_AX_BIT(29);}
 return n;
}
static void writeb(unsigned char n,void *p){memcpy(p,&n,1);}
static void writew(unsigned short n,void *p){memcpy(p,&n,2);}
static void writel(unsigned int n,void *p){
 if((unsigned char *)p==memory+RTL_AX_POWER && (n&RTL_AX_BIT(9)))off_requests++;
 memcpy(p,&n,4);
}
#define readl_poll_timeout(addr,val,cond,delay,timeout) ({val=readl(addr);cond?0:-ETIMEDOUT;})
#define fsleep(us) ((void)0)
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define dev_err_probe(dev,error,...) (error)
static int request_firmware(const struct firmware **fw,const char *name,struct device *d);
static void release_firmware(const struct firmware *fw){fw_releases++;}
'''
start = text.index('struct rtl_ax_ap {')
common += text[start:text.index('};', start) + 3]
for name in ('rtl_ax_read8', 'rtl_ax_read16', 'rtl_ax_read32',
             'rtl_ax_write8', 'rtl_ax_write16', 'rtl_ax_write32', 'rtl_ax_delay_us',
             'rtl_ax_poll32', 'rtl_ax_stop', 'rtl_ax_probe', 'rtl_ax_remove', 'rtl_ax_shutdown'):
    common += function(name)
assembly = []
for index, name in enumerate(('array_8852c_u1_ap', 'array_8852c_u2_ap', 'array_8192xb_u1_ap', 'array_8192xb_u2_ap')):
    path = study / images[name]['extracted_path']
    common += f'extern const unsigned char firmware_{index}[],firmware_{index}_end[];\n'
    assembly.append(f'.section .rodata\n.balign 4\n.global firmware_{index},firmware_{index}_end\nfirmware_{index}:\n.incbin "{path}"\nfirmware_{index}_end:\n')
unit = r'''
static int request_firmware(const struct firmware **fw,const char *name,struct device *d){
 static struct firmware data;fw_requests++;requested=name;
 struct pci_dev *pdev=(struct pci_dev *)d;struct rtl_ax_ap *radio=pdev->data;
 /* This detects selection before efuse identity has updated the cut. */
 assert(radio->powered && radio->identity.adie==0x1234);
 assert((readl(memory+RTL_AX_DMAC)&0x7fff8000)==0x7fff8000);
 if(fault==MISSING_FW)return -ENOENT;
 unsigned int i=(radio->chip==RTL_AX_8192XB?2:0)+(radio->identity.cv?1:0);
 const unsigned char *start[]={firmware_0,firmware_1,firmware_2,firmware_3};
 const unsigned char *end[]={firmware_0_end,firmware_1_end,firmware_2_end,firmware_3_end};
 if(fault==WRONG_FW)i^=2;
 data=(struct firmware){start[i],(size_t)(end[i]-start[i])-(fault==TRUNCATED_FW)};
 *fw=&data;return 0;
}
static void prepare(unsigned int chip,unsigned int cv,unsigned int factory){
 memset(memory,0,sizeof(memory));memset(efuse,255,sizeof(efuse));
 unsigned int power=RTL_AX_BIT(17);memcpy(memory+RTL_AX_POWER,&power,4);
 memory[RTL_AX_CHIP]=chip;memory[RTL_AX_CONFIG+1]=cv<<4;
 efuse[0x5e9]=0xaa;efuse[0x5ea]=factory;efuse[0x5f0]=0x34;efuse[0x5f1]=0x12;
 fw_requests=fw_releases=off_requests=0;selected_bar=0;requested=NULL;allocation=NULL;
}
static void finish(void){free(allocation);allocation=NULL;}
int main(void){
 unsigned int cases=0;
 for(unsigned int chip=RTL_AX_8852C;chip<=RTL_AX_8192XB;chip++)
  for(unsigned int raw_cv=0;raw_cv<2;raw_cv++)for(unsigned int variant=0;variant<4;variant++){
   unsigned int factory=variant==0?255:variant==1?15:variant==2?31:0;
   fault=0;prepare(chip,raw_cv,factory);struct pci_dev device={};
   assert(!rtl_ax_probe(&device,NULL));struct rtl_ax_ap *radio=device.data;
   unsigned int cv=factory==255 || !factory?raw_cv:factory>>4;
   assert(radio->identity.cv==cv && selected_bar==2 && fw_requests==1 && fw_releases==1);
   assert(radio->firmware.cut==(cv?2:1));
   assert(radio->download_prepared);
   assert((readl(memory+RTL_AX_HAXI_CONFIG)&(RTL_AX_BIT(7)|RTL_AX_BIT(15)|RTL_AX_BIT(17)))==RTL_AX_BIT(17));
   assert((readl(memory+RTL_AX_HAXI_STOP1)&0xfff00)==0xfff00);
   assert(!strcmp(requested,rtl_ax_ap_name(chip==RTL_AX_8852C?2:3,cv)));
   rtl_ax_shutdown(&device);assert(!radio->powered && off_requests==1);
   assert(!radio->download_prepared && !(readl(memory+RTL_AX_DMAC)&RTL_AX_DLE_ENABLE));
   rtl_ax_remove(&device);assert(off_requests==1);finish();cases++;
  }
 for(int f=ENABLE;f<=PLE_TIMEOUT;f++){
  fault=f;prepare(RTL_AX_8192XB,0,31);struct pci_dev device={};
  int ret=rtl_ax_probe(&device,NULL);assert(ret<0);
  if(f==EFUSE_READ || f==MISSING_FW || f==TRUNCATED_FW || f==WRONG_FW || f==WDE_TIMEOUT || f==PLE_TIMEOUT){
   assert(off_requests==1);struct rtl_ax_ap *radio=device.data;assert(!radio->powered);
  }
  if(f==WDE_TIMEOUT || f==PLE_TIMEOUT){
   assert(!(readl(memory+RTL_AX_DMAC)&RTL_AX_DLE_ENABLE));
   assert((readl(memory+RTL_AX_HAXI_CONFIG)&(RTL_AX_BIT(7)|RTL_AX_BIT(15)|RTL_AX_BIT(17)))==RTL_AX_BIT(17));
  }
  if(f<=MAP || f==POWER || f==EFUSE_READ)assert(!fw_requests);
  finish();cases++;
 }
 fault=0;prepare(0x50,0,255);struct pci_dev device={};
 assert(rtl_ax_probe(&device,NULL)==-ENODEV && !fw_requests && !off_requests);finish();cases++;
 printf("Native PCI probe:%u production-function cases;actual four AP images,CV override before selection,32bit DMA,first memory BAR,and failure/removal power cleanup passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='radio-probe-') as directory:
    path = Path(directory)
    source, data, binary = path / 'probe.c', path / 'firmwares.S', path / 'probe'
    source.write_text(common + unit)
    data.write_text(''.join(assembly) + '.section .note.GNU-stack,"",@progbits\n')
    subprocess.run(['cc', '-g', '-fsanitize=address,undefined', '-I' + str(native),
                    str(source), str(data), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/radio-probe-test.json').write_text(json.dumps({
    'native_sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (native / 'pci.c', native / 'power.h', native / 'download.h', native / 'fw.h')},
    'production_probe_cases': 30, 'original_firmware_images': 4,
    'DLE_ready_and_HCI_paused_without_host_DMA_rings': True,
    'WDE_PLE_timeouts_leave_DLE_disabled_and_HCI_paused': True,
    'efuse_cv_override_before_firmware_selection': True,
    'DMA_mask_bits': 32, 'first_memory_BAR_test_index': 2,
    'missing_wrong_and_truncated_firmware_release_power': True,
    'remove_after_shutdown_does_not_repeat_completed_power_off': True,
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
