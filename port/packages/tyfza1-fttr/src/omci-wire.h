/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_WIRE_H
#define TYFZA1_FTTR_OMCI_WIRE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Populated from MiniOLT OMCI registers, not from a subordinate model name. */
struct fttr_omci_link {
	uint8_t host_mac[6];
	uint8_t olt_mac[6];
	uint16_t ethertype;
	bool downstream_length;
};

struct fttr_omci_message {
	uint16_t onu;
	const uint8_t *body;
	size_t length; /* OMCI header/content/trailer, excluding its CRC. */
};

uint32_t fttr_omci_crc(const uint8_t *data, size_t length);
int fttr_omci_pack(const struct fttr_omci_link *link, uint16_t onu,
		   const uint8_t *body, size_t length, uint8_t *frame, size_t capacity);
/* Upstream uses 16 bytes. On a payload error, available ONU/TCI metadata is retained. */
int fttr_omci_unpack(const uint8_t *frame, size_t length, uint16_t ethertype,
		     struct fttr_omci_message *message);

#endif
