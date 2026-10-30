#!/usr/bin/env python3
"""Compile actual patched iperf3 GRO receive functions; run on Linux."""
from pathlib import Path
import subprocess
import sys
import tempfile

source = (Path(sys.argv[1]) / 'src/net.c').read_text()
start = source.index('static int recv_msg_gro(')
end = source.index('\n#else', start)
fixture = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <netinet/udp.h>
#include <time.h>
#include <unistd.h>
#define NET_HARDERROR -2
#define MAX_UDP_BLOCKSIZE 65507
'''
checks = r'''
static int rx, result, segment;
static char buffer[2048];
static double cpu_seconds;
static void *receive(void *unused) {
 struct timespec a,b;
 clock_gettime(CLOCK_THREAD_CPUTIME_ID,&a);
 result=Nread_gro(rx,buffer,sizeof(buffer),0,&segment);
 clock_gettime(CLOCK_THREAD_CPUTIME_ID,&b);
 cpu_seconds=b.tv_sec-a.tv_sec+(b.tv_nsec-a.tv_nsec)/1e9;
 return NULL;
}
int main(void) {
 struct sockaddr_in addr={.sin_family=AF_INET,.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
 socklen_t len=sizeof(addr);
 int tx=socket(AF_INET,SOCK_DGRAM,0),one=1;
 rx=socket(AF_INET,SOCK_DGRAM,0);
 assert(rx>=0 && tx>=0 && !bind(rx,(void *)&addr,sizeof(addr)));
 assert(!getsockname(rx,(void *)&addr,&len));
 assert(!connect(tx,(void *)&addr,len));
 assert(!setsockopt(rx,IPPROTO_UDP,UDP_GRO,&one,sizeof(one)));
 pthread_t worker;
 assert(!pthread_create(&worker,NULL,receive,NULL));
 usleep(150000);
 assert(send(tx,"payload",7,0)==7);
 assert(!pthread_join(worker,NULL));
 assert(result==7 && segment==-1 && !memcmp(buffer,"payload",7));
 assert(cpu_seconds<0.03);

 /* Kernel UDP GSO/GRO: preserve every segment and the ancillary size. */
 char data[384],control[CMSG_SPACE(sizeof(uint16_t))]={0};
 for(unsigned i=0;i<sizeof(data);i++)data[i]=(char)i;
 struct iovec iov={.iov_base=data,.iov_len=sizeof(data)};
 struct msghdr msg={.msg_iov=&iov,.msg_iovlen=1,
  .msg_control=control,.msg_controllen=sizeof(control)};
 struct cmsghdr *c=CMSG_FIRSTHDR(&msg);
 c->cmsg_level=IPPROTO_UDP;c->cmsg_type=UDP_SEGMENT;
 c->cmsg_len=CMSG_LEN(sizeof(uint16_t));
 *(uint16_t *)CMSG_DATA(c)=128;
 assert(sendmsg(tx,&msg,0)==sizeof(data));
 assert(Nread_gro(rx,buffer,sizeof(buffer),0,&segment)==sizeof(data));
 assert(segment==128 && !memcmp(buffer,data,sizeof(data)));

 /* Cancellation must stop an idle stream without waiting for a packet. */
 assert(!pthread_create(&worker,NULL,receive,NULL));
 usleep(50000);
 assert(!pthread_cancel(worker));
 void *status;
 assert(!pthread_join(worker,&status) && status==PTHREAD_CANCELED);
 struct timeval timeout={.tv_usec=50000};
 assert(!setsockopt(rx,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout)));
 assert(Nread_gro(rx,buffer,sizeof(buffer),0,&segment)==0);
 close(rx);close(tx);
 puts("GRO: idle wait, payload/segments, cancellation, socket timeout PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='iperf-gro-') as directory:
    p = Path(directory)
    (p / 'test.c').write_text(fixture + source[start:end] + checks)
    subprocess.run(['cc', '-O2', '-pthread', '-Wall', str(p / 'test.c'),
                    '-o', str(p / 'test')], check=True)
    subprocess.run([str(p / 'test')], check=True, timeout=5)
