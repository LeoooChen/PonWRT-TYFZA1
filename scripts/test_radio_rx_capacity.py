#!/usr/bin/env python3
"""Exercise receive startup/mode changes against the advertised MPDU size."""
from pathlib import Path
import re, subprocess, tempfile
root = Path(__file__).resolve().parents[1]
driver = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/wireless/realtek/rtl_ax_ap'
source = (driver / 'mac-runtime.h').read_text()
def function(name):
    start = re.search(r'^static[^;]*?\b' + name + r'\([^;]*?\)\s*\{', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'
code = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#define BIT(n) (1U << (n))
#define RTL_AX_BIT(n) BIT(n)
struct rtl_ax_io {void *ctx;unsigned(*read16)(void*,unsigned);void(*write16)(void*,unsigned,unsigned);void(*write8)(void*,unsigned,unsigned);int(*poll32)(void*,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned*);};
static unsigned filter;
static unsigned read16(void *p,unsigned r){(void)p;(void)r;return 0;}
static void write16(void *p,unsigned r,unsigned v){(void)p;(void)r;(void)v;}
static void write8(void *p,unsigned r,unsigned v){(void)p;(void)r;(void)v;}
static int poll32(void*p,unsigned r,unsigned m,unsigned v,unsigned d,unsigned t,unsigned*l){(void)p;(void)r;(void)m;(void)v;(void)d;(void)t;*l=0;return 0;}
static void rtl_ax_update32(const struct rtl_ax_io*i,unsigned r,unsigned clear,unsigned set){(void)i;if(r==0xce20)filter=(filter&~clear)|set;}
static void rtl_ax_update8(const struct rtl_ax_io*i,unsigned r,unsigned c,unsigned s){(void)i;(void)r;(void)c;(void)s;}
"""
code += function('rtl_ax_cmac_receive') + function('rtl_ax_hal_start_receive') + function('rtl_ax_receive_mode')
code += r"""
int main(void){
 struct rtl_ax_io io={0,read16,write16,write8,poll32};
 for(unsigned fw=0;fw<2;fw++){
  filter=0x0800003f;assert(!rtl_ax_cmac_receive(&io,fw));
  unsigned capacity=((filter>>16)&63)*512;
  assert(capacity>=11454 && capacity<=12288-256);
  rtl_ax_hal_start_receive(&io,0,0);
  assert(((filter>>16)&63)*512==capacity);
  unsigned modes[]={0,1,2,4};
  for(unsigned i=0;i<4;i++){assert(!rtl_ax_receive_mode(&io,modes[i]));assert(((filter>>16)&63)*512==capacity);}
 }
 puts("RX capacity covers advertised 11454-byte MPDU across start and mode switches");
}
"""
with tempfile.TemporaryDirectory(prefix='rtl-rx-capacity-') as d:
    d=Path(d);(d/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Wextra','-Werror',str(d/'test.c'),'-o',str(d/'test')],check=True)
    subprocess.run([str(d/'test')],check=True)
