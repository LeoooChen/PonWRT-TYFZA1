#!/usr/bin/env python3
"""Run the production init script's pre-FPGA sequence and NI supplier acquisition."""
import argparse
import ctypes as C
import json
from pathlib import Path
import subprocess
import tempfile

from test_fttr_fpga_boot import function

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    script = (ROOT / 'port/packages/tyfza1-fttr/files/tyfza1-fttr.init').read_text().split('start_service()')[0]
    driver = (ROOT / 'port/overlay/target/linux/rtl9617c/files-6.18/drivers/fpga/tyfza1-pg2l100h.c').read_text()
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        fixture = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <string.h>
#define EPROBE_DEFER 517
#define DL_FLAG_AUTOREMOVE_CONSUMER 1
struct device_node {int unused;};
struct device {struct device_node *of_node;};
struct platform_device {struct device dev;};
static struct device_node node;
static struct platform_device engine;
static int fault,node_refs,refs,links,begun;
static struct device_node *of_parse_phandle(struct device_node *n,const char *name,int index) {
 (void)n;assert(!strcmp(name,"realtek,network-engine") && !index);
 if(fault==1)return NULL;
 node_refs++;return &node;
}
static bool of_device_is_compatible(struct device_node *n,const char *name) {
 assert(n==&node && !strcmp(name,"realtek,rtl9617c-xram-eth"));return fault!=2;
}
static void of_node_put(struct device_node *n){assert(n==&node);node_refs--;}
static struct platform_device *of_find_device_by_node(struct device_node *n) {
 assert(n==&node);if(fault==3)return NULL;refs++;return &engine;
}
static bool device_is_bound(struct device *d){assert(d==&engine.dev);return fault!=4;}
static void *platform_get_drvdata(struct platform_device *p){assert(p==&engine);return fault==5?NULL:p;}
static void *device_link_add(struct device *consumer,struct device *supplier,int flags) {
 (void)consumer;assert(supplier==&engine.dev && flags==DL_FLAG_AUTOREMOVE_CONSUMER);
 links++;return fault==6?NULL:supplier;
}
static void put_device(struct device *d){assert(d==&engine.dev);refs--;}
static int rtl9617c_ni_fttr_load_begin(struct device *d){assert(d==&engine.dev);begun++;return fault==7?-EBUSY:0;}
'''
        fixture += function(driver, 'tyfza1_fpga_p6_acquire')
        fixture += r'''
struct spi_device {struct device dev;};
static int held,setup_calls,setup_error;
static int dev_err_probe(struct device *d,int error,const char *message){(void)d;(void)message;return error;}
static int tyfza1_fpga_setup(struct spi_device *spi){(void)spi;assert(refs==1 && begun==1);setup_calls++;return setup_error;}
static void rtl9617c_ni_fttr_load_end(struct device *d,int error){assert(d==&engine.dev && error==setup_error);held++;}
'''
        fixture += function(driver, 'tyfza1_fpga_probe')
        fixture += r'''
