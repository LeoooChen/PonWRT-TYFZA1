/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TYFZA1_FTTR_OMCI_WIFI_H
#define TYFZA1_FTTR_OMCI_WIFI_H

#include "omci-model.h"
#include "omci-objects.h"

/* Values are the original ff69/ff6a protocol codes, not hostapd enums.
 * Lengths count bytes; the caller must report the complete input length. */
struct fttr_wifi_config {
	uint16_t instance;
	uint8_t mode, channel, bandwidth, security, encryption;
	uint8_t key_auth, key_index;
	bool ssid_enabled, radio_enabled;
	const uint8_t *ssid, *shared_key, *keys[4];
	size_t ssid_size, shared_key_size, key_size[4];
};

struct fttr_wifi_job {
	struct fttr_objects_job transaction;
	struct fttr_omci_object objects[9];
};

/* Requires the actual MIB's existing ff69 and ff6a instance. The shared ME
 * transaction reads every group before writing and verifies/restores each. */
int fttr_wifi_job_start(struct fttr_wifi_job *job, uint16_t onu, uint16_t *counter,
		const struct fttr_mib_model *model, const struct fttr_wifi_config *config,
		const uint8_t serial[8]);
/* Diagnostic output excludes all SSID/key values and raw exchanges. */
int fttr_wifi_job_dump(const struct fttr_wifi_job *job, FILE *file);

#endif
