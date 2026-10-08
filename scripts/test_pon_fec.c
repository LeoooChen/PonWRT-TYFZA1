/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Run the real codec/backend and FEC register sequence against a sysfs model. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pon-oam.h"
#include "rtl9617c-pon-fec.h"

static struct {
 unsigned int regs[0xc000/4];
 enum rtl_pon_mode mode;
 unsigned int writes;
 int missing, reject, stale;
 char read_data[128];
 size_t offset;
} model;

static unsigned int read_reg(void *ctx, unsigned int reg)
{ (void)ctx; assert(!(reg&3) && reg<sizeof(model.regs)); return model.regs[reg/4]; }
static void write_reg(void *ctx, unsigned int reg, unsigned int value)
{ (void)ctx; assert(!(reg&3) && reg<sizeof(model.regs)); model.regs[reg/4]=value; model.writes++; }
static const struct rtl_dq_io io={NULL,read_reg,write_reg,NULL};
static ssize_t sys_read(void *ctx, char *buf, size_t size)
{
 (void)ctx;
 size_t remain=strlen(model.read_data)-model.offset;
 if(size>remain)size=remain;
 memcpy(buf,model.read_data+model.offset,size); model.offset+=size;
 return size;
}
static ssize_t sys_write(void *ctx, const char *buf, size_t size)
{
 (void)ctx;
 if(model.reject){errno=EIO;return -1;}
 assert(size==2 && buf[1]=='\n' && (buf[0]=='0'||buf[0]=='1'));
 if(!model.stale)rtl_pon_fec_set(&io,model.mode,buf[0]=='1');
 return size;
}
static FILE *sys_open(const char *path, const char *mode)
{
 assert(!strcmp(path,"/native/pon/fec"));
 if(model.missing){errno=ENOENT;return NULL;}
 if(mode[0]=='r'){
  model.offset=0;
  snprintf(model.read_data,sizeof(model.read_data),"supported=1 upstream=%u\n",
   model.mode==RTL_PON_EPON_BI10G ? !(read_reg(NULL,0x24)&1) : !!(read_reg(NULL,0x810)&2));
 }
 return fopencookie(NULL,mode,(cookie_io_functions_t){.read=sys_read,.write=sys_write});
}
#define fopen sys_open
#include "../port/packages/tyfza1-pon-control/src/pon-backend.c"
#undef fopen

static void frame(uint8_t *data, const uint8_t *payload, size_t size)
{
 memset(data,0,PON_OAM_FRAME_MAX);
 memcpy(data+12,"\x88\x09\x03",3);data[17]=0xfe;
 memcpy(data+18,payload,size);
}
int main(void)
{
 struct pon_backend backend;
 struct pon_oam_identity identity={};
 struct pon_oam_session session={};
 uint8_t input[PON_OAM_FRAME_MAX],output[PON_OAM_FRAME_MAX];
 const uint8_t get[]={0x11,0x11,0x11,1,7,1,0x39,7,1,0x3a,0};
 const uint8_t set[]={0x11,0x11,0x11,3,7,1,0x3a,4,0,0,0,2,0};
 const uint8_t get_reply[]={0x11,0x11,0x11,2,7,1,0x39,4,0,0,0,2,7,1,0x3a,4,0,0,0,3,0};
 const uint8_t set_reply[]={0x11,0x11,0x11,4,7,1,0x3a,0x80,0};
 assert(!pon_backend_init(&backend,"/native/pon/mpcp"));
 pon_backend_bind(&identity,&backend);
 pon_oam_reset(&session);session.local_echoed=true;session.have_peer=true;session.have_sent=true;session.peer_flags=0x10;
 model.mode=RTL_PON_EPON_D10G;
 frame(input,get,sizeof(get));
 assert(pon_oam_receive(&session,&identity,input,60,output)==60);
 assert(!memcmp(output+18,get_reply,sizeof(get_reply)));
 frame(input,set,sizeof(set));
 assert(pon_oam_receive(&session,&identity,input,60,output)==60);
 assert(!memcmp(output+18,set_reply,sizeof(set_reply)) && model.writes==3);
 assert(read_reg(NULL,0x810)==2 && read_reg(NULL,0x8050)==(1U<<20) && read_reg(NULL,0x8054)==(1U<<20));
 /* A refused or unconfirmed hardware write never produces a success ACK. */
 for(unsigned int fail=0;fail<3;fail++){
  model.missing=fail==0;model.reject=fail==1;model.stale=fail==2;
  input[29]=3;
  assert(pon_oam_receive(&session,&identity,input,60,output)==60);
  assert(output[25]==0x87 && read_reg(NULL,0x810)==2);
 }
 model.missing=model.reject=model.stale=0;
 input[29]=1;
 assert(pon_oam_receive(&session,&identity,input,60,output)==60 && output[25]==0x86);
 /* All three upstream modes preserve unrelated/RX and scheduler-enable bits. */
 for(unsigned int mode=0;mode<3;mode++){
  model.mode=mode;
  write_reg(NULL,0x810,0xa5a50101);write_reg(NULL,0x8050,0x03000214);write_reg(NULL,0x8054,0x0100c081);
  write_reg(NULL,0x24,0xabcdef21);model.writes=0;
  rtl_pon_fec_set(&io,mode,1);
  if(mode==RTL_PON_EPON_BI10G){
   assert(read_reg(NULL,0x24)==0xabcdef20 && model.writes==1);
   assert(read_reg(NULL,0x810)==0xa5a50101 && read_reg(NULL,0x8050)==0x03000214);
  }else{
   assert(read_reg(NULL,0x810)==0xa5a50103);
   assert(read_reg(NULL,0x8050)==0x03100214 && read_reg(NULL,0x8054)==0x0110c081 && model.writes==3);
  }
  rtl_pon_fec_set(&io,mode,0);
  assert(read_reg(NULL,0x810)==0xa5a50101 && read_reg(NULL,0x8050)==0x03000214);
  assert(read_reg(NULL,0x8054)==0x0100c081 && read_reg(NULL,0x24)==0xabcdef21);
 }
 puts("Native FEC: three modes preserve RX/scheduler bits; real CTC Get/Set ACK follows successful hardware readback only");
 return 0;
}
