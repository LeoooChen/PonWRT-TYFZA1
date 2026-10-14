/* SPDX-License-Identifier: GPL-2.0-only */
/* Darwin BPF LAN -> AP link test. This bypasses IP routing, not the radio.
 * Compile on native Mac storage; root opens en5/en0 BPF devices.
 * Usage: mac_wifi_l2_stress target_Mbps seconds
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
static int rx;static unsigned int received,bad,duplicate;static unsigned char seen[1000000];static double first,last;static atomic_int sending=1;static unsigned char expected[1496];
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static int bpf(const char *name){int fd=-1;char path[40];for(int n=0;n<256;n++){snprintf(path,sizeof(path),"/dev/bpf%d",n);fd=open(path,O_RDWR);if(fd>=0)break;}if(fd<0){perror("open bpf");exit(1);}unsigned int sz=1024*1024,one=1,zero=0;struct timeval timeout={.tv_sec=1};struct ifreq q={};strlcpy(q.ifr_name,name,sizeof(q.ifr_name));ioctl(fd,BIOCSBLEN,&sz);if(ioctl(fd,BIOCSETIF,&q)){perror(name);exit(1);}ioctl(fd,BIOCIMMEDIATE,&one);ioctl(fd,BIOCSRTIMEOUT,&timeout);ioctl(fd,BIOCSSEESENT,&zero);return fd;}
static void mac(const char *name,unsigned char *out){struct ifaddrs *all,*p;getifaddrs(&all);for(p=all;p;p=p->ifa_next)if(p->ifa_addr && p->ifa_addr->sa_family==AF_LINK && !strcmp(p->ifa_name,name)){struct sockaddr_dl *a=(void*)p->ifa_addr;memcpy(out,LLADDR(a),6);break;}freeifaddrs(all);}
static void *receive(void *unused){unsigned int size;ioctl(rx,BIOCGBLEN,&size);unsigned char *buffer=malloc(size);while(atomic_load(&sending) || now()-last<1.1){int n=read(rx,buffer,size);if(n<=0){if(!atomic_load(&sending))break;continue;}for(unsigned int p=0;p+(unsigned int)sizeof(struct bpf_hdr)<=(unsigned int)n;){struct bpf_hdr *h=(void*)(buffer+p);unsigned char *d=buffer+p+h->bh_hdrlen;if(h->bh_caplen==1514 && d[12]==0x88 && d[13]==0xb5 && !memcmp(d+18,"TYFZA1-L2",9)){if(memcmp(d+18,expected,sizeof(expected)))bad++;unsigned int sequence;memcpy(&sequence,d+14,4);if(sequence<sizeof(seen)){duplicate+=seen[sequence]!=0;seen[sequence]=1;}if(!received)first=now();received++;last=now();}p+=BPF_WORDALIGN(h->bh_hdrlen+h->bh_caplen);}}free(buffer);return NULL;}
int main(int argc,char **argv){unsigned int rate=argc>1?atoi(argv[1]):200;double seconds=argc>2?atof(argv[2]):5;int tx=bpf("en5");rx=bpf("en0");unsigned char frame[1514]={};mac("en0",frame);mac("en5",frame+6);frame[12]=0x88;frame[13]=0xb5;for(unsigned int i=0;i<sizeof(expected);i++)expected[i]=(i*31+7)&255;memcpy(expected,"TYFZA1-L2",9);memcpy(frame+18,expected,sizeof(expected));pthread_t thread;pthread_create(&thread,NULL,receive,NULL);double start=now(),deadline=start+seconds;unsigned int sent=0;while(now()<deadline){unsigned int sequence=sent;memcpy(frame+14,&sequence,4);if(write(tx,frame,sizeof(frame))!=(int)sizeof(frame)){perror("bpf write");break;}sent++;double due=start+(double)sent*1514*8/(rate*1000000.0);while(now()<due){};}double end=now();atomic_store(&sending,0);pthread_join(thread,NULL);printf("{\"target_Mbps\":%u,\"sent\":%u,\"received\":%u,\"payload_errors\":%u,\"duplicate_sequences\":%u,\"send_seconds\":%.6f,\"received_Mbps_during_send\":%.3f,\"first_receive\":%.6f,\"last_receive\":%.6f,\"source_interface\":\"en5\",\"destination_interface\":\"en0\",\"path\":\"physical LAN to AP bridge, not NAT\"}\n",rate,sent,received,bad,duplicate,end-start,received*1514*8/(end-start)/1000000,first,last);close(tx);close(rx);return 0;}
