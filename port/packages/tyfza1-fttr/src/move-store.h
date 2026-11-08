/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_MOVE_STORE_H
#define TYFZA1_FTTR_MOVE_STORE_H
#include "miniolt.h"
#include "fttr-control.h"

/* One controller serializes migrations; same lease/directory as services. */
struct fttr_move_record {
 struct fttr_admin_request request;
 enum { FTTR_MOVE_RELEASE = 1, FTTR_MOVE_RESERVATION } stage;
 bool service;
};
int fttr_move_store_save(int directory, const struct fttr_omci_link *link,
 enum fttr_backend backend, const struct fttr_move_record *record);
int fttr_move_store_load(int directory, const struct fttr_omci_link *link,
 enum fttr_backend backend, struct fttr_move_record *record);
int fttr_move_store_remove(int directory);
/* 0 original eligible pair, 1 complete reservation, negative partial/conflict. */
int fttr_move_observe(const struct fttr_move_record *record,
 const struct fttr_onu_state *source, const struct fttr_onu_state *target);
#endif
