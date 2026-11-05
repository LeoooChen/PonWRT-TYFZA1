/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef PON_BACKEND_H
#define PON_BACKEND_H
#include "pon-oam.h"
struct pon_backend {
	char device[512];
	unsigned int llid;
	uint64_t epoch;
	int management_error;
};
int pon_backend_init(struct pon_backend *backend, const char *mpcp_path);
void pon_backend_bind(struct pon_oam_identity *identity, struct pon_backend *backend);
#endif
