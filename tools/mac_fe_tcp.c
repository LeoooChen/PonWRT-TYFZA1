/* SPDX-License-Identifier: GPL-2.0-only */
/* Raw Ethernet TCP client for testing the attached ONU without host proxies. */
#include <pcap/pcap.h>
#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

static unsigned char frame[1600];
static const unsigned char router[6]={0,0x50,0xc2,1,2,3};
static const unsigned char client[6]={0,0xe0,0x4c,0x16,0x74,0xb0};
static const unsigned char source[4]={10,23,42,1},destination[4]={1,1,1,1};
static uint32_t local_sequence=0x9617c000,remote_sequence;
static void be16(unsigned char *p,unsigned int v){p[0]=v>>8;p[1]=v;}
static void be32(unsigned char *p,uint32_t v){p[0]=v>>24;p[1]=v>>16;p[2]=v>>8;p[3]=v;}
static uint32_t read32(const unsigned char *p){return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|p[2]<<8|p[3];}
static unsigned int read16(const unsigned char *p){return p[0]*256+p[1];}
static unsigned int sum(const unsigned char *p,unsigned int n){unsigned int v=0;while(n>=2){v+=read16(p);p+=2;n-=2;}if(n)v+=p[0]<<8;return v;}
static unsigned int checksum(unsigned int v){while(v>>16)v=(v&65535)+(v>>16);return (~v)&65535;}
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static int send_arp(pcap_t *pcap)
{
 unsigned char arp[60]={};
 const unsigned char target_ip[4]={192,168,1,1};

 memcpy(arp,router,6);memcpy(arp+6,client,6);be16(arp+12,0x806);
 be16(arp+14,1);be16(arp+16,0x800);arp[18]=6;arp[19]=4;be16(arp+20,2);
 memcpy(arp+22,client,6);memcpy(arp+28,source,4);
 memcpy(arp+32,router,6);memcpy(arp+38,target_ip,4);
 return pcap_inject(pcap,arp,sizeof(arp))==(int)sizeof(arp)?0:-1;
}
static int send_tcp(pcap_t *pcap,unsigned int flags,const char *payload,unsigned int size)
{
 unsigned char *ip=frame+14,*tcp=frame+34;
 unsigned int bytes=54+size,csum;

 memset(frame,0,sizeof(frame));memcpy(frame,router,6);memcpy(frame+6,client,6);be16(frame+12,0x800);
 ip[0]=0x45;be16(ip+2,40+size);be16(ip+6,0x4000);ip[8]=64;ip[9]=6;
 memcpy(ip+12,source,4);memcpy(ip+16,destination,4);be16(ip+10,checksum(sum(ip,20)));
 be16(tcp,53123);be16(tcp+2,80);be32(tcp+4,local_sequence);be32(tcp+8,remote_sequence);
 tcp[12]=0x50;tcp[13]=flags;be16(tcp+14,32768);
 if(size)memcpy(tcp+20,payload,size);
 csum=checksum(sum(ip+12,8)+6+20+size+sum(tcp,20+size));be16(tcp+16,csum);
 if(bytes<60)bytes=60;
 int sent=pcap_inject(pcap,frame,bytes);
 if(sent!=(int)bytes){fprintf(stderr,"pcap inject:%s\n",pcap_geterr(pcap));return -1;}
 return 0;
}
static int receive_tcp(pcap_t *pcap,int syn,unsigned int *reply_bytes)
{
 double deadline=now()+5;
 struct pcap_pkthdr *header;
 const unsigned char *packet;

 while(now()<deadline) {
  int ret=pcap_next_ex(pcap,&header,&packet);
  if(ret<0)return -1;
  if(!ret)continue;
  if(header->caplen<54 || packet[12]!=8 || packet[13])continue;
  unsigned int ihl=(packet[14]&15)*4,total=read16(packet+16);
  if(ihl<20 || header->caplen<14+total || total<ihl+20)continue;
  const unsigned char *tcp=packet+14+ihl;
  unsigned int tcp_length=(tcp[12]>>4)*4;
  if(tcp_length<20 || total<ihl+tcp_length)continue;
  unsigned int payload=total-ihl-tcp_length;
  unsigned int flags=tcp[13];uint32_t sequence=read32(tcp+4),ack=read32(tcp+8);
  if(flags&4){fprintf(stderr,"peer TCP reset\n");return -1;}
  if(syn) {
   if((flags&18)==18 && ack==local_sequence+1) {
    local_sequence++;remote_sequence=sequence+1;return send_tcp(pcap,16,NULL,0);
   }
   continue;
  }
  if(sequence==remote_sequence && payload) {
   remote_sequence+=payload;*reply_bytes+=payload;
   if(send_tcp(pcap,16,NULL,0))return -1;
   printf("TCP reply bytes=%u first=%.12s\n",payload,tcp+tcp_length);fflush(stdout);
   return 0;
  }
  if(flags&1) {
   remote_sequence=sequence+payload+1;send_tcp(pcap,16,NULL,0);
   fprintf(stderr,"peer TCP closed\n");return -1;
  }
 }
 fprintf(stderr,"TCP response timeout\n");return -1;
}
int main(int argc,char **argv)
{
 char error[PCAP_ERRBUF_SIZE];struct bpf_program filter;
 unsigned int replies=0;
 if(argc!=2){fprintf(stderr,"usage:mac_fe_tcp interface\n");return 1;}
 pcap_t *pcap=pcap_create(argv[1],error);
 if(!pcap){fprintf(stderr,"%s\n",error);return 1;}
 pcap_set_snaplen(pcap,2048);pcap_set_timeout(pcap,20);pcap_set_immediate_mode(pcap,1);
 if(pcap_activate(pcap)<0){fprintf(stderr,"%s\n",pcap_geterr(pcap));return 1;}
 if(pcap_compile(pcap,&filter,"tcp and src host 1.1.1.1 and dst host 10.23.42.1 and src port 80 and dst port 53123",1,PCAP_NETMASK_UNKNOWN) || pcap_setfilter(pcap,&filter)) {
  fprintf(stderr,"%s\n",pcap_geterr(pcap));return 1;
 }
 pcap_freecode(&filter);
 if(send_tcp(pcap,2,NULL,0))return 1;
 usleep(10000);
 if(send_arp(pcap) || receive_tcp(pcap,1,&replies))return 1;
 puts("Raw en5 TCP established:10.23.42.1:53123 ->1.1.1.1:80");fflush(stdout);
 const char request[]="GET / HTTP/1.1\r\nHost:1.1.1.1\r\nConnection:keep-alive\r\n\r\n";
 for(unsigned int i=0;i<30;i++) {
  if(send_tcp(pcap,24,request,sizeof(request)-1))return 1;
  local_sequence+=sizeof(request)-1;
  if(receive_tcp(pcap,0,&replies))return 1;
  printf("completed HTTP request %u\n",i);fflush(stdout);
  usleep(500000);
 }
 send_tcp(pcap,17,NULL,0);pcap_close(pcap);
 printf("Raw TCP completed:30 HTTP requests, reply bytes=%u\n",replies);
 return 0;
}
