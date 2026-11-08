// SPDX-License-Identifier: GPL-2.0-only
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include "omci-alarm.h"
static uint16_t be16(const uint8_t *p){return (uint16_t)p[0]<<8|p[1];}
const char *fttr_alarm_name(uint16_t class_id,unsigned int bit)
{
 /* Target relay warning names and its ONU-G/ANI-G bitmap converters.
  * Vendor/reserved bits retain their numeric identity without guessed names. */
 static const char *const onug[]={"Equipment alarm","Powering alarm","Battery missing",
  "Battery failure","Battery low","Physical intrusion","ONT self test failure",
  "Dying gasp","Temperature yellow","Temperature red","Voltage yellow","Voltage red"};
 static const char *const anig[]={"Low received optical power","High received optical power",
  "SF","SD","Low transmit optical power","High transmit optical power","Laser bias current"};
 if(class_id==256 && bit<sizeof(onug)/sizeof(*onug))return onug[bit];
 if(class_id==263 && bit<sizeof(anig)/sizeof(*anig))return anig[bit];
 if(class_id==11 && !bit)return "LAN-LOS";
 return NULL;
}
static bool body_valid(const struct fttr_omci_message *m)
{
 return m->length==44 && m->body[3]==10 && !memcmp(m->body+40,"\0\0\0\x28",4);
}
static void transient_clear(struct fttr_alarm_state *s)
{
 free(s->snapshot);free(s->updates);s->snapshot=s->updates=NULL;
 s->total=s->received=s->update_count=0;s->active=false;
}
void fttr_alarm_clear(struct fttr_alarm_state *s)
{
 transient_clear(s);free(s->rows);memset(s,0,sizeof(*s));
}
void fttr_alarm_invalidate(struct fttr_alarm_state *s,int error,int64_t now)
{
 transient_clear(s);s->valid=false;s->error=error;s->due=now+2000;s->saw_sequence=false;
}
static int upsert(struct fttr_alarm_row **rows,unsigned int *count,const struct fttr_alarm_row *row)
{
 unsigned int i;
 for(i=0;i<*count;i++)if((*rows)[i].class_id==row->class_id && (*rows)[i].instance==row->instance)break;
 if(i==*count) {
  if(*count==FTTR_ALARM_MAX)return -E2BIG;
  void *p=realloc(*rows,(*count+1)*sizeof(**rows));if(!p)return -ENOMEM;
  *rows=p;(*count)++;
 }
 (*rows)[i]=*row;return 0;
}
static int complete(struct fttr_alarm_state *s,int64_t now)
{
 for(unsigned int i=0;i<s->update_count;i++) {
  int ret=upsert(&s->snapshot,&s->received,&s->updates[i]);
  if(ret){fttr_alarm_invalidate(s,ret,now);return 1;}
 }
 free(s->rows);s->rows=s->snapshot;s->count=s->received;s->snapshot=NULL;
 transient_clear(s);s->valid=true;s->error=0;s->checked=now;s->due=now+60000;
 return 1;
}
static void next_request(struct fttr_alarm_state *s,uint16_t *counter)
{
 fttr_me_job_start(&s->me,s->me.onu,counter,0x4c,2,0,0,NULL,0);
 s->me.request[8]=s->received>>8;s->me.request[9]=s->received;s->me.no_retry=true;
}
void fttr_alarm_start(struct fttr_alarm_state *s,uint16_t onu,uint16_t *counter,int64_t now)
{
 transient_clear(s);s->valid=false;s->active=true;s->header=true;
 s->saw_sequence=false;s->sequence=0;s->error=0;s->expires=now+55000;
 fttr_me_job_start(&s->me,onu,counter,0x4b,2,0,0,NULL,0);
 /* Original mode0 snapshot resets the remote notification sequence to1.
  * Do not retry individual requests against a replaced/expired snapshot. */
 s->me.no_retry=true;
}
int fttr_alarm_reply(struct fttr_alarm_state *s,const struct fttr_omci_message *m,uint16_t *counter,int64_t now)
{
 struct fttr_alarm_row row;
 if(!s->active || !s->me.attempts || m->onu!=s->me.onu || m->length<2 || be16(m->body)!=s->me.transaction)return 0;
 if(now>=s->expires){fttr_alarm_invalidate(s,-ETIMEDOUT,now);return 1;}
 if(!body_valid(m) || m->body[2]!=(s->header?0x2b:0x2c) || be16(m->body+4)!=2 || be16(m->body+6))goto bad;
 if(s->header) {
  s->total=be16(m->body+8);s->header=false;
  if(s->total>FTTR_ALARM_MAX){fttr_alarm_invalidate(s,-E2BIG,now);return 1;}
  if(!s->total)return complete(s,now);
  s->snapshot=calloc(s->total,sizeof(*s->snapshot));
  if(!s->snapshot){fttr_alarm_invalidate(s,-ENOMEM,now);return 1;}
 } else {
  row.class_id=be16(m->body+8);row.instance=be16(m->body+10);memcpy(row.bitmap,m->body+12,28);
  if (!row.class_id) goto bad;
  for(unsigned int i=0;i<s->received;i++)
   if(s->snapshot[i].class_id==row.class_id && s->snapshot[i].instance==row.instance)goto bad;
  s->snapshot[s->received++]=row;
  if(s->received==s->total)return complete(s,now);
 }
 next_request(s,counter);return 2;
bad:
 fttr_alarm_invalidate(s,-EPROTO,now);return 1;
}
int fttr_alarm_notify(struct fttr_alarm_state *s,const struct fttr_omci_message *m,int64_t now)
{
 const uint8_t *p=m->body;struct fttr_alarm_row row;int ret;
 if(m->length<4 || p[2]!=0x10)return 0;
 if(!s->enabled || m->onu!=s->me.onu)return 0;
 if(!body_valid(m) || be16(p) || !p[39]){fttr_alarm_invalidate(s,-EPROTO,now);return 1;}
 if(s->active && s->header){fttr_alarm_invalidate(s,-EAGAIN,now);return 1;}
 if(!s->valid && !s->active)return 0;
 row.class_id=be16(p+4);row.instance=be16(p+6);memcpy(row.bitmap,p+8,28);
 if(!row.class_id){fttr_alarm_invalidate(s,-EPROTO,now);return 1;}
 if(s->saw_sequence && p[39]==s->sequence) {
  if(!memcmp(&s->last_notification,&row,sizeof(row)))return 0;
  fttr_alarm_invalidate(s,-EPROTO,now);return 1;
 }
 if(p[39]!=(s->sequence==255?1:s->sequence+1)){fttr_alarm_invalidate(s,-EPIPE,now);return 1;}
 ret=s->active?upsert(&s->updates,&s->update_count,&row):upsert(&s->rows,&s->count,&row);
 if(ret){fttr_alarm_invalidate(s,ret,now);return 1;}
 s->last_notification=row;s->sequence=p[39];s->saw_sequence=true;s->checked=now;return 1;
}
