#!/usr/bin/env python3
"""Check actual NI poll against the factory EPP producer-pointer contract."""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
relative = 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--revision')
parser.add_argument('--expect-replay', action='store_true')
args = parser.parse_args()
source = (subprocess.check_output(['git', 'show', args.revision + ':' + relative + '/rtl9617c-qm.c'],
                                 cwd=root, text=True) if args.revision else
          (root / relative / 'rtl9617c-qm.c').read_text())
fixture = ast.parse((root / 'scripts/test_ni_rx_credit.py').read_text())
common = next(ast.literal_eval(node.value) for node in fixture.body
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
              t.id == 'common' for t in node.targets))
common = common.replace('static bool seen[129];',
                        'static bool seen[129];\nstatic bool status_pending;')
common = common.replace('if(r==0x659c)return producer!=consumer;',
                        'if(r==0x659c)return status_pending || producer!=consumer;')
common = common.replace('assert(consumer<=producer);', '')
common = common.replace('assert(eq==4&&consumer>id);', 'assert(eq==4);(void)id;')
common = common.replace('if(!injected++){for(unsigned i=64;i<129;i++)enqueue(i);}', '')
start = source.index('static void rtl_qm_rx_retire(')
end = source.index('\nssize_t rtl_qm_rx_state(', start)
case = r'''
int main(void) {
 kind=1;
 struct net_device owner={.running=true},input={.running=true};
 struct napi_struct napi={.dev=&owner};
 struct rtl9617c_ni_wifi_endpoint wifi={.netdev=&input,.receive=wifi_receive};
 struct rtl_qm_rx rx={.memory_dma=0x04000000,.device=&owner};rx.wifi[1]=&wifi;
 rx.memory=calloc(1,RTL_QM_MEMORY_BYTES);rx.ring_memory=rx.memory;
 rx.ni=calloc(1,0x10000);active=&rx;
 /* Stale entries are not republished by a queue-status bit. */
 for(unsigned i=0;i<128;i++)enqueue(i);
 producer=consumer=128;status_pending=true;
 int consumed=rtl_qm_rx_poll(&rx,&napi,64);
 if (EXPECT_REPLAY) {
  assert(consumed==64 && delivered==64 && rx.read[0]==512);
  puts("Original poll delivers64 stale entries without producer progress");
 } else {
  assert(consumed==0 && !delivered && !rx.refill.pending && !rx.read[0]);
  /* A subsequent publication is consumed exactly once. */
  status_pending=false;enqueue(128);
  assert(rtl_qm_rx_poll(&rx,&napi,64)==1 && delivered==1 && seen[128]);
  assert(rtl_qm_rx_poll(&rx,&napi,64)==0 && delivered==1);
  /* A127-entry published batch crosses the byte-pointer wrap. */
  producer=consumer=126;rx.read[0]=126*8;
  memset(seen,0,sizeof(seen));delivered=0;
  memset(&rx.refill,0,sizeof(rx.refill));
  for(unsigned i=0;i<127;i++)enqueue(i);
  assert(rtl_qm_rx_poll(&rx,&napi,64)==64);
  assert(rtl_qm_rx_poll(&rx,&napi,64)==63);
  assert(rtl_qm_rx_poll(&rx,&napi,64)==0);
  assert(delivered==127 && consumer==producer && !rx.refill.pending);
  for(unsigned i=0;i<127;i++)assert(seen[i]);
  puts("Factory EPP contract: equal pointers empty; later publication and wrap exact");
 }
 free(rx.memory);free(rx.ni);
}
'''
with tempfile.TemporaryDirectory(prefix='ni-epp-', dir=root / 'artifacts') as temp:
    temp = Path(temp)
    (temp / 'test.c').write_text('#define EXPECT_REPLAY ' + str(int(args.expect_replay)) + '\n' +
                               common + source[start:end] + case)
    subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-Wno-misleading-indentation', '-Wno-unused-function', '-Wno-unused-variable',
                    '-fsanitize=address,undefined',
                    '-I', str(root / relative),
                    '-I', str(root / 'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),
                    str(temp / 'test.c'), '-o', str(temp / 'test')], check=True)
    result = subprocess.check_output([str(temp / 'test')], text=True)
    print(json.dumps({'result': result.strip(), 'actual_poll_C': True,
                      'hardware_timing_modeled': True, 'root_cause_claim': False}, indent=2))
