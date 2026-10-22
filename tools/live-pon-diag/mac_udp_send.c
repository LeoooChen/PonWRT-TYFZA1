// SPDX-License-Identifier: GPL-2.0-only
/* Send sequenced, fully verifiable UDP through the test Wi-Fi socket. */
#include <arpa/inet.h>
#include <mach/mach_time.h>
#include <errno.h>
#include <net/if.h>
#include <sys/socket.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
/* Keep sub-microsecond packet deadlines on the native Mach timebase. */
static double seconds_per_tick;
static double now(void) {
 return mach_absolute_time()*seconds_per_tick;
}

int main(int argc,char **argv) {
 if(argc!=3)return 1;
 mach_timebase_info_data_t clock_info;mach_timebase_info(&clock_info);
 seconds_per_tick=(double)clock_info.numer/clock_info.denom/1e9;
 unsigned int rate=strtoul(argv[1],NULL,10),count=strtoul(argv[2],NULL,10),sent=0;
 unsigned char packet[1464];memcpy(packet,"TYFPPP-SIM",10);
 for(unsigned int i=14;i<sizeof(packet);i++)packet[i]=(i+50)*31+7;
 int fd=socket(AF_INET,SOCK_DGRAM,0),iface=if_nametoindex("en0"),buffer=4*1024*1024;
 struct sockaddr_in local={.sin_family=AF_INET,.sin_port=htons(55000)};
 struct sockaddr_in peer={.sin_family=AF_INET,.sin_port=htons(55001)};
 inet_pton(AF_INET,"192.168.1.212",&local.sin_addr);
 inet_pton(AF_INET,"198.19.0.1",&peer.sin_addr);
 if(fd<0||setsockopt(fd,IPPROTO_IP,IP_BOUND_IF,&iface,sizeof(iface))||
    bind(fd,(void *)&local,sizeof(local))) {
  perror("UDP setup");return 1;
 }
 int br=setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&buffer,sizeof(buffer));
 socklen_t bl=sizeof(buffer);getsockopt(fd,SOL_SOCKET,SO_SNDBUF,&buffer,&bl);
 unsigned int no_buffer=0;
 double started=now(),due=started;
 for(;sent<count;sent++) {
  uint32_t sequence=htonl(sent);memcpy(packet+10,&sequence,4);
  double attempted;
  while((attempted=now())<due){}
  ssize_t n;
  for(;;) {
   n=sendto(fd,packet,sizeof(packet),0,(void *)&peer,sizeof(peer));
   if(n>=0||errno!=ENOBUFS)break;
   no_buffer++;struct timespec pause={.tv_nsec=100000};nanosleep(&pause,NULL);
   attempted=now();
  }
  if(n!=(ssize_t)sizeof(packet)){fprintf(stderr,"send_return=%zd errno=%d\n",n,errno);perror("UDP send");break;}
  /* A stalled send must not turn into a catch-up burst. */
  due=attempted+sizeof(packet)*8.0/(rate*1e6);
 }
 while(now()<due){}
 double elapsed=now()-started;
 printf("{\"requested\":%u,\"sent\":%u,\"payload_bytes\":%llu,\"target_Mbps\":%u,\"seconds\":%.6f,\"offered_payload_Mbps\":%.3f,\"ENOBUFS_retries\":%u,\"send_buffer_result\":%d,\"send_buffer_bytes\":%d}\n",
 count,sent,(unsigned long long)sent*sizeof(packet),rate,elapsed,sent*sizeof(packet)*8/elapsed/1e6,no_buffer,br,buffer);
 close(fd);return sent==count?0:1;
}
