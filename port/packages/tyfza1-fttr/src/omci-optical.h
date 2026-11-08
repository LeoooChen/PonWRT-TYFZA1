/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_OPTICAL_H
#define TYFZA1_FTTR_OMCI_OPTICAL_H
#include "omci-me.h"
struct fttr_optical_job {
 struct fttr_me_job me;
 uint16_t raw[5], valid;
 bool acknowledged, result, done;
 int error;
};
void fttr_optical_start(struct fttr_optical_job *job, uint16_t onu, uint16_t instance, uint16_t *counter);
/* Result and ACK may arrive in either order; only their matching pair completes. */
int fttr_optical_reply(struct fttr_optical_job *job, const struct fttr_omci_message *message);
void fttr_optical_cancel(struct fttr_optical_job *job, int error);
int fttr_optical_tick(struct fttr_optical_job *job, int64_t now);
/* Factory units: centivolts, centidBm, centidBm, centimA, centidegrees C. */
int32_t fttr_optical_value(unsigned int index, uint16_t raw);
#endif