int run(int f) {
 struct device consumer={&node},*supplier=NULL;
 fault=f;node_refs=refs=links=begun=0;
 int ret=tyfza1_fpga_p6_acquire(&consumer,&supplier);
 assert(!node_refs && refs==(!ret));
 assert(begun==(f==0 || f==7));
 if(!ret){assert(supplier==&engine.dev);put_device(supplier);}
 return ret;
}
int probe(int f,int error) {
 struct spi_device spi={.dev={&node}};
 fault=f;node_refs=refs=links=begun=held=setup_calls=0;setup_error=error;
 int ret=tyfza1_fpga_probe(&spi);
 assert(!node_refs && !refs && setup_calls==(f==0) && held==setup_calls);
 return ret;
}
'''
        (folder / 'supplier.c').write_text(fixture)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror', str(folder / 'supplier.c'),
                        '-o', str(folder / 'supplier.so')], check=True)
        native = C.CDLL(str(folder / 'supplier.so'))
        for fault, error in {0:0,1:-22,2:-22,3:-517,4:-517,5:-517,6:-12,7:-16}.items():
            assert native.run(fault) == error
            assert native.probe(fault,0) == error
        assert native.probe(0,-5) == -5
        cases = []
        for name in ('cold-0', 'cold-1', 'warm', 'probing', 'no-fpga', 'p6-fail', 'module-fail', 'load-fail', 'miniolt-fail'):
            case = folder / name; case.mkdir()
            ni = case / 'sys/class/net/fttr0/device'; ni.mkdir(parents=True)
            spi = case / 'sys/bus/spi/devices/spi0.0'; spi.mkdir(parents=True)
            mdio = case / 'sys/bus/mdio_bus/devices/bus:0f'; mdio.mkdir(parents=True)
            fpga_driver = case / 'driver'; fpga_driver.mkdir()
            for attr in ('fttr_accel','fttr_link'): (ni / attr).write_text('')
            if name != 'no-fpga':
                (spi / 'of_node').mkdir(); (spi / 'of_node/compatible').write_bytes(b'pango,tyfza1-pg2l100h\0')
            if name == 'warm':
                (spi / 'driver').symlink_to(fpga_driver); (mdio / 'backend').write_text('fpga1\n')
                (spi / 'firmware').touch()
            if name == 'probing': (spi / 'driver').symlink_to(fpga_driver)
            if name == 'p6-fail':
                (ni / 'fttr_link').unlink(); (ni / 'fttr_link').mkdir()
            log = case / 'commands'; log.touch()
            # Redirect the production sysfs paths, retaining shell branching
            # and write order. External ip/modprobe and probe completion are modeled.
            run_script = script.replace('/sys/', str(case / 'sys') + '/')
            prefix = f'''
echo() {{
 if [ "$*" = spi0.0 ]; then
  printf 'load\\n' >> '{log}'
  [ "{name}" != load-fail ] || return 1
  [ -L '{spi}/driver' ] || ln -s '{fpga_driver}' '{spi}/driver'
  touch '{spi}/firmware'
  [ "{name}" = miniolt-fail ] || printf 'fpga1\\n' > '{mdio}/backend'
 else
  printf '%s\\n' "$*" >> '{log}'
  printf '%s\\n' "$*"
 fi
}}
ip() {{ printf 'ip %s\\n' "$*" >> '{log}'; }}
modprobe() {{ printf 'modprobe %s\\n' "$*" >> '{log}'; [ "{name}" != module-fail ]; }}
sleep() {{ :; }}
'''
            nway = 0 if name == 'cold-0' else 1
            result = subprocess.run(['bash', '-c', run_script + prefix + f'fpga_start {nway}'], capture_output=True, text=True)
            events = log.read_text().splitlines()
            if name.startswith('cold') or name=='probing':
                assert result.returncode == 0, (name, result.stderr, events)
                assert events == ['ip link set dev fttr0 down','0',f'5 {nway}','modprobe tyfza1-pg2l100h','load']
                assert (ni / 'fttr_link').read_text() == f'5 {nway}\n'
            elif name in ('warm','no-fpga'):
                assert result.returncode == 0 and not events
            else:
                assert result.returncode != 0, (name, events)
                if name == 'p6-fail': assert not any(e.startswith('modprobe') or e=='load' for e in events)
                else: assert events[-1] == '0 0' and (ni / 'fttr_link').read_text() == '0 0\n'
            cases.append({'case': name, 'expected_order_and_exit': True})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'init_cases': cases, 'supplier_reference_errors_checked': True,
        'scope': 'Production shell and supplier function with sysfs/probe/IP boundaries modeled; no physical load.'}, indent=2) + '\n')
    print('FPGA preload: factory NWAY precedes load; warm start, failures and NI supplier references passed')


if __name__ == '__main__':
    main()
