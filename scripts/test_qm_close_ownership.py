#!/usr/bin/env python3
"""Execute actual QM stop/free with rejected close writes and retained DMA allocations."""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--revision');p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
relative='port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c'
source=subprocess.check_output(['git','show',a.revision+':'+relative],cwd=root,text=True) if a.revision else (root/relative).read_text()
fixture=ast.parse((root/'scripts/test_qm_wifi_allocation.py').read_text())
code=next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='fixture' for t in n.targets))
code=code.replace('static void rtl_qm_rx_stop(struct rtl_qm_rx *r){(void)r;assert(0);}', 'void rtl_qm_rx_stop(struct rtl_qm_rx *);')
code=code.replace('#define dev_err(...) ((void)0)', 'static unsigned errors;\n#define dev_err(...) (errors++)')
code=code.replace('bool hardware_owned;int stop_error;', 'void *ni;bool deepq_configured,hardware_owned;int stop_error;')
code=code.replace('static void writel(u32 value,void *p){memcpy(p,&value,4);}',r'''
#define BIT(n) (1U<<(n))
#define CB_CTRL 0x950c
#define RMU_CTRL 0x6104
#define ES_CTRL 0x6108
static unsigned fail_mask,writes;
static void writel(u32 value,void *p){
 unsigned reg=(unsigned char*)p-ni;
 unsigned bit=reg==CB_CTRL?1:reg==0x610c?2:reg==RMU_CTRL?4:reg==ES_CTRL?8:0;
 writes++;if(!(fail_mask&bit))memcpy(p,&value,4);
}
''')
begin=source.index('static unsigned int rtl_qm_wifi_chunk_bytes(')
code+=source[begin:source.index('\nstatic unsigned int rtl_dq_read',begin)]
begin=source.index('void rtl_qm_rx_stop(')
code+=source[begin:source.index('\nstatic void rtl_qm_rx_retire(',begin)]
code+=('#define OLD_REVISION\n' if a.revision else '')
code+=r'''
static void arm(void){
 fail_mask=0;writel(0xabcdef01,ni+CB_CTRL);writel(0x1234ffff,ni+0x610c);writel(0x87654321,ni+RMU_CTRL);writel(0x8462ff0f,ni+ES_CTRL);writes=0;
}
int main(void){unsigned cases=0;
 for(unsigned fttr=0;fttr<2;fttr++)for(unsigned failures=1;failures<16;failures++)for(unsigned started=0;started<2;started++){
  struct device dev={};struct rtl_qm_rx rx={.ni=ni,.fttr_supported=fttr};
  memset(records,0,sizeof(records));calls=live=reject=errors=0;
  assert(!rtl_qm_rx_alloc(&dev,&rx));unsigned count=live;
  /* Includes addresses published by a start that later failed. */
  rx.hardware_owned=true;rx.deepq_configured=started;arm();fail_mask=failures;
  rtl_qm_rx_stop(&rx);
#ifndef OLD_REVISION
  assert(rx.stop_error==-EIO);
#endif
  assert(!rx.deepq_configured&&rx.hardware_owned&&writes==4&&live==count);
  for(unsigned attempt=0;attempt<3;attempt++){
   rtl_qm_rx_free(&dev,&rx);assert(live==count&&errors==attempt+1&&rx.hardware_owned);
  }
  fail_mask=0;rtl_qm_rx_free(&dev,&rx);
  assert(!live&&!rx.hardware_owned&&!rx.stop_error);
  assert(readl(ni+CB_CTRL)==0x2bcdef01&&readl(ni+0x610c)==0x12340000&&readl(ni+RMU_CTRL)==0x07654321&&readl(ni+ES_CTRL)==0x0462ff0f);
  cases++;
 }
 /* Unpublished allocations may be released without touching inherited HW. */
 for(unsigned fttr=0;fttr<2;fttr++){
  struct device dev={};struct rtl_qm_rx rx={.fttr_supported=fttr};calls=live=reject=errors=0;
  assert(!rtl_qm_rx_alloc(&dev,&rx));writes=0;
  rtl_qm_rx_free(&dev,&rx);assert(!live&&!writes&&!errors);cases++;
 }
 printf("QM stop/free: %u all-close-fault masks, partial-start ownership, retry, pool retention and unpublished allocation cases PASS\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='qm-close-') as temp:
    d=Path(temp);(d/'test.c').write_text(code)
    subprocess.run(['cc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-fsanitize=address,undefined',str(d/'test.c'),'-o',str(d/'test')],check=True)
    result=subprocess.run([str(d/'test')],text=True,capture_output=True)
    if a.revision:
        assert result.returncode and 'live==count&&errors==attempt+1&&rx.hardware_owned' in result.stderr,result
        print('REPRODUCED: old QM free releases hardware-published pools despite rejected close writes')
    else:
        assert result.returncode==0,result.stderr
        print(result.stdout,end='')
a.output.write_text(json.dumps(dict(revision=a.revision,native_passed=result.returncode==0,
    scope='Actual QM stop/free and allocation functions; rejected control writes and DMA allocator modeled. Successful disable is not proof of bus-idle or complete packet retirement.'),indent=2)+'\n')
