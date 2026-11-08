// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <string.h>
#include "omci-optical.h"
void fttr_optical_start(struct fttr_optical_job *job,uint16_t onu,uint16_t instance,uint16_t *counter)
{
 memset(job,0,sizeof(*job));
 fttr_me_job_start(&job->me,onu,counter,0x52,263,instance,0,NULL,0);
 job->me.request[8]=7;
 job->me.no_retry=true;
}
void fttr_optical_cancel(struct fttr_optical_job *job,int error)
{
 job->error=error;job->done=true;job->valid=0;
 job->me.state=FTTR_ME_FAILED;job->me.error=error;
}
int fttr_optical_reply(struct fttr_optical_job *job,const struct fttr_omci_message *m)
{
 const uint8_t ids[5]={1,3,5,9,12};const uint8_t *p=m->body;
 if(job->done || !job->me.attempts || m->onu!=job->me.onu || m->length<2 ||
    memcmp(p,job->me.request,2))return 0;
 if(m->length!=44 || p[3]!=10 || memcmp(p+4,job->me.request+4,4) ||
    (p[2]!=0x32 && p[2]!=0x1b) || memcmp(p+40,"\0\0\0\x28",4))goto malformed;
 if(p[2]==0x32) {
  if(p[8]){fttr_optical_cancel(job,-EREMOTEIO);return 1;}
  job->acknowledged=true;
 } else {
  uint16_t raw[5]={0},valid=0;
  for(unsigned i=0;i<5;i++) {
   const uint8_t *v=p+8+i*3;
   if(!v[0]){if(v[1]||v[2])goto malformed;continue;}
   if(v[0]!=ids[i])goto malformed;
   raw[i]=(uint16_t)v[1]<<8|v[2];valid|=1U<<i;
  }
  if(!valid) {fttr_optical_cancel(job,-ENODATA);return 1;}
  if(job->result && (valid!=job->valid || memcmp(raw,job->raw,sizeof(raw))))goto malformed;
  memcpy(job->raw,raw,sizeof(raw));job->valid=valid;job->result=true;
 }
 job->done=job->acknowledged&&job->result;
 return 1;
malformed:
 fttr_optical_cancel(job,-EPROTO);return 1;
}
int fttr_optical_tick(struct fttr_optical_job *job,int64_t now)
{
 if(!job->done && job->me.attempts && now>=job->me.deadline)fttr_optical_cancel(job,-ETIMEDOUT);
 return job->done;
}
int32_t fttr_optical_value(unsigned int index,uint16_t raw)
{
 int32_t signed_raw=raw&0x8000?(int32_t)raw-65536:raw;
 switch(index) {
 case 0:return signed_raw*2;
 case 1:case 2:return signed_raw/5-3000;
 case 3:return raw/5;
 case 4:return signed_raw*100/256;
 default:return 0;
 }
}
