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
#define BATCH 8
/* Keep sub-microsecond packet deadlines on the native Mach timebase. */
static double seconds_per_tick;
static double now(void) {
 return mach_absolute_time()*seconds_per_tick;
}

int main(int argc,char **argv) {
 if(argc!=3 && argc!=8){
  fprintf(stderr,"usage: mac_udp_send Mbps packets [interface source_IP destination_IP source_port destination_port]\n");
  return 1;
 }
 mach_timebase_info_data_t clock_info;mach_timebase_info(&clock_info);
 seconds_per_tick=(double)clock_info.numer/clock_info.denom/1e9;
 unsigned int rate=strtoul(argv[1],NULL,10),count=strtoul(argv[2],NULL,10),sent=0;
 unsigned char packet[1464];memcpy(packet,"TYFPPP-SIM",10);
 for(unsigned int i=14;i<sizeof(packet);i++)packet[i]=(i+50)*31+7;
 const char *interface=argc==8?argv[3]:"en0";
 int fd=socket(AF_INET,SOCK_DGRAM,0),iface=if_nametoindex(interface),buffer=4*1024*1024;
 struct sockaddr_in local={.sin_family=AF_INET,.sin_port=htons(argc==8?strtoul(argv[6],NULL,10):55000)};
 struct sockaddr_in peer={.sin_family=AF_INET,.sin_port=htons(argc==8?strtoul(argv[7],NULL,10):55001)};
 inet_pton(AF_INET,argc==8?argv[4]:"192.168.1.212",&local.sin_addr);
 inet_pton(AF_INET,argc==8?argv[5]:"198.19.0.1",&peer.sin_addr);
 if(fd<0||!iface||setsockopt(fd,IPPROTO_IP,IP_BOUND_IF,&iface,sizeof(iface))||
    bind(fd,(void *)&local,sizeof(local))) {
  perror("UDP setup");return 1;
 }
 int br=setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&buffer,sizeof(buffer));
 socklen_t bl=sizeof(buffer);getsockopt(fd,SOL_SOCKET,SO_SNDBUF,&buffer,&bl);
 unsigned int no_buffer=0;
 struct timespec common_start,common_end;
 clock_gettime(CLOCK_MONOTONIC,&common_start);
 double started=now(),due=started;
 while(sent<count) {
  unsigned int batch=count-sent<BATCH?count-sent:BATCH,done=0;
  double attempted;
  while((attempted=now())<due){}
  while(done<batch) {
   uint32_t sequence=htonl(sent);memcpy(packet+10,&sequence,4);
   ssize_t n=sendto(fd,packet,sizeof(packet),0,(void *)&peer,sizeof(peer));
   if(n<0 && errno==ENOBUFS){
    no_buffer++;struct timespec pause={.tv_nsec=100000};nanosleep(&pause,NULL);
    attempted=now();continue;
   }
   if(n!=(ssize_t)sizeof(packet)){perror("UDP send");goto finished;}
   done++;sent++;
  }
  /* Eight datagrams amortize deadline jitter. A stalled batch
   * still starts a new deadline, so it cannot cause a catch-up burst. */
  due=attempted+batch*sizeof(packet)*8.0/(rate*1e6);
 }
finished:
 while(now()<due){}
 double elapsed=now()-started;
 clock_gettime(CLOCK_MONOTONIC,&common_end);
 printf("{\"requested\":%u,\"sent\":%u,\"payload_bytes\":%llu,\"target_Mbps\":%u,\"seconds\":%.6f,\"offered_payload_Mbps\":%.3f,\"ENOBUFS_retries\":%u,\"send_buffer_result\":%d,\"send_buffer_bytes\":%d,\"start_monotonic_seconds\":%.9f,\"end_monotonic_seconds\":%.9f,\"batch_packets\":%u}\n",
 count,sent,(unsigned long long)sent*sizeof(packet),rate,elapsed,sent*sizeof(packet)*8/elapsed/1e6,no_buffer,br,buffer,
 common_start.tv_sec+common_start.tv_nsec/1e9,common_end.tv_sec+common_end.tv_nsec/1e9,BATCH);
 close(fd);return sent==count?0:1;
}
