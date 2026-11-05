#!/usr/bin/env python3
"""Execute the QSPI polling loop against ready, busy and timeout hardware models."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/spi/spi-rtl9617c.c').read_text()
body = source[source.index('static int rtl_qspi_kick('):source.index('static bool rtl_qspi_supports_op(')]
defines = '\n'.join(line for line in source.splitlines() if re.match(r'#define (BUSY|BUSY_START|BUSY_WRITE|BUSY_MASK)\s', line))
code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#define BIT(n) (1U<<(n))
typedef int64_t ktime_t;
struct rtl_qspi {unsigned char *base;};
static unsigned char registers[64];
static unsigned int command,reads,ready_after,mode;
static ktime_t now;
static void writel(unsigned int value,void *address){assert(address==registers+0x10);command=value;}
static unsigned int readl(void *address){
 assert(address==registers+0x10);reads++;
 if(mode==2)return (1U<<16); /* BUSY_MASK must also check bit16, not just START. */
 return reads>=ready_after?0:2;
}
static ktime_t ktime_get(void){return now;}
static ktime_t ktime_add_us(ktime_t time,unsigned int us){return time+us;}
static bool ktime_after(ktime_t a,ktime_t b){return a>b;}
static void cpu_relax(void){now+=25000;}
''' + defines + '\n' + body + r'''
int main(void){
 struct rtl_qspi qspi={.base=registers};unsigned int cases=0;
 for(unsigned int out=0;out<2;out++)for(unsigned int scenario=0;scenario<4;scenario++){
  now=0;reads=0;mode=scenario;
  ready_after=scenario==0?1:scenario==1?3:scenario==3?7:~0U;
  int ret=rtl_qspi_kick(&qspi,out);
  assert(command==(2U|(out?1U<<9:0)));
  if(scenario==2)assert(ret==-ETIMEDOUT && now==125000 && reads==7);
  else assert(!ret && reads==ready_after);
  if(scenario==3)assert(now==125000); /* Final post-deadline read accepts completion. */
  cases++;
 }
 printf("%u QSPI ready/busy/deadline cases passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='qspi-poll-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True, timeout=5)
