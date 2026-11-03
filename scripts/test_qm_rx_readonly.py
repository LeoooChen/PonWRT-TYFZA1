#!/usr/bin/env python3
"""Run the actual QM poll on read-only DMA buffers across repeated receptions."""
import argparse
import ast
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision')
args = parser.parse_args()
source = (subprocess.check_output(['git', 'show', f'{args.revision}:{relative}/rtl9617c-qm.c'],
                                 cwd=root, text=True) if args.revision else
          (root / relative / 'rtl9617c-qm.c').read_text())
fixture = ast.parse((root / 'scripts/test_ni_rx_credit.py').read_text())
common = next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'common' for t in n.targets))
common = common.replace('#include <assert.h>', '#include <assert.h>\n#include <sys/mman.h>')
common = common.replace('static bool seen[129];', '''static bool seen[129];
static bool allocation_failure;
static unsigned int cpu_syncs;
''')
common = common.replace('assert(n==2048&&direction==2);', 'assert(n==2048&&direction==2);cpu_syncs++;')
common = common.replace('dma_sync_single_for_cpu(d,a,n,direction);}',
                        '(void)d;(void)a;(void)n;(void)direction;assert(!"redundant device sync on read-only RX");}')
common = common.replace('(void)n;assert(length==64);', '(void)n;assert(length==64);if(allocation_failure)return NULL;')
common = common.replace('if(!injected++){for(unsigned i=64;i<129;i++)enqueue(i);}',
                        'memset(s->data,0xcc,s->len);')
if 'endpoint->receive(endpoint, napi, skb, wifi_forwarded)' not in source:
    common = common.replace('struct napi_struct *,struct sk_buff *,bool', 'struct sk_buff *,bool')
    common = common.replace('struct napi_struct *n,struct sk_buff *s,bool f', 'struct sk_buff *s,bool f')
    common = common.replace('assert(n && n->dev==active->device);', '')
start = source.index('static void rtl_qm_rx_retire(')
end = source.index('\nssize_t rtl_qm_rx_state(', start)
case = r'''
int main(void) {
 unsigned cases=0;
 for(unsigned mode=0;mode<3;mode++)for(unsigned fault=0;fault<3;fault++){
  struct net_device owner={.running=true},input={.running=true};
  struct napi_struct napi={.dev=&owner};
  struct rtl9617c_ni_wifi_endpoint wifi={.netdev=&input,.receive=wifi_receive};
  struct rtl9617c_ni_endpoint pon={.receive=pon_receive};
  struct rtl_qm_rx rx={.memory_dma=0x04000000,.device=&owner,.pon=&pon};
  rx.wifi[1]=&wifi;rx.lan[0]=&input;kind=mode;active=&rx;
  rx.memory=mmap(NULL,RTL_QM_MEMORY_BYTES,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
  assert(rx.memory!=MAP_FAILED);
  rx.ring_memory=calloc(1,8192);rx.ni=calloc(1,65536);
  unsigned char *original=malloc(RTL_QM_MEMORY_BYTES);assert(original);
  for(unsigned epoch=0;epoch<4;epoch++){
   assert(!mprotect(rx.memory,RTL_QM_MEMORY_BYTES,PROT_READ|PROT_WRITE));
   memset(rx.memory,0x30+epoch,RTL_QM_MEMORY_BYTES);
   producer=consumer=delivered=injected=cpu_syncs=0;
   memset(seen,0,sizeof(seen));memset(rx.read,0,sizeof(rx.read));
   memset(&rx.refill,0,sizeof(rx.refill));
   allocation_failure=fault==1;
   for(unsigned i=0;i<64;i++){
    enqueue(i);
    if(fault==2){
     u32 command=get_unaligned_le32(rx.ring_memory+i*8);
     put(command&~BIT(4),rx.ring_memory+i*8);
    }
   }
   memcpy(original,rx.memory,RTL_QM_MEMORY_BYTES);
   /* Any source mutation by the production poll faults, including on errors. */
   assert(!mprotect(rx.memory,RTL_QM_MEMORY_BYTES,PROT_READ));
   assert(rtl_qm_rx_poll(&rx,&napi,64)==64);
   assert(cpu_syncs==64&&consumer==64&&rx.refill.returned==64&&!rx.refill.pending);
   assert(delivered==(fault?0:64));
   assert(!memcmp(original,rx.memory,RTL_QM_MEMORY_BYTES));
   assert(rtl_qm_rx_poll(&rx,&napi,64)==0&&cpu_syncs==64);
   cases++;
  }
  munmap(rx.memory,RTL_QM_MEMORY_BYTES);free(original);free(rx.ring_memory);free(rx.ni);
 }
 printf("QM read-only RX: %u cases, LAN/Wi-Fi/PON, repeated buffers, allocation failure, invalid EPP and private skb mutation passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='qm-readonly-', dir=root / 'artifacts') as tmp:
    tmp = Path(tmp)
    (tmp / 'test.c').write_text(common + source[start:end] + case)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-function', '-Wno-misleading-indentation',
                    '-fsanitize=address,undefined', '-I', str(root / relative),
                    '-I', str(root / 'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),
                    str(tmp / 'test.c'), '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True)
