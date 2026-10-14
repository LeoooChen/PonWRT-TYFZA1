/* SPDX-License-Identifier: GPL-2.0-only */
/* Darwin BPF LAN -> AP link test. This bypasses IP routing, not the radio.
 * Compile on native Mac storage; root opens en5/en0 BPF devices.
 * Usage: mac_wifi_udp_stress target_Mbps seconds [source_interface destination_interface]
 */
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <net/if.h>
#include <net/if_dl.h>
#include <net/bpf.h>
#include <ifaddrs.h>
#include <pthread.h>
#include <stdatomic.h>
#include <fcntl.h>
#include <time.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static int rx;static unsigned int received,bad,duplicate;static unsigned char *seen;static unsigned int seen_count;static double first,last;static atomic_int sending=1;static unsigned retries;static unsigned char expected[1468];
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static int bpf(const char *name){int fd=-1;char path[40];for(int n=0;n<256;n++){snprintf(path,sizeof(path),"/dev/bpf%d",n);fd=open(path,O_RDWR);if(fd>=0)break;}if(fd<0){perror("open bpf");exit(1);}unsigned int sz=1024*1024,one=1,zero=0;struct timeval timeout={.tv_sec=1};struct ifreq q={};strlcpy(q.ifr_name,name,sizeof(q.ifr_name));ioctl(fd,BIOCSBLEN,&sz);if(ioctl(fd,BIOCSETIF,&q)){perror(name);exit(1);}ioctl(fd,BIOCIMMEDIATE,&one);ioctl(fd,BIOCSRTIMEOUT,&timeout);ioctl(fd,BIOCSSEESENT,&zero);return fd;}
static void mac(const char *name,unsigned char *out){struct ifaddrs *all,*p;getifaddrs(&all);for(p=all;p;p=p->ifa_next)if(p->ifa_addr && p->ifa_addr->sa_family==AF_LINK && !strcmp(p->ifa_name,name)){struct sockaddr_dl *a=(void*)p->ifa_addr;memcpy(out,LLADDR(a),6);break;}freeifaddrs(all);}
static void *receive(void *unused){(void)unused;unsigned int size;ioctl(rx,BIOCGBLEN,&size);unsigned char *buffer=malloc(size);while(atomic_load(&sending) || now()-last<1.1){int n=read(rx,buffer,size);if(n<=0){if(!atomic_load(&sending))break;continue;}for(unsigned int p=0;p+(unsigned int)sizeof(struct bpf_hdr)<=(unsigned int)n;){struct bpf_hdr *h=(void*)(buffer+p);unsigned char *d=buffer+p+h->bh_hdrlen;if(h->bh_caplen==1514 && d[12]==0x08 && d[13]==0 && d[23]==17 && !memcmp(d+46,"TYFZA1-L2",9)){if(memcmp(d+46,expected,sizeof(expected)))bad++;unsigned int sequence;memcpy(&sequence,d+42,4);if(sequence<seen_count){duplicate+=seen[sequence]!=0;seen[sequence]=1;}if(!received)first=now();received++;last=now();}p+=BPF_WORDALIGN(h->bh_hdrlen+h->bh_caplen);}}free(buffer);return NULL;}
int main(int argc,char **argv){unsigned int rate=argc>1?atoi(argv[1]):200;double seconds=argc>2?atof(argv[2]):5;const char *txif=argc>3?argv[3]:"en5", *rxif=argc>4?argv[4]:"en0";seen_count=(unsigned int)(seconds*rate*1000000.0/(1514*8))+1;seen=calloc(seen_count,1);int tx=bpf(txif);rx=bpf(rxif);unsigned char frame[1514]={};mac(rxif,frame);mac(txif,frame+6);frame[12]=8;frame[13]=0;
frame[14]=0x45;frame[16]=1500>>8;frame[17]=1500&255;frame[22]=64;frame[23]=17;
frame[26]=frame[30]=198;frame[27]=frame[31]=18;frame[28]=frame[32]=1;frame[29]=1;frame[33]=2;
unsigned int sum=0;for(unsigned i=14;i<34;i+=2)sum+=(frame[i]<<8)|frame[i+1];while(sum>>16)sum=(sum&65535)+(sum>>16);sum=(~sum)&65535;frame[24]=sum>>8;frame[25]=sum;
frame[34]=55000>>8;frame[35]=55000&255;frame[36]=55001>>8;frame[37]=55001&255;frame[38]=1480>>8;frame[39]=1480&255;
for(unsigned int i=0;i<sizeof(expected);i++)expected[i]=(i*31+7)&255;memcpy(expected,"TYFZA1-L2",9);memcpy(frame+46,expected,sizeof(expected));pthread_t thread;pthread_create(&thread,NULL,receive,NULL);double start=now(),deadline=start+seconds;unsigned int sent=0;while(now()<deadline){unsigned int sequence=sent;memcpy(frame+42,&sequence,4);if(write(tx,frame,sizeof(frame))!=(int)sizeof(frame)){if(errno==ENOBUFS){retries++;struct timespec pause={.tv_nsec=100000};nanosleep(&pause,NULL);continue;}perror("bpf write");break;}sent++;double due=start+(double)sent*1514*8/(rate*1000000.0);while(now()<due){};}double end=now();atomic_store(&sending,0);pthread_join(thread,NULL);struct bpf_stat capture={};ioctl(rx,BIOCGSTATS,&capture);printf("{\"target_Mbps\":%u,\"host_ENOBUFS\":%u,\"host_capture_drops\":%u,\"sent\":%u,\"received\":%u,\"payload_errors\":%u,\"duplicate_sequences\":%u,\"send_seconds\":%.6f,\"received_Mbps_during_send\":%.3f,\"first_receive\":%.6f,\"last_receive\":%.6f,\"source_interface\":\"%s\",\"destination_interface\":\"%s\",\"path\":\"physical interface to interface bridge\"}\n",rate,retries,capture.bs_drop,sent,received,bad,duplicate,end-start,received*1514.0*8/((last>end?last:end)-start)/1000000,first,last,txif,rxif);close(tx);close(rx);free(seen);return 0;}
