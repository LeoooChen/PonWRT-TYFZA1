/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_CONTROL_H
#define TYFZA1_FTTR_CONTROL_H

#include <stdint.h>
#include <stddef.h>

/* Local Unix datagrams, native byte order. Reply is an int32_t errno (or0).
 * The state-directory socket is owned by the controller's MiniOLT lease.
 */
struct fttr_admin_request {
	uint32_t version, id, action;
	uint8_t serial[8];
	/* Version2 move request; version1 ends immediately before target.
	 * Version3 image commands use the full size with these fields zero;
	 * download carries exactly one SCM_RIGHTS regular-file descriptor.
	 */
	uint32_t target;
	uint8_t target_serial[8];
};

enum fttr_admin_action { FTTR_ADMIN_RESUME, FTTR_ADMIN_BLOCK, FTTR_ADMIN_DEACTIVATE, FTTR_ADMIN_MOVE, FTTR_ADMIN_REBOOT,
	FTTR_ADMIN_DOWNLOAD, FTTR_ADMIN_ACTIVATE, FTTR_ADMIN_COMMIT, FTTR_ADMIN_UPGRADE_ABORT, FTTR_ADMIN_UPGRADE_CHECK };

#endif
