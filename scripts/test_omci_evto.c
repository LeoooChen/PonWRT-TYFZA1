/* SPDX-License-Identifier: GPL-2.0-only */
/* Real OLT provisioning produces the exact snapshot consumed by the kernel. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pon-omci.h"

static unsigned int transaction;
static void be16(uint8_t *p,unsigned int v){p[0]=v>>8;p[1]=v;}
static void be32(uint8_t *p,unsigned int v){p[0]=v>>24;p[1]=v>>16;p[2]=v>>8;p[3]=v;}
static unsigned int le16(const uint8_t *p){return p[0]|p[1]<<8;}
static int request(struct omci_agent *a,unsigned int action,unsigned int cls,unsigned int id,
                   const uint8_t *body,unsigned int length)
{
 uint8_t frame[44]={},reply[2048];
 be16(frame,++transaction);frame[2]=0x40|action;frame[3]=10;
 be16(frame+4,cls);be16(frame+6,id);frame[43]=0x28;memcpy(frame+8,body,length);
 assert(tyfza1_omci_dispatch(a,frame,sizeof(frame),reply,0)==44);
 return reply[8];
}
static void path(struct omci_agent *a,unsigned int n)
{
 uint8_t body[32]={};
 be16(body,4000+n);be16(body+2,0x8000);body[4]=3;be16(body+5,0x8007);
 be16(body+7,0xffff);be16(body+9,0xffff);be16(body+11,0xffff);
 assert(!request(a,4,268,0x100+n,body,13));
 memset(body,0,sizeof(body));be16(body,0x100+n);body[2]=5;
 be16(body+3,0xffff);be16(body+5,0xffff);assert(!request(a,4,266,0x200+n,body,10));
 memset(body,0,sizeof(body));be16(body,1);body[2]=n+1;body[3]=5;be16(body+4,0x200+n);
 assert(!request(a,4,47,0x300+n,body,14));
 memset(body,0,sizeof(body));be16(body,100+n);body[24]=4;body[25]=1;
 assert(!request(a,4,84,0x300+n,body,26));
}
static void profile(struct omci_agent *a,unsigned int id,unsigned int kind,unsigned int pointer)
{
 uint8_t body[3]={kind};be16(body+1,pointer);assert(!request(a,4,171,id,body,sizeof(body)));
}
static int row(struct omci_agent *a,unsigned int id,unsigned int input,unsigned int output)
{
 uint8_t body[18]={4,0};
 be32(body+2,0xf8000000); /* No outer tag. */
 be32(body+6,input==65535?0xf8000000:0x80004000|(input<<15));
 be32(body+10,input==65535?0x000f0000:0x400f0000);
 be32(body+14,(input==65535?5U:8U)<<16|output<<3|4);
 return request(a,8,171,id,body,sizeof(body));
}
static struct omci_agent *agent(void)
{
 struct omci_identity identity={.serial={'T','E','S','T',1,2,3,4}};
 struct omci_agent *a=tyfza1_omci_new(&identity);
 uint8_t body[4]={0x80,0,4,0x27};
 assert(!request(a,8,262,0x8000,body,sizeof(body)));
 return a;
}
static void save(const char *name,const uint8_t *wire,unsigned int length)
{
 FILE *file=fopen(name,"wb");assert(file);
 assert(fwrite(wire,1,length,file)==length);assert(!fclose(file));
}
int main(int argc,char **argv)
{
 static uint8_t wire[65536],before[65536];
 struct omci_agent *a=agent();
 path(a,0);
 profile(a,65535,10,0x0a01); /* FFFF is a valid ME ID, not a missing profile. */
 profile(a,0x501,5,0x200);
 assert(!row(a,65535,65535,100));assert(!row(a,0x501,100,200));
 int length=tyfza1_omci_paths(a,wire);
 assert(length>12 && le16(wire)==3 && le16(wire+2)==1 && le16(wire+4)==2);
 assert(le16(wire+12+13)==1 && le16(wire+12+21)==0);
 memcpy(before,wire,length);
 uint8_t invalid_mode[3]={8,0,9};
 assert(request(a,8,171,0x501,invalid_mode,sizeof(invalid_mode))==3);
 assert(tyfza1_omci_paths(a,wire)==length && !memcmp(wire,before,length));
 if(argc>=3)save(argv[1],wire,length);
 tyfza1_omci_free(a);
 a=agent();
 for(unsigned int n=0;n<8;n++) {
  path(a,n);profile(a,0x500+n,5,0x200+n);
  for(unsigned int i=0;i<29;i++)assert(!row(a,0x500+n,10+i,1000+i));
 }
 length=tyfza1_omci_paths(a,wire);
 assert(length==12+8*50+8*(32+32*16) && length>4096);
 memcpy(before,wire,length);
 assert(row(a,0x500,99,999)==1);
 assert(tyfza1_omci_paths(a,wire)==length && !memcmp(wire,before,length));
 if(argc>=3)save(argv[2],wire,length);
 tyfza1_omci_free(a);
 a=agent();path(a,0);
 uint8_t body[32]={};be16(body,0xffff);
 for(unsigned int i=0;i<8;i++)be16(body+2+i*2,i?0xffff:0x200);
 assert(!request(a,4,130,0x400,body,24));
 memset(body,0,sizeof(body));body[0]=0x30;body[2]=3;be16(body+3,0x400);
 assert(!request(a,8,47,0x300,body,5));
 memset(body,0,sizeof(body));body[0]=0xe0;be16(body+2,5<<13|100);body[26]=12;body[27]=1;
 assert(!request(a,8,84,0x300,body,28));
 profile(a,65535,10,0x0a01);assert(!row(a,65535,65535,100));
 profile(a,0x502,0,0x300);profile(a,0x503,1,0x400);
 body[0]=4;body[1]=0;be32(body+2,0xf8000000);be32(body+6,5U<<28|100<<15|4<<12);
 be32(body+10,0x400f0000);be32(body+14,100<<3|4);
 assert(!request(a,8,171,0x502,body,18));
 be32(body+6,100<<15|4<<12);be32(body+14,1U<<16|200<<3|4);
 assert(!request(a,8,171,0x503,body,18));
 length=tyfza1_omci_paths(a,wire);
 assert(length>12 && le16(wire+2)==1 && wire[18]==1 && wire[35]==(uint8_t)~(1U<<5));
 assert(le16(wire+12+17)==0 && le16(wire+12+19)==1);
 if(argc==4)save(argv[3],wire,length);
 /* The mapper policy is carried separately from bridge priority filtering. */
 memset(body,0,sizeof(body));body[1]=0x50;
 body[2]=1;body[3]=0xfd; /* Fixed implied PCP uses the low three bits only. */
 assert(!request(a,8,130,0x400,body,4));
 assert(tyfza1_omci_paths(a,wire)==length && wire[36]==2 && wire[37]==5);
 memset(body,0,sizeof(body));body[1]=0x40;body[2]=0;
 assert(!request(a,8,130,0x400,body,3));
 memset(body,0,sizeof(body));body[1]=0x20;body[2+17]=5<<3;
 assert(!request(a,8,130,0x400,body,26));
 assert(tyfza1_omci_paths(a,wire)==length && wire[36]==1 && wire[38+17]==5<<3);
 tyfza1_omci_free(a);
 /* G.988 9.3.10 TP type7 connects a mapper directly to the VEIP. */
 a=agent();path(a,0);
 assert(!request(a,6,84,0x300,body,0));
 assert(!request(a,6,47,0x300,body,0));
 memset(body,0,sizeof(body));be16(body,0x0a01);
 for(unsigned int i=0;i<8;i++)be16(body+2+i*2,i==5?0x200:0xffff);
 body[18]=1;body[19]=5;body[20]=7;
 assert(!request(a,4,130,0x400,body,21));
 profile(a,65535,10,0x0a01);assert(!row(a,65535,65535,100));
 profile(a,0x501,1,0x400);
 length=tyfza1_omci_paths(a,wire);
 assert(length>12 && le16(wire+2)==1 && wire[18]==(1U<<5) && wire[36]==2 && wire[37]==5);
 assert(le16(wire+25)==1 && le16(wire+31)==0);
 tyfza1_omci_free(a);
 puts("OMCI EVTO: autonomous defaults, VEIP/IWTP binding, ME FFFF, 32-row limit and multi-page snapshot passed");
}
