/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_OMCI_H
#define TYFZA1_OMCI_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
struct omci_identity {
	uint8_t serial[8], equipment[20], hardware[14], software[14];
	uint8_t loid[24], password[12], operator_id[4];
	uint8_t psk[16], has_psk;
};
struct omci_agent;
struct omci_agent *tyfza1_omci_new(const struct omci_identity *identity);
void tyfza1_omci_free(struct omci_agent *agent);
int tyfza1_omci_dispatch(struct omci_agent *agent, const uint8_t *data, size_t length, uint8_t *output, int backend_result);
int tyfza1_omci_broadcast(const uint8_t *data, size_t length, uint8_t row[18]);
int tyfza1_omci_paths(struct omci_agent *agent, uint8_t *output);
int tyfza1_omci_session_key(struct omci_agent *agent, uint8_t *output);
void tyfza1_omci_key_complete(struct omci_agent *agent, bool installed);
int tyfza1_omci_notification(struct omci_agent *agent, uint8_t *output);
void tyfza1_omci_epoch(struct omci_agent *agent);
#endif
