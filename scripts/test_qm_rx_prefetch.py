#!/usr/bin/env python3
"""Actual QM poll: prefetch only synchronized, valid, bounded RX payloads."""
import argparse
import ast
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
drivers = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path)
args = parser.parse_args()
source = (args.source or drivers / 'rtl9617c-qm.c').read_text()
tree = ast.parse((root / 'scripts/test_ni_rx_credit.py').read_text())
common = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'common' for t in n.targets))
common = common.replace('#include <assert.h>', '#include <assert.h>\n#include <sys/mman.h>')
common = common.replace('#define prefetch(p) ((void)(p))', '')
common = common.replace('u8 storage[128]', 'u8 storage[2048]')
common = common.replace('static bool seen[129];', r'''
static bool seen[129];
static unsigned int test_length, test_header, synced, hints;
static bool fail_alloc;
static void prefetch(const void *p) {
 uintptr_t first=(uintptr_t)active->memory+8192+RTL_QM_HEADROOM+test_header;
 assert(synced==1);
 assert((uintptr_t)p==first+hints*L1_CACHE_BYTES);
 assert((uintptr_t)p>=first && (uintptr_t)p<first+test_length);
 hints++;
}
''')
common = common.replace('assert(n==2048&&direction==2);', 'assert(n==2048&&direction==2);synced++;')
common = common.replace('assert(length==64);', 'assert(length==test_length);assert(hints==(test_length+63)/64);if(fail_alloc)return NULL;')
a = common.index('static void received(')
b = common.index('static void napi_gro_receive', a)
common = common[:a] + r'''
static void received(struct sk_buff *s){
 unsigned skip=kind==0?14:0;
 assert(s->len==test_length-skip);
 assert(!memcmp(s->data,active->memory+8192+RTL_QM_HEADROOM+test_header+skip,s->len));
 memset(s->data,0xcc,s->len);delivered++;free(s);
}
''' + common[b:]
start = source.index('static void rtl_qm_rx_retire(')
end = source.index('\nssize_t rtl_qm_rx_state(', start)
tests = r'''
int main(void){unsigned cases=0;
unsigned sizes[]={14,63,64,65,127,128,129,1490,1584,1592};
for(unsigned mode=0;mode<4;mode++)for(unsigned si=0;si<ARRAY_SIZE(sizes);si++)for(unsigned fault=0;fault<5;fault++){
 struct net_device owner={.running=true},input={.running=true};struct napi_struct napi={.dev=&owner};
 struct rtl9617c_ni_wifi_endpoint wifi={.netdev=&input,.receive=wifi_receive};struct rtl9617c_ni_endpoint pon={.receive=pon_receive};
 struct rtl_qm_rx rx={.memory_dma=0x04000000,.device=&owner,.pon=&pon};rx.wifi[1]=&wifi;rx.lan[0]=&input;
 kind=mode==3?0:mode;test_length=sizes[si];test_header=(mode==1||mode==3)?16:8;
 rx.memory=mmap(NULL,RTL_QM_MEMORY_BYTES,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(rx.memory!=MAP_FAILED);
 rx.ring_memory=calloc(1,8192);rx.ni=calloc(1,65536);active=&rx;
 producer=consumer=delivered=injected=synced=hints=0;fail_alloc=fault==1;
 enqueue(0);u32 h0=mode==3?BIT(31):mode==2?5U<<18:0;
 u32 h1=(test_length+(mode==3?8:0))<<15|(mode==2?7U<<9:0);
 put(__builtin_bswap32(h0),rx.memory+8192+RTL_QM_HEADROOM);put(__builtin_bswap32(h1),rx.memory+8192+RTL_QM_HEADROOM+4);
 if(mode==3)put(__builtin_bswap32(15U<<8),rx.memory+8192+RTL_QM_HEADROOM+12);
 u8 *data=rx.memory+8192+RTL_QM_HEADROOM+test_header;
 for(unsigned i=0;i<test_length;i++)data[i]=i*37;
 data[0]=2;data[12]=8;data[13]=0;
 if(mode==1)put(1U<<14|(test_length+16),rx.ring_memory+4);
 if(fault==2)put(get_unaligned_le32(rx.ring_memory)&~BIT(4),rx.ring_memory);
 if(fault==3)put(0,rx.ring_memory);
 if(fault==4){put(__builtin_bswap32((test_header==16?BIT(31):0)),rx.memory+8192+RTL_QM_HEADROOM);put(__builtin_bswap32(1U<<15),rx.memory+8192+RTL_QM_HEADROOM+4);put(0,rx.ring_memory+4);}
 assert(!mprotect(rx.memory,RTL_QM_MEMORY_BYTES,PROT_READ));
 bool valid=fault<2&&test_length<=RTL_QM_DATA_BYTES-test_header;
 assert(rtl_qm_rx_poll(&rx,&napi,64)==1&&consumer==1);
 assert(synced==(fault==3?0:1));assert(hints==(valid?(test_length+63)/64:0));
 assert(delivered==(valid&&!fail_alloc));assert(rx.refill.returned==(fault==3?0:1));
 munmap(rx.memory,RTL_QM_MEMORY_BYTES);free(rx.ring_memory);free(rx.ni);cases++;
}
printf("Actual QM prefetch: %u LAN/WiFi/PON/full-header size/fault cases, CPU sync before bounded hints and before allocation, read-only source passed\n",cases);
}
'''
with tempfile.TemporaryDirectory(prefix='qm-prefetch-', dir=root / 'artifacts') as tmp:
    tmp = Path(tmp)
    (tmp / 'test.c').write_text(common + source[start:end] + tests)
    subprocess.run(['cc', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-Wno-unused-variable', '-Wno-misleading-indentation', '-fsanitize=address,undefined',
                    '-I', str(drivers), '-I', str(root / 'port/overlay/target/linux/rtl9617c/files-6.18/include/linux'),
                    str(tmp / 'test.c'), '-o', str(tmp / 'test')], check=True)
    subprocess.run([str(tmp / 'test')], check=True)
