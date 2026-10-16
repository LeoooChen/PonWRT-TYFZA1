#!/usr/bin/env python3
"""Exercise the built hostapd ACS callbacks with unsupported survey collection."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
sources = list((root / 'sources/openwrt/build_dir').glob('target-*/hostapd-*/hostapd-*/src/ap/acs.c'))
assert len(sources) == 1, 'Prepare the selected hostapd package first'
source = sources[0].read_text()


def function(name):
    start = source.index('static ' + ('int ' if name == 'acs_study_options' else 'void ') + name + '(')
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#define MSG_DEBUG 0
#define MSG_INFO 1
#define MSG_ERROR 2
#define wpa_printf(...) ((void)0)
struct hostapd_config { unsigned acs_num_scans; };
struct hostapd_iface {
 void *scan_cb; unsigned acs_num_retries,acs_num_completed_scans;
 struct hostapd_config *conf; void *bss[1];
};
static int survey_result,request_result,survey_study_result,bss_result;
static unsigned surveys,requests,studies,bss_studies,failures,completions;
static int hostapd_drv_get_survey(void *bss,unsigned freq) {
 (void)bss;assert(freq==0);surveys++;return survey_result;
}
static int acs_request_scan(struct hostapd_iface *iface) {
 (void)iface;requests++;return request_result;
}
static void hostapd_acs_completed(struct hostapd_iface *iface,int error) {
 (void)iface;assert(error==1);completions++;
}
static void acs_fail(struct hostapd_iface *iface) {(void)iface;failures++;}
static int acs_study_survey_based(struct hostapd_iface *iface) {
 (void)iface;return survey_study_result;
}
static int acs_study_bss_based(struct hostapd_iface *iface) {
 (void)iface;bss_studies++;return bss_result;
}
'''
model += function('acs_study_options')
model += r'''
static void acs_study(struct hostapd_iface *iface) {
 studies++;if(acs_study_options(iface)<0)acs_fail(iface);
}
'''
model += function('acs_scan_complete')
model += r'''
static void reset(void) {
 survey_result=request_result=survey_study_result=bss_result=0;
 surveys=requests=studies=bss_studies=failures=completions=0;
}
int main(void) {
 struct hostapd_config config={.acs_num_scans=3};
 struct hostapd_iface iface={.conf=&config};
 /* The native radio has no get_survey callback: still finish all scans. */
 reset();survey_result=-EOPNOTSUPP;survey_study_result=-1;
 for(unsigned i=0;i<3;i++) {
  iface.scan_cb=&iface;iface.acs_num_retries=2;
  acs_scan_complete(&iface);
  assert(!failures&&!completions&&!iface.scan_cb&&!iface.acs_num_retries);
 }
 assert(surveys==3&&requests==2&&studies==1&&bss_studies==1);
 /* Successful survey collection must keep the existing survey algorithm. */
 reset();config.acs_num_scans=1;iface.acs_num_completed_scans=0;
 acs_scan_complete(&iface);
 assert(studies==1&&!bss_studies&&!failures);
 /* Empty surveys already fall back to BSS; failed BSS studies still fail. */
 reset();survey_study_result=-1;acs_scan_complete(&iface);
 assert(studies==1&&bss_studies==1&&!failures);
 reset();survey_result=-EOPNOTSUPP;survey_study_result=bss_result=-1;
 acs_scan_complete(&iface);assert(studies==1&&bss_studies==1&&failures==1);
 /* Do not hide device removal, I/O, invalid requests or allocation errors. */
 int errors[]={-ENODEV,-EIO,-EINVAL,-ENOBUFS};
 for(unsigned i=0;i<sizeof(errors)/sizeof(errors[0]);i++) {
  reset();survey_result=errors[i];acs_scan_complete(&iface);
  assert(failures==1&&completions==1&&!studies&&!requests);
 }
 reset();config.acs_num_scans=3;iface.acs_num_completed_scans=0;
 survey_result=-EOPNOTSUPP;request_result=-EBUSY;acs_scan_complete(&iface);
 assert(requests==1&&!failures&&!studies);
 reset();iface.acs_num_completed_scans=0;
 survey_result=-EOPNOTSUPP;request_result=-EIO;acs_scan_complete(&iface);
 assert(requests==1&&failures==1&&completions==1&&!studies);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='hostapd-acs-') as directory:
    directory = Path(directory)
    test = directory / 'test.c'
    test.write_text(model)
    binary = directory / 'test'
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    str(test), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Actual hostapd ACS callbacks: unsupported survey fallback and error boundaries passed; no hardware test')
