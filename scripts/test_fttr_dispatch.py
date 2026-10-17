#!/usr/bin/env python3
"""Execute the production QM source dispatch, including FTTR and upstream PON."""
import ctypes
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
path = root / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek/rtl9617c-qm.c'
text = path.read_text()
start = text.index('if (source >= ARRAY_SIZE(rx->lan)) {')
end, depth = text.index('{', start) + 1, 1
while depth:
    depth += (text[end] == '{') - (text[end] == '}')
    end += 1
branch = text[start:end]
shim = r'''
#include <stddef.h>
#include <stdint.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
#define FIELD_GET(mask,value) (((value)&(mask))>>18)
#define rcu_read_lock() do {} while(0)
#define rcu_read_unlock() do {} while(0)
#define rcu_dereference(p) (p)
struct napi_struct { int unused; }; struct sk_buff { int unused; };
struct rtl9617c_ni_endpoint {
 void (*receive)(struct rtl9617c_ni_endpoint *,struct napi_struct *,struct sk_buff *,unsigned int);
};
struct rx { void *lan[5];struct rtl9617c_ni_endpoint *fttr,*pon; };
struct device { struct { unsigned int rx_dropped; } stats; };
unsigned int delivered,tag,freed;
static void dev_kfree_skb_any(struct sk_buff *skb) {(void)skb;freed++;}
static void fttr_rx(struct rtl9617c_ni_endpoint *ep,struct napi_struct *n,struct sk_buff *s,unsigned int source) {
 (void)ep;(void)n;(void)s;delivered=6;tag=source;
}
static void pon_rx(struct rtl9617c_ni_endpoint *ep,struct napi_struct *n,struct sk_buff *s,unsigned int source) {
 (void)ep;(void)n;(void)s;delivered=7;tag=source;
}
unsigned int dispatch(unsigned int source,int fttr,int pon) {
 struct rtl9617c_ni_endpoint a={fttr_rx},b={pon_rx};
 struct rx r={.fttr=fttr?&a:0,.pon=pon?&b:0},*rx=&r;
 struct device d={0},*dev=&d;struct napi_struct np,*napi=&np;
 struct sk_buff packet,*skb=&packet;unsigned int h0=123U<<18;
 delivered=tag=freed=0;
'''
tail = r'''
consume:
 return d.stats.rx_dropped;
}
'''
with tempfile.TemporaryDirectory() as directory:
    directory = Path(directory)
    (directory / 'dispatch.c').write_text(shim + branch + tail)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                    str(directory / 'dispatch.c'), '-o', str(directory / 'dispatch.so')], check=True)
    library = ctypes.CDLL(str(directory / 'dispatch.so'))
    library.dispatch.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_int]
    for source, fttr, pon, expected in [(6,1,1,6),(7,1,1,7),(32,1,1,7),(63,1,1,7),
            (6,0,1,0),(7,1,0,0),(5,1,1,0),(31,1,1,0)]:
        drops = library.dispatch(source,fttr,pon)
        delivered = ctypes.c_uint.in_dll(library,'delivered').value
        freed = ctypes.c_uint.in_dll(library,'freed').value
        assert delivered == expected and drops == freed == int(not expected)
        if expected:
            assert ctypes.c_uint.in_dll(library,'tag').value == source | (123 << 8)
print('QM dispatch: source6 routes to FTTR; upstream7/LLIDs unchanged; absent/unknown endpoints drop once')
