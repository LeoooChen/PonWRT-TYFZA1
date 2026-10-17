#!/usr/bin/env python3
"""Exercise native bootstrap with modeled sysfs callbacks in a private net namespace."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parents[1]

shim = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
ssize_t write(int fd,const void *buffer,size_t count) {
 static ssize_t (*real_write)(int,const void *,size_t);
 char reference[64],path[PATH_MAX],command[256],show[256];
 const char *prefix=getenv("FTTR_TEST_ROOT"),*bad=getenv("FTTR_TEST_BAD"),*name;
 ssize_t n,result;unsigned int mode,nway;int log;
 if(!real_write)real_write=dlsym(RTLD_NEXT,"write");
 snprintf(reference,sizeof(reference),"/proc/self/fd/%d",fd);
 n=readlink(reference,path,sizeof(path)-1);
 if(n<0 || !prefix)return real_write(fd,buffer,count);
 path[n]=0;
 if(strncmp(path,prefix,strlen(prefix)))return real_write(fd,buffer,count);
 name=strrchr(path,'/');name=name ? name+1 : path;
 if(strcmp(name,"tx_enable") && strcmp(name,"reset") && strcmp(name,"calibration") &&
    strcmp(name,"host_serdes") && strcmp(name,"fttr_link") && strcmp(name,"pon_serdes") &&
    strcmp(name,"core_init") && strcmp(name,"omci_link") && strcmp(name,"dba"))
  return real_write(fd,buffer,count);
 log=open(getenv("FTTR_TEST_LOG"),O_WRONLY|O_CREAT|O_APPEND,0600);
 real_write(log,name,strlen(name));real_write(log," ",1);real_write(log,buffer,count);close(log);
 if(bad && !strcmp(bad,"core") && !strcmp(name,"core_init")){errno=EIO;return -1;}
 result=real_write(fd,buffer,count);if(result<0)return result;
 n=count<sizeof(command)-1 ? count : sizeof(command)-1;memcpy(command,buffer,n);command[n]=0;
 show[0]=0;
 if(!strcmp(name,"calibration"))
  snprintf(show,sizeof(show),"configured=%u model=GN25L99 marker=%s writes=128 error=0\n",
   bad && !strcmp(bad,"marker") ? 0 : 1,bad && !strcmp(bad,"marker") ? "00" : "b0");
 if(!strcmp(name,"host_serdes")) {
  sscanf(command,"%u %u",&mode,&nway);
  snprintf(show,sizeof(show),"mode=%u nway=%u pll=1 error=0 link=%u\n",mode,nway,
   bad && !strcmp(bad,"link") ? 0 : 1);
 }
 if(!strcmp(name,"fttr_link")) {
  sscanf(command,"%u %u",&mode,&nway);
  snprintf(show,sizeof(show),"configured=%u mode=%u nway=%u clock=%u error=0 link=%u\n",
   !!mode,mode,nway,!!mode,mode && !(bad && !strcmp(bad,"link")));
 }
 if(!strcmp(name,"omci_link"))
  snprintf(show,sizeof(show),"host=%.17s olt=0a:0b:0c:0d:0e:0f ethertype=8888 downstream_length=0\n",command);
 if(show[0]){lseek(fd,0,SEEK_SET);ftruncate(fd,0);real_write(fd,show,strlen(show));}
 return result;
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--daemon',type=Path,required=True)
    parser.add_argument('--ucode',type=Path,required=True)
    parser.add_argument('--ucode-modules',type=Path,required=True)
    parser.add_argument('--factory',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assert {x['ifname'] for x in json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))}=={'lo'}
    subprocess.run(['ip','link','add','ftr-host','type','veth','peer','name','ftr-peer'],check=True)
    for interface,mac in [('ftr-host','02:03:04:05:06:07'),('ftr-peer','0a:0b:0c:0d:0e:0f')]:
        subprocess.run(['ip','link','set','dev',interface,'address',mac,'mtu','1982','up'],check=True)
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory);source=work/'factory';imported=work/'imported'
        source.mkdir();imported.mkdir()
        for name in ('config_hs.xml','config.xml','rtkbosa_k.bin','rtkoltbosa_k.bin'):
            if (args.factory/name).exists():shutil.copy2(args.factory/name,source/name)
        hs=source.joinpath('config_hs.xml').read_bytes();cs=source.joinpath('config.xml').read_bytes()
        def field(data,key):return re.search(rb'<Value\s+Name="'+key.encode()+rb'"\s+Value="([^"]*)"',data)[1].decode()
        expected=' '.join([field(cs,'FTTR_MASTER_PON_ENABLE'),field(hs,'EXTERNAL_VIRTUAL_RGMII_PORT_SERDES_MODE'),
            field(hs,'EXTERNAL_VIRTUAL_RGMII_PORT_SERDES_NWAY'),field(cs,'FTTR_MASTER_OLT_PON_SERDES_MODE')])+'\n'
        command=[str(args.ucode),'-L',str(args.ucode_modules),str(root/'port/packages/tyfza1-pon-control/files/factory.uc'),str(source),str(imported)]
        result=subprocess.run(command,capture_output=True,text=True)
        settings=imported/'tmp/tyfza1-factory/fttr_startup'
        assert settings.read_text()==expected and 'Factory FTTR startup:' not in result.stderr
        source.joinpath('config.xml').write_bytes(cs.replace(b'Name="FTTR_MASTER_PON_ENABLE"',b'Name="TEST_REMOVED_FTTR_ENABLE"'))
        subprocess.run(command,capture_output=True,text=True)
        assert not settings.exists()
        work.joinpath('sysfs.c').write_text(shim)
        subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror',str(work/'sysfs.c'),'-ldl','-o',str(work/'sysfs.so')],check=True)

        def wait(process,predicate,seconds=4):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                if predicate():return
                if process.poll() is not None:raise AssertionError(('bootstrap exited',process.communicate()[1]))
                time.sleep(0.01)
            raise AssertionError('bootstrap observation timeout')

        for bad in ('none','link','marker','core','stop-during-init'):
            case=work/bad;case.mkdir()
            miniolt,ni,optical,store=[case/n for n in ('miniolt','ni','optical','state')]
            for p in (miniolt,ni,optical,store):p.mkdir(mode=0o700)
            for name in ('tx_enable','reset','host_serdes','pon_serdes','core_init','omci_link','dba'):
                miniolt.joinpath(name).write_text('0\n')
            ni.joinpath('fttr_link').write_text('0 0\n');optical.joinpath('calibration').write_text('0\n')
            miniolt.joinpath('onu_table').write_text(''.join(f'{i} 00000000 00000000 00000000 0000000000000000\n' for i in range(64)))
            log=case/'commands';log.write_text('')
            env=dict(os.environ,LD_PRELOAD=str(work/'sysfs.so'),FTTR_TEST_ROOT=str(case),
                FTTR_TEST_LOG=str(log),FTTR_TEST_BAD=bad)
            argv=[str(args.daemon.resolve()),str(miniolt),str(ni),str(store),'ftr-host',str(optical),'9','1','3']
            process=subprocess.Popen(argv,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
            def state():
                try:return json.loads(store.joinpath('status.json').read_text())
                except FileNotFoundError:return {}
            try:
                if bad=='none':
                    wait(process,lambda:state().get('state')=='running')
                    expected_commands=['tx_enable 0','fttr_link 0 0','reset 1','calibration 1','host_serdes 9 1',
                        'fttr_link 9 1','pon_serdes 3','core_init 1','omci_link 02:03:04:05:06:07','dba 1','tx_enable 1']
                    assert log.read_text().splitlines()==expected_commands
                    replacement=subprocess.Popen(argv,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
                    try:
                        wait(replacement,lambda:'locks' in Path(f'/proc/{replacement.pid}/wchan').read_text())
                        process.send_signal(signal.SIGTERM);_,errors=process.communicate(timeout=10)
                        assert process.returncode==0,errors
                        wait(replacement,lambda:state().get('state')=='running' and state().get('pid')==replacement.pid)
                        assert log.read_text().splitlines().count('reset 1')==2
                        replacement.send_signal(signal.SIGTERM);_,errors=replacement.communicate(timeout=10)
                        assert replacement.returncode==0,errors
                    finally:
                        if replacement.poll() is None:replacement.terminate();replacement.communicate(timeout=10)
                elif bad=='stop-during-init':
                    wait(process,lambda:'pon_serdes 3\n' in log.read_text())
                    process.send_signal(signal.SIGTERM);_,errors=process.communicate(timeout=10)
                    assert process.returncode==0,errors
                    assert 'core_init 1' not in log.read_text()
                else:
                    _,errors=process.communicate(timeout=5)
                    assert process.returncode==1,errors
                    assert state()['error']==(-110 if bad=='link' else -5)
                    assert 'tx_enable 1' not in log.read_text().splitlines()
                assert miniolt.joinpath('tx_enable').read_text()=='0\n'
                assert ni.joinpath('fttr_link').read_text().startswith('configured=0 ')
                assert state()['state'] in ('stopped','failed') and state()['pid']==0
                flags=json.loads(subprocess.check_output(['ip','-j','link','show','dev','ftr-host'],text=True))[0]['flags']
                assert 'UP' not in flags
            finally:
                if process.poll() is None:process.terminate();process.communicate(timeout=10)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'factory_parameters_imported':True,'stale_parameters_revoked':True,
        'native_bootstrap_command_order':True,'link_marker_core_errors_stop_before_tx':True,
        'stop_during_init_closes_hardware':True,'restart_waits_for_previous_owner':True,
        'real_netdev_ioctls_in_private_namespace':True,'modeled_hardware':'sysfs write/show callbacks',
        'optical_hardware_verified':False},indent=2)+'\n')
    print('Native startup: factory import, IO order, failure cleanup and controller replacement passed')


if __name__=='__main__':main()
