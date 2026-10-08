#!/usr/bin/env python3
"""Check capture failures and actual probe LLID metadata without network traffic."""
import json
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
checks = []
probe_model = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
static unsigned int submitted;
int model_socket(int domain,int type,int protocol) {(void)protocol;assert(domain==AF_PACKET&&type==SOCK_RAW);return 42;}
int model_ioctl(int fd,unsigned long request,...) {
    va_list ap;va_start(ap,request);struct ifreq *interface=va_arg(ap,struct ifreq *);va_end(ap);
    assert(fd==42&&!strcmp(interface->ifr_name,"renamed-oam"));
    if(request==SIOCGIFINDEX)interface->ifr_ifindex=7;
    else {assert(request==SIOCGIFHWADDR);memcpy(interface->ifr_hwaddr.sa_data,"\2\1\2\3\4\5",6);}
    return 0;
}
FILE *model_fopen(const char *path,const char *mode) {
    assert(!strcmp(path,"/sys/class/net/renamed-oam/llid_index")&&!strcmp(mode,"r"));
    const char *index="7\n";FILE *file=tmpfile();assert(file);fputs(index,file);rewind(file);return file;
}
int model_bind(int fd,const struct sockaddr *address,socklen_t length) {
    assert(fd==42&&length==sizeof(struct sockaddr_ll));assert(((const struct sockaddr_ll *)address)->sll_ifindex==7);return 0;
}
ssize_t model_send(int fd,const void *data,size_t length,int flags) {
    (void)data;(void)flags;assert(fd==42&&length==60);submitted++;return length;
}
int model_close(int fd) {assert(fd==42);assert(submitted==3);return 0;}
'''

with tempfile.TemporaryDirectory(prefix='capture-probe-test-', dir=root / 'artifacts') as temporary:
    temporary = Path(temporary)
    capture = temporary / 'capture'
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(root / 'tools/lan_capture.c'), '-o', str(capture)], check=True)
    output = temporary / 'wrong-interface.pcap'
    result = subprocess.run([str(capture), 'nonexistent-qm-interface', str(output)], capture_output=True, text=True)
    assert result.returncode and 'if_nametoindex' in result.stderr and not output.exists()
    checks.append('Actual capture rejects missing interface before socket/output creation')
    probe = temporary / 'probe'
    harness = temporary / 'probe-model.c'
    harness.write_text(probe_model)
    flags = ['-Dsocket=model_socket', '-Dioctl=model_ioctl', '-Dfopen=model_fopen',
             '-Dbind=model_bind', '-Dsend=model_send', '-Dclose=model_close']
    production = temporary / 'probe.o'
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', *flags, '-c', str(root / 'tools/pon_oam_probe.c'), '-o', str(production)], check=True)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(harness), str(production), '-o', str(probe)], check=True)
    result = subprocess.run([str(probe), 'renamed-oam', '3'], capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report['LLID_index'] == 7 and report['submitted'] == 3
    checks.append('Actual probe reports driver LLID7 even when interface name is changed')
    legacy = subprocess.run([str(probe), 'pon-oam0', '7', '3'], capture_output=True, text=True)
    assert legacy.returncode and 'usage:' in legacy.stderr
    checks.append('Old conflicting interface-plus-LLID argument is rejected before opening socket')
(root / 'logs/capture-probe-tests.json').write_text(json.dumps({'passed':checks,'network_traffic_generated':False},indent=2)+'\n')
print(f'{len(checks)} actual capture/probe checks passed')
