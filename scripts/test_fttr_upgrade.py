#!/usr/bin/env python3
"""Drive actual native image transactions against target factory wire producers."""
import argparse
import ctypes as C
import errno
from pathlib import Path
import struct
import subprocess
import tempfile
from trace_stock_fttr_upgrade import Upgrade

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--relay',type=Path,required=True);p.add_argument('--fpga-relay',type=Path,required=True)
a=p.parse_args()
shim=r'''
#include <errno.h>
#include <string.h>
#include "omci-upgrade.h"
#include "omci-socket.h"
static struct fttr_upgrade_job job;
static uint16_t counter=0x8000;
static unsigned char sent[44];
static unsigned int sends;
static int send_error;
ssize_t fttr_omci_send(int fd,const struct fttr_omci_link *link,uint16_t onu,const uint8_t *body,size_t size) {
 (void)fd;(void)link;(void)onu;if(send_error)return send_error;
 if(size!=44)return -EINVAL;
 memcpy(sent,body,44);sends++;return 44;
}
int fttr_omci_receive(int fd,uint16_t protocol,uint8_t *frame,size_t size,struct fttr_omci_message *message) {
 (void)fd;(void)protocol;(void)frame;(void)size;(void)message;return -EAGAIN;
}
int fttr_miniolt_same(int fd,unsigned int id,const struct fttr_onu_state *identity) {
 (void)fd;(void)id;(void)identity;return -ESTALE;
}
int begin(int action,const void *data,size_t size){sends=0;send_error=0;return fttr_upgrade_start(&job,7,&counter,action,data,size);}
int transmit(long now){return fttr_upgrade_send(&job,&counter,-1,0,now);}
int reply(const unsigned char *body,int size,long now){struct fttr_omci_message m={.onu=7,.body=body,.length=size};return fttr_upgrade_reply(&job,&counter,&m,now);}
int tick(long now){return fttr_upgrade_tick(&job,&counter,now);}
void cancel(void){fttr_upgrade_cancel(&job,-ESTALE);}
void io_fail(void){send_error=-EIO;}
const void *frame(void){return sent;}
int state(void){return job.stage;}
int waiting(void){return job.waiting;}
int confirmed(void){return job.confirmed;}
int verified(void){return job.verified;}
int accepted(void){return job.accepted;}
int count(void){return sends;}
int error(void){return job.error;}
'''
with tempfile.TemporaryDirectory() as d:
    d=Path(d);(d/'shim.c').write_text(shim)
    src=ROOT/'port/packages/tyfza1-fttr/src'
    subprocess.run(['cc','-shared','-fPIC','-O2','-std=c11','-Wall','-Wextra','-Werror','-ffunction-sections','-Wl,--gc-sections','-I'+str(src),str(d/'shim.c'),str(src/'omci-upgrade.c'),str(src/'omci-me.c'),str(src/'omci-wire.c'),'-o',str(d/'native.so')],check=True)
    lib=C.CDLL(str(d/'native.so'));lib.frame.restype=C.c_void_p
    lib.begin.argtypes=[C.c_int,C.c_void_p,C.c_size_t]
    def frame():return C.string_at(lib.frame(),44)
    def response(status=0,payload=b'',now=0,wrong=False):
        f=bytearray(frame());f[2]=(f[2]&31)|32;f[8:40]=(bytes([status])+payload).ljust(32,b'\0')
        if wrong:f[1]^=1
        return lib.reply(bytes(f),44,now)
    def image_reply(committed,active,valid):return response(payload=b'\xf0\0'+b'VERSION0000001'+bytes((committed,active,valid)))
    def select(action,data=None,rows=((1,1,1),(0,0,0))):
        assert lib.begin(action,data,len(data) if data else 0)==0
        for row in rows:
            assert lib.transmit(0)==0;assert frame()[2:10] in (bytes.fromhex('490a00070000f000'),bytes.fromhex('490a00070001f000'))
            ret=image_reply(*row)
        return ret
    checks=0
    for binary in (a.relay,a.fpga_relay):
        stock=Upgrade(binary)
        for size in (1,30,31,32,62,63,960,961,962,1923):
            data=bytes(i%251 for i in range(size));assert select(0,data)==1
            assert lib.transmit(0)==0
            assert frame()[2:]==stock.request('start',size=size)[2:44]
            assert response(payload=b'\x1e')==1
            offset=0
            while offset<size:
                ret,frames,row=stock.window(data,offset)
                for expected in frames:
                    assert lib.transmit(0)==0;assert frame()[2:]==expected[2:44]
                    assert lib.confirmed()==offset
                assert lib.waiting()
                assert response(payload=bytes([len(frames)-1]),wrong=True)==0
                assert response(payload=bytes([len(frames)-1]))==1
                offset=min(size,offset+961);assert lib.confirmed()==offset
            assert lib.transmit(0)==0
            crc=stock.oracle.call('plr_gponrm_ver_crc32_get',stock.base+0x1000,size)
            assert frame()[2:]==stock.request('end',size=size,crc=crc)[2:44]
            assert response()==1 and lib.accepted() and not lib.verified()
            assert lib.transmit(0)==0;assert image_reply(0,0,1)==1
            assert lib.state()==9 and lib.verified();checks+=1
        for action,name,rows in ((1,'activate',((1,1,1),(0,0,1))), (2,'commit',((1,0,1),(0,1,1)))):
            assert select(action,rows=rows)==1;assert lib.transmit(0)==0
            assert frame()[2:]==stock.request(name)[2:44]
            assert response()==1
            if action==2:
                assert lib.transmit(0)==0;assert image_reply(1,1,1)==1 and lib.verified()
            else:assert lib.accepted() and not lib.verified()
            assert lib.state()==9;checks+=1
    data=b'x'*100
    # Rejected window retransmits the complete same offset, at most twice.
    assert select(0,data)==1;lib.transmit(0);response(payload=b'\x01')
    for attempt in range(3):
        for section in range(2):lib.transmit(0);assert frame()[8]==section
        ret=response(status=2)
        assert not lib.confirmed();assert ret==(1 if attempt<2 else -errno.EREMOTEIO)
    checks+=1
    # Invalid negotiation, confirmation or current image never advances.
    for win in (31,255):
        select(0,data);lib.transmit(0);assert response(payload=bytes([win]))==-errno.EPROTO
    select(0,data);lib.transmit(0);response(payload=b'\0');lib.transmit(0)
    assert response(payload=b'\1')==-errno.EPROTO and not lib.confirmed()
    assert select(0,data,((1,1,1),(1,0,1)))==-errno.ENOENT
    assert select(0,data,((2,1,1),(0,0,0)))==-errno.EPROTO;checks+=5
    # Every legal negotiated window, including short final windows, preserves bytes.
    for width in range(1,32):
        data=bytes(i%251 for i in range(1000))
        select(0,data);lib.transmit(0);response(payload=bytes([width-1]))
        while lib.confirmed()<len(data):
            offset=lib.confirmed();n=min(width,(len(data)-offset+30)//31)
            for section in range(n):
                assert lib.transmit(0)==0
                assert frame()[8]==section
                assert frame()[9:40]==data[offset+section*31:offset+(section+1)*31].ljust(31,b'\0')
            assert response(payload=bytes([n-1]))==1
        checks+=1
    # Confirmed download with invalid software-image readback must fail.
    select(0,b'x');lib.transmit(0);response(payload=b'\0');lib.transmit(0);response(payload=b'\0')
    lib.transmit(0);response();lib.transmit(0);assert image_reply(0,0,0)==-errno.EUCLEAN
    # Header mismatch cannot confirm a window, even with the expected TCI.
    select(0,b'x');lib.transmit(0);response(payload=b'\0');lib.transmit(0)
    f=bytearray(frame());f[2]=0x34;f[7]=0;f[8:10]=b'\0\0'
    assert lib.reply(bytes(f),44,0)==-errno.EPROTO and not lib.confirmed()
    checks+=2
    # End rejection waits4s; the120th retry is the last allowed transmission.
    select(0,b'x');lib.transmit(0);response(payload=b'\0');lib.transmit(0);response(payload=b'\0');lib.transmit(0)
    for retry in range(120):
        now=retry*4000;assert response(status=6,now=now)==1
        old=lib.count();assert lib.tick(now+3999)==0;lib.transmit(now+3999);assert lib.count()==old
        assert lib.tick(now+4000)==1;assert lib.transmit(now+4000)==0
    assert response(status=6,now=480000)==-errno.EREMOTEIO;checks+=1
    for action in (0,1,2):
        select(action,data if action==0 else None,((1,1,1),(0,0,1)))
        lib.transmit(0);old=lib.count();assert lib.tick(29999)==0
        assert lib.tick(30000)==-errno.ETIMEDOUT;lib.transmit(40000);assert lib.count()==old
        checks+=1
    select(0,data);lib.io_fail();assert lib.transmit(0)==-errno.EIO
    select(0,data);lib.cancel();assert lib.transmit(0)==-errno.ESTALE
    assert lib.begin(0,None,1)==-errno.EINVAL and lib.begin(1,data,len(data))==-errno.EINVAL
    print(f'Native upgrade: {checks+3} factory wire, complete image, retry, timeout and policy scenarios passed')
