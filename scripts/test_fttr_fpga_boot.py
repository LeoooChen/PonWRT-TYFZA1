#!/usr/bin/env python3
"""Compare native post-load writes with the retained sloader shell and test FPGA dependency."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from prepare_fttr_fpga import prepare


def function(source, name):
    start = source.index('static ', source.index(name) - 30)
    brace = source.index('{', start); end = brace + 1; depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock-script', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source_dir = ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/net/ethernet/realtek'
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        image = prepare(args.image, folder / 'firmware')
        assert (folder / 'firmware/fttr-top.bin').read_bytes() == args.image.read_bytes()
        bad = folder / 'bad.bin'; bad.write_bytes(args.image.read_bytes()[:-1])
        try: prepare(bad, folder / 'bad')
        except ValueError: pass
        else: raise AssertionError('truncated image accepted')
        changed = bytearray(args.image.read_bytes()); changed[-1] ^= 1; bad.write_bytes(changed)
        try: prepare(bad, folder / 'bad')
        except ValueError: pass
        else: raise AssertionError('modified image accepted')
        assert not (folder / 'bad').exists()
        shim = r'''
#include <errno.h>
#include "rtl9617c-fttr-fpga.h"
unsigned int events[16][3], count, fail_at;
static int write_reg(void *ctx, unsigned int reg, unsigned int value) {
    (void)ctx; events[count][0]=1; events[count][1]=reg; events[count++][2]=value;
    return count==fail_at ? -EIO : 0;
}
static void delay(void *ctx, unsigned int us) {
    (void)ctx; events[count][0]=2; events[count++][1]=us;
}
int run(int backend, int calibrated, int failure) {
    struct rtl_fttr_reg_io io={.write=write_reg,.delay=delay};
    count=0; fail_at=failure;
    return rtl_fttr_fpga_init(&io, backend, calibrated);
}
'''
        (folder / 'regs.c').write_text(shim)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', '-I' + str(source_dir),
                        str(folder / 'regs.c'), '-o', str(folder / 'regs.so')], check=True)
        native = C.CDLL(str(folder / 'regs.so'))
        def events():
            rows = (C.c_uint * 3 * 16).in_dll(native, 'events')
            return [['write', x[1], x[2]] if x[0] == 1 else ['delay', x[1]]
                    for x in rows[:C.c_uint.in_dll(native, 'count').value]]
        # Execute the actual retained post-ready shell suffix, redirecting only
        # filesystem paths and modeling the external MDIO/usleep commands.
        script = args.stock_script.read_text()
        script = script[script.index('    FpgaTypeStr='):].rsplit('\nfi', 1)[0]
        script = script.replace('/var/config/rtkoltbosa_k.bin', str(folder / 'calibration'))
        script = script.replace('/dev/ttyS0', str(folder / 'console')).replace('/proc/sloader/status', str(folder / 'status'))
        comparisons = []
        for backend in (1, 2):
            for calibrated in (0, 1):
                calibration = folder / 'calibration'
                calibration.unlink(missing_ok=True)
                if calibrated: calibration.touch()
                trace = folder / 'trace'; trace.write_text('')
                prefix = '''mdiocmd() {
if [ "$2" = 0 ]; then printf 'data:0x%%08x\\n' %d;
else printf 'write %%s %%s\\n' "$4" "$5" >> '%s'; fi
}
usleep() { printf 'delay %%s\\n' "$1" >> '%s'; }
''' % (backend, trace, trace)
                subprocess.run(['bash', '-c', prefix + script], check=True)
                expected = [[tokens[0], *[int(s, 0) for s in tokens[1:]]] for tokens in map(str.split, trace.read_text().splitlines())]
                assert native.run(backend, calibrated, 0) == 0
                assert events() == expected, (backend, calibrated, events(), expected)
                for offset, entry in enumerate(expected):
                    if entry[0] == 'write':
                        assert native.run(backend, calibrated, offset + 1) == -5
                        assert events() == expected[:offset + 1]
                comparisons.append({'backend': backend, 'calibration_present': calibrated, 'events': expected})
        assert native.run(3, 1, 0) == -95 and not events()
        source = (source_dir / 'rtl9617c-fttr.c').read_text()
        acquire = function(source, 'rtl_fttr_fpga_acquire')
        # C shims run production dependency branching; no kernel/device probe
        # is asserted here. Real kernel objects/DT compile separately.
        fixture = r'''
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define IS_REACHABLE(x) 1
#define CONFIG_FPGA 1
#define EPROBE_DEFER 517
#define FPGA_MGR_STATE_OPERATING 9
#define DL_FLAG_AUTOREMOVE_CONSUMER 2
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
struct device_node { int unused; };
struct device { struct device_node *of_node; struct device *parent; };
struct fpga_manager;
struct ops { int (*state)(struct fpga_manager *); };
struct fpga_manager { struct device dev; struct ops *mops; int state; };
static struct device_node node;
static struct device supplier;
static int physical_state(struct fpga_manager *m);
static struct ops ops={physical_state};
static struct fpga_manager mgr={.dev={.parent=&supplier},.mops=&ops};
static int scenario, refs, locks, reads, links, released;
static int physical_state(struct fpga_manager *m) { (void)m; reads++; return scenario==8 ? 0 : 9; }
static void *of_find_property(struct device_node *n,const char *s,void *v) {
    (void)n;(void)s;(void)v; return scenario==0 ? NULL : &node;
}
static struct device_node *of_parse_phandle(struct device_node *n,const char *s,int i) {
    (void)n;(void)s;(void)i;return scenario==1 ? NULL : &node;
}
static int of_device_is_compatible(struct device_node *n,const char *s) { (void)n;(void)s; return 1; }
static struct fpga_manager *of_fpga_mgr_get(struct device_node *n) {
    (void)n; if(scenario==2) return (void *)(intptr_t)-ENODEV;
    refs++; return &mgr;
}
static void *device_link_add(struct device *a,struct device *b,int f) {
    (void)a;(void)b;(void)f;links++;return scenario==3 ? NULL : &node;
}
static int device_is_bound(struct device *d) { (void)d;return scenario!=4; }
static int fpga_mgr_lock(struct fpga_manager *m) { (void)m;if(scenario==5)return -EBUSY;locks++;return 0; }
static void fpga_mgr_unlock(struct fpga_manager *m) { (void)m;locks--; }
static void fpga_mgr_put(struct fpga_manager *m) { (void)m;refs--; }
static void rtl_fttr_fpga_release(void *m) { fpga_mgr_put(m);released++; }
static int devm_add_action_or_reset(struct device *d,void (*fn)(void *),void *p) {
    (void)d;if(scenario==9){fn(p);return -ENOMEM;}return 0;
}
static void of_node_put(struct device_node *n) { (void)n; }
'''
        wrapper = r'''
int dependency(int test) {
    struct device dev={.of_node=&node}; int ret;
    scenario=test;refs=locks=reads=links=released=0;mgr.state=test==7?0:9;
    ret=rtl_fttr_fpga_acquire(&dev);
    if(locks || (ret && refs))return 1000;
    if(!ret && test!=0){if(refs!=1 || reads!=1)return 1001;rtl_fttr_fpga_release(&mgr);}
    return ret;
}
'''
        (folder / 'dependency.c').write_text(fixture + acquire + wrapper)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', str(folder / 'dependency.c'),
                        '-o', str(folder / 'dependency.so')], check=True)
        dep = C.CDLL(str(folder / 'dependency.so'))
        expected = {0:0,1:-22,2:-517,3:-12,4:-517,5:-16,6:0,7:-5,8:-5,9:-12}
        for scenario, result in expected.items(): assert dep.dependency(scenario) == result, scenario
        probe = function(source, 'rtl_fttr_probe')
        assert probe.index('rtl_fttr_fpga_acquire') < probe.index('rtl_fttr_identify')
        loader_source = (source_dir.parents[3] / 'drivers/fpga/tyfza1-pg2l100h.c').read_text()
        loader = function(loader_source, 'tyfza1_fpga_load')
        load_fixture = r'''
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#define GFP_KERNEL 0
struct device { int unused; };
struct fpga_manager { int unused; };
struct fpga_image_info { char *firmware_name; };
static int fault, allocated, locked, loads;
static struct fpga_image_info *fpga_image_info_alloc(struct device *d) {
    (void)d;if(fault==1)return NULL;allocated++;return calloc(1,sizeof(struct fpga_image_info));
}
static char *devm_kstrdup(struct device *d,const char *s,int flags) {
    (void)d;(void)flags;return fault==2?NULL:strdup(s);
}
static void fpga_image_info_free(struct fpga_image_info *p) { free(p->firmware_name);free(p);allocated--; }
static int fpga_mgr_lock(struct fpga_manager *m) { (void)m;if(fault==3)return -EBUSY;locked++;return 0; }
static void fpga_mgr_unlock(struct fpga_manager *m) { (void)m;locked--; }
static int fpga_mgr_load(struct fpga_manager *m,struct fpga_image_info *info) {
    (void)m;if(!locked || strcmp(info->firmware_name,"realtek/rtl9617c/fttr-top.bin"))abort();
    loads++;return fault==4?-ENOENT:fault==5?-EIO:0;
}
'''
        load_wrapper = r'''
int run_load(int f) {
    struct device d;struct fpga_manager m;int ret;
    fault=f;allocated=locked=loads=0;
    ret=tyfza1_fpga_load(&d,&m,"realtek/rtl9617c/fttr-top.bin");
    if(allocated || locked || loads!=(f==0 || f>=4))return 1000;
    return ret;
}
'''
        (folder / 'load.c').write_text(load_fixture + loader + load_wrapper)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', str(folder / 'load.c'),
                        '-o', str(folder / 'load.so')], check=True)
        load = C.CDLL(str(folder / 'load.so'))
        load_cases = {0:0,1:-12,2:-12,3:-16,4:-2,5:-5}
        for scenario, result in load_cases.items(): assert load.run_load(scenario) == result
        load_probe = function(loader_source, 'tyfza1_fpga_probe')
        assert '"firmware-name", &firmware' in load_probe and 'tyfza1_fpga_load(dev, mgr, firmware)' in load_probe
        store = loader_source[loader_source.index('static ssize_t firmware_store'):loader_source.index('static DEVICE_ATTR_WO(firmware)')]
        assert store.index('"firmware-name"') < store.index('tyfza1_fpga_load')
    result = {'stock_script_sha256': hashlib.sha256(args.stock_script.read_bytes()).hexdigest(),
              'image': image, 'postload_comparisons': comparisons, 'dependency_cases': expected, 'load_cases': load_cases,
              'scope': 'Executed original post-ready shell with modeled MDIO, native register helper and dependency branching shims. Image byte identity verified; no physical FPGA, optical or ONU service acceptance.'}
    args.output.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
