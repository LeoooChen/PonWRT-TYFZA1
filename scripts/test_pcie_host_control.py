#!/usr/bin/env python3
"""Exercise actual host startup/MSI/ATU waits using Linux's polling macros."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/pci/controller/dwc'
source = (drivers / 'pcie-rtl9617c.c').read_text()
kernel = Path(subprocess.check_output(['python3', str(root / 'port/tools/project_paths.py'),
                                     '--kernel-directory', str(root / 'sources/openwrt')], text=True).strip())
polling = (kernel / 'include/linux/iopoll.h').read_text()
designware = (kernel / 'drivers/pci/controller/dwc/pcie-designware-host.c').read_text()
assert designware.index('pp->ops->msi_init(pp)') < designware.index('pci_host_probe(bridge)')
assert '.post_init = rtl_pcie_post_init' not in source

def function(name):
    start = re.search(r'^static .*\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

def macro(name):
    lines = polling.splitlines(True)
    start = next(i for i, line in enumerate(lines) if re.match(r'#define ' + name + r'\(', line))
    end = start
    while lines[end].rstrip().endswith('\\'):
        end += 1
    return ''.join(lines[start:end + 1])

common = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include "rtl9617c-pcie-sequence.h"
typedef uint32_t u32;typedef uint64_t u64,ktime_t;
typedef pthread_mutex_t raw_spinlock_t;
#define __iomem
#define BIT(n) (1U<<(n))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define GLB_IRQ_STATUS 0
#define GLB_IRQ_ENABLE 4
#define GLB_MSI_ENABLE 0x1a4
#define GLB_MSI_IRQ BIT(8)
struct device {};
struct dw_pcie_rp {bool has_msi_ctrl;int msi_irq[8];unsigned int num_vectors;};
struct dw_pcie {struct device *dev;struct dw_pcie_rp pp;};
#define to_dw_pcie_from_pp(p) container_of(p,struct dw_pcie,pp)
struct clk {};struct reset_control {};struct gpio_desc {};struct phy {};struct irq_domain {};
struct platform_device {};
struct resource {uint64_t start,end;};
static struct resource dbi={0xa0000000,0xa0000fff},atu={0xa0300000,0xa03fffff};
static struct platform_device pdev;
static struct platform_device *to_platform_device(struct device *d){(void)d;return &pdev;}
static struct resource *platform_get_resource_byname(struct platform_device *d,int type,const char *name){
 (void)d;(void)type;return !strcmp(name,"dbi")?&dbi:&atu;
}
#define IORESOURCE_MEM 0
static struct {uint64_t time;u32 *atu;unsigned int busy_mask,reads,clear_after;
 int msi_error,atu_error,stops,ber_logs;bool msi_allocated,endpoint_reset;} model;
static u32 readl(void *address){
 if(address==model.atu){
  model.reads++;
  if(model.clear_after && model.reads>=model.clear_after)*model.atu&=~model.busy_mask;
 }
 return *(u32 *)address;
}
static void writel(u32 value,void *address){*(u32 *)address=value;}
static ktime_t ktime_get(void){return model.time;}
static ktime_t ktime_add_us(ktime_t value,u64 delta){return value+delta;}
static int ktime_compare(ktime_t a,ktime_t b){return (a>b)-(a<b);}
#define might_sleep_if(v) ((void)(v))
#define barrier() ((void)0)
#define cpu_relax() ((void)0)
static void usleep_range(unsigned long low,unsigned long high){(void)low;model.time+=high;}
static void msleep(unsigned int ms){model.time+=(uint64_t)ms*1000;}
static int clk_prepare_enable(struct clk *c){(void)c;return 0;}
static int phy_power_on(struct phy *p){(void)p;return 0;}
static int phy_power_off(struct phy *p){(void)p;return 0;}
static int reset_control_assert(struct reset_control *r){(void)r;return 0;}
static int reset_control_deassert(struct reset_control *r){(void)r;return 0;}
static void gpiod_set_value_cansleep(struct gpio_desc *g,int value){(void)g;model.endpoint_reset=value;}
#define dev_info(d,format,...) do{(void)(d);if(strstr(format,"BER notify"))model.ber_logs++;}while(0)
#define dev_err_probe(d,error,...) ((void)(d),(error))
#define raw_spin_lock_irqsave(l,f) do{(f)=0;pthread_mutex_lock(l);}while(0)
#define raw_spin_unlock_irqrestore(l,f) do{(void)(f);pthread_mutex_unlock(l);}while(0)
struct irq_data {void *chip;unsigned int hwirq;};
struct irq_desc {};
static void rtl_pcie_irq(struct irq_desc *desc){(void)desc;}
static void irq_set_chained_handler_and_data(int irq,void (*handler)(struct irq_desc *),void *data){
 (void)data;assert(irq==74 && model.msi_allocated && handler==rtl_pcie_irq);
}
static void *irq_data_get_irq_chip_data(struct irq_data *d){return d->chip;}
static int dw_pcie_msi_host_init(struct dw_pcie_rp *p){
 assert(p->has_msi_ctrl && p->msi_irq[0]==74 && p->num_vectors==32);
 model.msi_allocated=!model.msi_error;return model.msi_error;
}
'''

test = r'''
int main(void){
 u32 global[128]={},serdes[512]={},coherent[7]={};struct device dev;
 struct rtl_pcie p={.pci={.dev=&dev},.global=global,.serdes=serdes,.coherent_atu=coherent,
  .ddr_bytes=0x20000000,.alias=4,.endpoint_ready_ms=150,.irq=74};
 pthread_mutex_init(&p.irq_lock,NULL);
 unsigned int ber_cases=0;
 for(unsigned int profile=0;profile<2;profile++)for(unsigned int index=0;index<2;index++)
  for(unsigned int ready=0;ready<4;ready++) {
   model=(__typeof__(model)){};memset(serdes,0,sizeof(serdes));
   serdes[0x7c/4]=BIT(4)|(ready&1);serdes[0x17c/4]=(ready>>1)&1;
   p.profile=profile?'B':'A';p.index=index;
   assert(!rtl_pcie_host_start(&p.pci.pp));
   assert(!model.stops && !model.endpoint_reset && model.ber_logs==(ready!=3));
   assert(global[0x40/4]==dbi.start && global[0x48/4]==atu.start);
   ber_cases++;
  }
 /* Mandatory ATU initialization still propagates its actual error. */
 model.atu_error=-ETIMEDOUT;p.profile='A';
 assert(rtl_pcie_host_start(&p.pci.pp)==-ETIMEDOUT && model.stops==1);
 model.atu_error=0;
 for(unsigned int mode=0;mode<2;mode++) {
  unsigned int mask=BIT(30+mode);
  model.time=0;model.atu=coherent;model.busy_mask=mask;model.reads=0;model.clear_after=3;
  coherent[0]=mask|0x01000003;
  assert(!rtl_atu_wait(&p,0,mask) && model.reads==3 && coherent[0]==0x01000003);
  model.reads=0;model.clear_after=0;model.time=0;coherent[0]=mask|1;
  assert(rtl_atu_wait(&p,0,mask)==-ETIMEDOUT && (coherent[0]&mask));
  model.reads=0;model.time=0;coherent[0]=1;
  assert(!rtl_atu_wait(&p,0,mask) && model.reads==1);
 }
 model.atu=NULL;model.msi_error=0;
 for(unsigned int bits=0;bits<16;bits++) {
  global[GLB_IRQ_ENABLE/4]=0;global[GLB_IRQ_STATUS/4]=0xa55a;
  for(unsigned int pin=0;pin<4;pin++)if(bits&BIT(pin)) {
   struct irq_data irq={.chip=&p,.hwirq=pin};rtl_pcie_intx_mask_set(&irq,true);
  }
  unsigned int before=global[GLB_IRQ_ENABLE/4];
  assert(!rtl_pcie_msi_init(&p.pci.pp) && model.msi_allocated);
  assert(global[GLB_IRQ_ENABLE/4]==(before|GLB_MSI_IRQ));
  assert(global[GLB_IRQ_STATUS/4]==0xa55a); /* No pending endpoint IRQ gets cleared after bind. */
  for(unsigned int pin=0;pin<4;pin++) {
   struct irq_data irq={.chip=&p,.hwirq=pin};rtl_pcie_intx_mask_set(&irq,true);
  }
  assert(global[GLB_IRQ_ENABLE/4]==(0x55|GLB_MSI_IRQ));
 }
 global[GLB_IRQ_ENABLE/4]=0x55;global[GLB_MSI_ENABLE/4]=0;model.msi_error=-ENOMEM;
 assert(rtl_pcie_msi_init(&p.pci.pp)==-ENOMEM && global[GLB_IRQ_ENABLE/4]==0x55 && !global[GLB_MSI_ENABLE/4]);
 pthread_mutex_destroy(&p.irq_lock);
 printf("Native PCIe:%u BER startup cases,actual Linux ATU busy/complete/timeout waits,and16 INTx/MSI enable cases passed\n",ber_cases);
}
'''
structure = re.search(r'struct rtl_pcie \{.*?\n\};', source, re.S)[0]
parts = [common, structure]
parts += [macro(name) for name in ('poll_timeout_us', 'read_poll_timeout', 'readx_poll_timeout', 'readl_poll_timeout')]
parts += [function(name) for name in ('rtl_pcie_from_dw', 'rtl_serdes_read', 'rtl_serdes_write',
                                     'rtl_serdes_delay', 'rtl_serdes_wait', 'rtl_atu_wait', 'rtl_pcie_release_internal')]
parts.append('static int rtl_atu_prepare(struct rtl_pcie *p){(void)p;return model.atu_error;}\n'
             'static void rtl_pcie_power_stop(struct rtl_pcie *p){(void)p;model.stops++;}\n')
parts += [function(name) for name in ('rtl_pcie_host_start', 'rtl_pcie_msi_init', 'rtl_pcie_intx_mask_set')]
parts.append(test)
with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='pcie-host-control-') as directory:
    work = Path(directory)
    unit, binary = work / 'control.c', work / 'control'
    unit.write_text('\n'.join(parts))
    subprocess.run(['cc', '-g', '-pthread', '-fsanitize=address,undefined', '-I' + str(drivers),
                    str(unit), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
(root / 'logs/pcie-host-control-test.json').write_text(json.dumps({
    'native_source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'Linux_iopoll_sha256': hashlib.sha256(polling.encode()).hexdigest(),
    'BER_startup_cases': 16, 'BER_timeout_continues': True,
    'ATU_test': 'Actual rtl_atu_wait plus actual Linux polling macros;busy bits30/31 clear to finish',
    'ATU_completion_and_timeout_cases': 6, 'INTx_MSI_cases': 16,
    'MSI_enable_before_pci_host_probe': True, 'pending_IRQs_preserved_after_binding': True,
    'sanitizers': ['ASan', 'UBSan'], 'hardware_verified': False,
}, indent=2) + '\n')
