// SPDX-License-Identifier: GPL-2.0-only
/* Inspect the diagnostic source qdisc, or use a byte FIFO for EQ backpressure. */
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <net/if.h>
#include <linux/rtnetlink.h>
#include <linux/pkt_sched.h>
#include <linux/gen_stats.h>

int main(int argc,char **argv)
{
 if(argc<2 || argc>3 || (argc==3 && strcmp(argv[2],"bfifo"))){
  fprintf(stderr,"Usage: %s INTERFACE [bfifo]\n",argv[0]);return 2;
 }
 unsigned int index=if_nametoindex(argv[1]);
 if(!index){perror("interface");return 1;}
 int fd=socket(AF_NETLINK,SOCK_RAW,NETLINK_ROUTE);
 if(fd<0){perror("socket");return 1;}
 struct sockaddr_nl kernel={.nl_family=AF_NETLINK};
 char buffer[16384];
 if(argc==3){
  unsigned int maximum;
  FILE *settings=fopen("/proc/sys/net/core/wmem_max","r");
  if(!settings){perror("wmem_max");return 1;}
  int parsed=fscanf(settings,"%u",&maximum);fclose(settings);
  if(parsed!=1 || maximum>UINT_MAX/4){fprintf(stderr,"Invalid wmem_max for FIFO\n");return 1;}
  struct {struct nlmsghdr h;struct tcmsg t;char attrs[64];} set={
   .h={.nlmsg_len=NLMSG_LENGTH(sizeof(struct tcmsg)),.nlmsg_type=RTM_NEWQDISC,
    .nlmsg_flags=NLM_F_REQUEST|NLM_F_ACK|NLM_F_CREATE|NLM_F_REPLACE,.nlmsg_seq=1},
   .t={.tcm_ifindex=index,.tcm_parent=TC_H_ROOT,.tcm_handle=0x10000}};
  struct rtattr *a=(void *)((char *)&set+NLMSG_ALIGN(set.h.nlmsg_len));
  a->rta_type=TCA_KIND;a->rta_len=RTA_LENGTH(6);memcpy(RTA_DATA(a),"bfifo",6);
  set.h.nlmsg_len=NLMSG_ALIGN(set.h.nlmsg_len)+RTA_ALIGN(a->rta_len);
  a=(void *)((char *)&set+NLMSG_ALIGN(set.h.nlmsg_len));
  a->rta_type=TCA_OPTIONS;a->rta_len=RTA_LENGTH(sizeof(struct tc_fifo_qopt));
  /* Linux doubles SO_SNDBUF; IPv4 UDP can allocate up to twice sk_sndbuf
   * in __ip_append_data(). Cover that allowance before imposing a limit. */
  struct tc_fifo_qopt q={.limit=maximum*4};
  if(q.limit<16U*1024*1024)q.limit=16U*1024*1024;
  memcpy(RTA_DATA(a),&q,sizeof(q));
  set.h.nlmsg_len=NLMSG_ALIGN(set.h.nlmsg_len)+RTA_ALIGN(a->rta_len);
  if(sendto(fd,&set,set.h.nlmsg_len,0,(void *)&kernel,sizeof(kernel))<0){perror("set qdisc");return 1;}
  int n=recv(fd,buffer,sizeof(buffer),0);
  struct nlmsghdr *h=(void *)buffer;
  if(n<0){perror("qdisc ACK");return 1;}
  if(!NLMSG_OK(h,n) || h->nlmsg_type!=NLMSG_ERROR || NLMSG_PAYLOAD(h,0)<sizeof(struct nlmsgerr))return 1;
  int error=((struct nlmsgerr *)NLMSG_DATA(h))->error;
  if(error){fprintf(stderr,"set qdisc: %s\n",strerror(-error));return 1;}
 }
 struct {struct nlmsghdr h;struct tcmsg t;} request={
  .h={.nlmsg_len=NLMSG_LENGTH(sizeof(struct tcmsg)),.nlmsg_type=RTM_GETQDISC,
   .nlmsg_flags=NLM_F_REQUEST|NLM_F_DUMP,.nlmsg_seq=2},.t={.tcm_ifindex=index}};
 if(sendto(fd,&request,sizeof(request),0,(void *)&kernel,sizeof(kernel))<0){perror("get qdisc");return 1;}
 for(;;){
  int n=recv(fd,buffer,sizeof(buffer),0);
  if(n<0){perror("qdisc stats");return 1;}
  for(struct nlmsghdr *h=(void *)buffer;NLMSG_OK(h,n);h=NLMSG_NEXT(h,n)){
   if(h->nlmsg_type==NLMSG_DONE){close(fd);return 0;}
   if(h->nlmsg_type==NLMSG_ERROR)return 1;
   if(h->nlmsg_type!=RTM_NEWQDISC || NLMSG_PAYLOAD(h,0)<sizeof(struct tcmsg))continue;
   struct tcmsg *t=NLMSG_DATA(h);
   if(t->tcm_ifindex!=(int)index)continue;
   printf("qdisc interface=%s handle=%x parent=%x",argv[1],t->tcm_handle,t->tcm_parent);
   int length=h->nlmsg_len-NLMSG_LENGTH(sizeof(*t));
   int fifo=0;
   for(struct rtattr *a=TCA_RTA(t);RTA_OK(a,length);a=RTA_NEXT(a,length)){
    unsigned int type=a->rta_type&NLA_TYPE_MASK;
    if(type==TCA_KIND){
     printf(" kind=%.*s",(int)RTA_PAYLOAD(a),(char *)RTA_DATA(a));
     fifo=RTA_PAYLOAD(a)==6 && !memcmp(RTA_DATA(a),"bfifo",6);
    }
    if(type==TCA_OPTIONS && fifo && RTA_PAYLOAD(a)>=sizeof(struct tc_fifo_qopt)){
     struct tc_fifo_qopt q;memcpy(&q,RTA_DATA(a),sizeof(q));printf(" limit_bytes=%u",q.limit);
    }
    if(type!=TCA_STATS2)continue;
    int nested=RTA_PAYLOAD(a);
    for(struct rtattr *s=RTA_DATA(a);RTA_OK(s,nested);s=RTA_NEXT(s,nested)){
     type=s->rta_type&NLA_TYPE_MASK;
     if(type==TCA_STATS_BASIC && RTA_PAYLOAD(s)>=12){
      uint64_t bytes;uint32_t packets;
      memcpy(&bytes,RTA_DATA(s),8);memcpy(&packets,(char *)RTA_DATA(s)+8,4);
      printf(" bytes=%llu packets=%u",(unsigned long long)bytes,packets);
     }
     if(type==TCA_STATS_QUEUE && RTA_PAYLOAD(s)>=sizeof(struct gnet_stats_queue)){
      struct gnet_stats_queue q;memcpy(&q,RTA_DATA(s),sizeof(q));
      printf(" qlen=%u backlog=%u drops=%u requeues=%u overlimits=%u",q.qlen,q.backlog,q.drops,q.requeues,q.overlimits);
     }
    }
   }
   puts("");
  }
 }
}
