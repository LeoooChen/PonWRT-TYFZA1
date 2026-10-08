/* SPDX-License-Identifier: GPL-2.0-only */
/* Inject one fixed Ethernet/IPv4/UDP tuple on the attached TYFZA1 LAN. */
#include <pcap/pcap.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <arpa/inet.h>

static void be16(unsigned char *p,unsigned int value) {p[0]=value>>8;p[1]=value;}
static unsigned int sum(const unsigned char *p,unsigned int size)
{
 unsigned int value=0;
 while(size>=2){value+=(p[0]<<8)|p[1];p+=2;size-=2;}
 if(size)value+=p[0]<<8;
 return value;
}
static unsigned int finish(unsigned int value)
{
 while(value>>16)value=(value&65535)+(value>>16);
 return (~value)&65535;
}
int main(int argc,char **argv)
{
 char error[PCAP_ERRBUF_SIZE];
 unsigned char frame[128]={0};
 const unsigned char da[]={0,0x50,0xc2,1,2,3},sa[]={0,0xe0,0x4c,0x16,0x74,0xb0};
 unsigned char source[]={10,23,42,1};
 const unsigned char destination[]={192,168,1,1};
 const char payload[]="TYFZA1-FE-NAT-TEST";
 unsigned char *ip=frame+14,*udp=frame+34;
 unsigned int size=14+20+8+sizeof(payload),udp_size=8+sizeof(payload),value;
 pcap_t *pcap;
 int sent;

 if(argc<2 || argc>4){fprintf(stderr,"usage: mac_fe_inject interface [interactive [sourceIPv4]]\n");return 1;}
 if(argc==4 && inet_pton(AF_INET,argv[3],source)!=1){fprintf(stderr,"invalid sourceIPv4\n");return 1;}
 memcpy(frame,da,6);memcpy(frame+6,sa,6);be16(frame+12,0x800);
 ip[0]=0x45;be16(ip+2,20+udp_size);ip[8]=64;ip[9]=17;
 memcpy(ip+12,source,4);memcpy(ip+16,destination,4);be16(ip+10,finish(sum(ip,20)));
 be16(udp,53000);be16(udp+2,18082);be16(udp+4,udp_size);
 memcpy(udp+8,payload,sizeof(payload));
 value=finish(sum(ip+12,8)+17+udp_size+sum(udp,udp_size));
 be16(udp+6,value?value:65535);
 pcap=pcap_open_live(argv[1],2048,0,10,error);
 if(!pcap){fprintf(stderr,"%s\n",error);return 1;}
 if(argc>=3) {
  char line[32];
  static const unsigned char gateway[]={192,168,71,1};
  static const unsigned char dns_server[]={116,228,111,118};
  static const unsigned char dns_query[]={
   0x82,0x77,0x01,0x00,0,1,0,0,0,0,0,0,
   7,'e','x','a','m','p','l','e',3,'c','o','m',0,0,1,0,1
  };

  puts("Raw en5 injection ready:send,trace,wan,dns");
  fflush(stdout);
  while(fgets(line,sizeof(line),stdin)) {
   int dns=!strncmp(line,"dns",3),wan=!strncmp(line,"wan",3);
   unsigned int port=dns?53:(!strncmp(line,"trace",5)?18081:18082);
   unsigned int payload_size=dns?sizeof(dns_query):sizeof(payload);

   udp_size=8+payload_size;
   size=14+20+udp_size;
   memcpy(ip+16,dns?dns_server:(wan?gateway:destination),4);
   be16(ip+2,20+udp_size);
   be16(ip+10,0);
   be16(ip+10,finish(sum(ip,20)));
   be16(udp+2,port);
   be16(udp+4,udp_size);
   memcpy(udp+8,dns?(const void *)dns_query:(const void *)payload,payload_size);
   be16(udp+6,0);
   value=finish(sum(ip+12,8)+17+udp_size+sum(udp,udp_size));
   be16(udp+6,value?value:65535);
   sent=pcap_inject(pcap,frame,size);
   printf("Injected %d bytes\n",sent);
   fflush(stdout);
  }
  pcap_close(pcap);
  return 0;
 }
 sent=pcap_inject(pcap,frame,size);
 if(sent!=(int)size){fprintf(stderr,"%s\n",pcap_geterr(pcap));pcap_close(pcap);return 1;}
 pcap_close(pcap);
 printf("Injected %u-byte fixed UDP frame on %s:10.23.42.1:53000 ->192.168.1.1:18082\n",size,argv[1]);
 return 0;
}
