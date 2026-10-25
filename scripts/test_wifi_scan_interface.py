#!/usr/bin/env python3
"""Run iwinfo's temporary-interface selection against advertised PHY modes."""
from pathlib import Path
import resource
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
archives = list((root / 'sources/openwrt/dl').glob('libiwinfo-*.tar.zst'))
assert len(archives) == 1


def function(source, name):
    at = source.index(name + '(')
    start = source.rfind('\n', 0, at) + 1
    end = source.index('{', at) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include <net/if.h>
#include <linux/nl80211.h>
#define NL_SKIP 2
struct nlattr { int type; };
struct nl_msg { int cmd, iftype; };
struct nl80211_msg_conveyor { struct nl_msg *msg; };
static struct nl_msg message;
static struct nl80211_msg_conveyor conveyor = { &message };
static struct nlattr modes[4], nested;
static struct nlattr *attrs[NL80211_ATTR_MAX + 1];
static int mode_count, live, selected, queries;
static void *nla_data(struct nlattr *attr) { assert(attr == &nested); return modes; }
static int nla_len(struct nlattr *attr) { assert(attr == &nested); return mode_count * sizeof(*modes); }
static struct nlattr *nla_find(struct nlattr *data, int bytes, int type) {
 for (int i = 0; i < bytes / (int)sizeof(*data); i++)
  if (data[i].type == type) return &data[i];
 return NULL;
}
static struct nlattr **nl80211_parse(struct nl_msg *msg) { (void)msg; return attrs; }
static int nl80211_request(const char *name, int cmd, int flags,
 int (*callback)(struct nl_msg *, void *), void *data) {
 assert(!strcmp(name, "radio0") && cmd == NL80211_CMD_GET_WIPHY && !flags);
 /* Queries and interface creation share iwinfo's static conveyor. */
 assert(!live); queries++; return callback(&message, data) == NL_SKIP ? 0 : -1;
}
static struct nl80211_msg_conveyor *nl80211_msg(const char *name, int cmd, int flags) {
 assert(!strcmp(name, "radio0") && cmd == NL80211_CMD_NEW_INTERFACE && !flags && !live);
 live = 1; message = (struct nl_msg){ .cmd = cmd }; return &conveyor;
}
static void nl80211_free(struct nl80211_msg_conveyor *req) {
 assert(req == &conveyor && live); live = 0;
}
static int nl80211_send(struct nl80211_msg_conveyor *req, void *cb, void *data) {
 (void)cb; (void)data; selected = req->msg->iftype; nl80211_free(req); return 0;
}
#define NLA_PUT_STRING(msg, attr, val) do { \
 assert((attr) == NL80211_ATTR_IFNAME && !strcmp((val), "tmp.radio0")); \
 if (!(msg)) goto nla_put_failure; \
} while (0)
#define NLA_PUT_U32(msg, attr, val) do { \
 assert((attr) == NL80211_ATTR_IFTYPE); (msg)->iftype = (val); \
} while (0)
/* No real network interface or procfs writes in the offline test. */
static FILE *test_fopen(const char *path, const char *mode) {
 assert(strstr(path, "/tmp.radio0/disable_ipv6") && !strcmp(mode, "w")); return NULL;
}
#define fopen test_fopen
'''
checks = r'''
static void check(int a, int b, int expected) {
 modes[0].type = a; modes[1].type = b; mode_count = 2;
 attrs[NL80211_ATTR_SUPPORTED_IFTYPES] = &nested; queries = 0;
 assert(!strcmp(nl80211_ifadd("radio0"), "tmp.radio0"));
 assert(selected == expected && !live && queries == 1);
}
int main(void) {
 /* Both TYFZA1 radios advertise AP + monitor, without station support. */
 check(NL80211_IFTYPE_AP, NL80211_IFTYPE_MONITOR, NL80211_IFTYPE_AP);
 check(NL80211_IFTYPE_STATION, NL80211_IFTYPE_AP, NL80211_IFTYPE_STATION);
 check(NL80211_IFTYPE_MONITOR, NL80211_IFTYPE_STATION, NL80211_IFTYPE_STATION);
 puts("iwinfo: AP-only PHY uses temporary AP; station-capable PHY keeps STA; query precedes allocation");
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='tyfza1-scan-interface-') as folder:
    folder = Path(folder)
    subprocess.run(['tar', '-xf', str(archives[0]), '-C', str(folder),
                    '--strip-components=1'], check=True)
    for patch in sorted((root / 'port/host-patches').glob('*iwinfo*.patch')):
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                       cwd=folder, check=True, stdout=subprocess.PIPE)
    source = (folder / 'iwinfo_nl80211.c').read_text()
    callback = 'nl80211_get_scan_iftype_cb'
    code = model
    if callback + '(' in source:
        code += function(source, callback)
    code += function(source, 'nl80211_ifadd') + checks
    (folder / 'test.c').write_text(code)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fsanitize=address,undefined', str(folder / 'test.c'),
                    '-o', str(folder / 'test')], check=True)
    subprocess.run([str(folder / 'test')], check=True)
