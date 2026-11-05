/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_CONTROL_H
#define TYFZA1_FTTR_CONTROL_H

#include <stdint.h>

/* Local Unix datagrams, native byte order. Reply is an int32_t errno (or0).
 * The state-directory socket is owned by the controller's MiniOLT lease.
 */
struct fttr_admin_request {
	uint32_t version, id, blocked;
	uint8_t serial[8];
};

#endif
