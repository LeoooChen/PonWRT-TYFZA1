// SPDX-License-Identifier: GPL-2.0-or-later
/* CTC parameters backed by native device state. */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "pon-backend.h"

static int fec_read(struct pon_backend *backend)
{
	char path[544], data[256], *upstream;
	FILE *file;
	unsigned int enabled;

	snprintf(path, sizeof(path), "%s/fec", backend->device);
	file = fopen(path, "r");
	if (!file)
		return -errno;
	if (!fgets(data, sizeof(data), file)) {
		fclose(file);
		return -EIO;
	}
	fclose(file);
	upstream = strstr(data, "upstream=");
	if (!strstr(data, "supported=1") || !upstream || sscanf(upstream, "upstream=%u", &enabled) != 1 || enabled > 1)
		return -EPROTO;
	return enabled;
}

static int parameter_get(void *context, uint8_t branch, uint16_t leaf,
	const uint8_t *object, size_t object_size, uint8_t *value, size_t capacity)
{
	struct pon_backend *backend = context;
	int fec;

	(void)object;
	(void)object_size;
	if (branch != 7 || (leaf != 0x139 && leaf != 0x13a))
		return -EOPNOTSUPP;
	if (capacity < 4)
		return -EMSGSIZE;
	fec = fec_read(backend);
	if (fec < 0)
		return fec;
	memset(value, 0, 4);
	value[3] = leaf == 0x139 ? 2 : fec ? 2 : 3;
	return 4;
}

static uint8_t parameter_set(void *context, uint8_t branch, uint16_t leaf,
	const uint8_t *object, size_t object_size, const uint8_t *value, size_t size)
{
	struct pon_backend *backend = context;
	char path[544];
	FILE *file;
	int ret;

	(void)object;
	(void)object_size;
	if (branch != 7 || leaf != 0x13a)
		return 0x87;
	if (size != 4 || value[0] || value[1] || value[2] || (value[3] != 2 && value[3] != 3))
		return 0x86;
	if (fec_read(backend) < 0)
		return 0x87;
	snprintf(path, sizeof(path), "%s/fec", backend->device);
	file = fopen(path, "w");
	if (!file)
		return 0x87;
	ret = fprintf(file, "%u\n", value[3] == 2);
	if (fclose(file) || ret < 0)
		return 0x87;
	/* The response is successful only after the live kernel reads back the mode. */
	return fec_read(backend) == (value[3] == 2) ? 0x80 : 0x87;
}

int pon_backend_init(struct pon_backend *backend, const char *mpcp_path)
{
	const char *slash = strrchr(mpcp_path, '/');
	size_t size;

	if (!slash)
		return -EINVAL;
	size = slash - mpcp_path;
	if (size >= sizeof(backend->device))
		return -ENAMETOOLONG;
	memcpy(backend->device, mpcp_path, size);
	backend->device[size] = 0;
	return 0;
}

void pon_backend_bind(struct pon_oam_identity *identity, struct pon_backend *backend)
{
	identity->backend_context = backend;
	identity->get_parameter = parameter_get;
	identity->set_parameter = parameter_set;
}
