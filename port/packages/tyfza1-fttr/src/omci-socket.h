/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_SOCKET_H
#define TYFZA1_FTTR_OMCI_SOCKET_H

#include <sys/types.h>
#include "omci-wire.h"

/* Returns a nonblocking AF_PACKET fd, or negative errno. The caller closes it. */
int fttr_omci_open(const char *interface, uint16_t ethertype);
ssize_t fttr_omci_send(int fd, const struct fttr_omci_link *link, uint16_t onu,
		      const uint8_t *body, size_t length);
int fttr_omci_receive(int fd, uint16_t ethertype, uint8_t *frame, size_t capacity,
		      struct fttr_omci_message *message);

#endif
