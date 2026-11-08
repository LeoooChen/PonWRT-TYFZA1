/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_ALARM_H
#define TYFZA1_FTTR_OMCI_ALARM_H
#include "omci-me.h"
#define FTTR_ALARM_MAX 4096
struct fttr_alarm_row { uint16_t class_id, instance; uint8_t bitmap[28]; };
struct fttr_alarm_state {
 struct fttr_me_job me;
 struct fttr_alarm_row *rows, *snapshot, *updates;
 unsigned int count, total, received, update_count;
 struct fttr_alarm_row last_notification;
 uint8_t sequence;
 bool enabled, valid, active, header, saw_sequence;
 int error;
 int64_t due, checked, expires;
};
void fttr_alarm_clear(struct fttr_alarm_state *state);
void fttr_alarm_invalidate(struct fttr_alarm_state *state,int error,int64_t now);
void fttr_alarm_start(struct fttr_alarm_state *state,uint16_t onu,uint16_t *counter,int64_t now);
const char *fttr_alarm_name(uint16_t class_id,unsigned int bit);
/* 0 unrelated, 1 state changed, 2 another request prepared. */
int fttr_alarm_reply(struct fttr_alarm_state *state,const struct fttr_omci_message *message,uint16_t *counter,int64_t now);
int fttr_alarm_notify(struct fttr_alarm_state *state,const struct fttr_omci_message *message,int64_t now);
#endif
