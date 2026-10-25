// SPDX-License-Identifier: GPL-2.0-or-later
/* CTC parameters backed by native device state. */
#include <errno.h>
#include <arpa/inet.h>
#include <linux/ethtool.h>
#include <linux/sockios.h>
#include <net/if.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/random.h>
#include <unistd.h>
#include "pon-backend.h"

static uint32_t get32(const uint8_t *value)
{
	return (uint32_t)value[0] << 24 | (uint32_t)value[1] << 16 |
		(uint32_t)value[2] << 8 | value[3];
}

static void put32(uint8_t *value, uint32_t number)
{
	number = htonl(number);
	memcpy(value, &number, sizeof(number));
}

static int port_number(const struct pon_oam_request *request)
{
	const uint8_t *object = request->object;
	unsigned int port;

	if (!object || object[1] || object[2] != 1)
		return -EINVAL;
	if (request->object_size == 5) {
		port = object[4];
	} else if (request->object_size == 8 && object[4] == 1 && !object[5]) {
		port = (unsigned int)object[6] << 8 | object[7];
	} else {
		return -EINVAL;
	}
	/* CTC UNI and native lan1..4 now both follow the enclosure labels. */
	return port >= 1 && port <= 4 ? (int)port - 1 : -EINVAL;
}

static int port_ioctl(unsigned int port, unsigned long operation, struct ifreq *request)
{
	int fd, ret;

	snprintf(request->ifr_name, sizeof(request->ifr_name), "lan%u", port + 1);
	fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -errno;
	ret = ioctl(fd, operation, request) ? -errno : 0;
	close(fd);
	return ret;
}

static int port_ethtool(unsigned int port, void *command)
{
	struct ifreq request = {.ifr_data = command};

	return port_ioctl(port, SIOCETHTOOL, &request);
}

static int technology(uint32_t abilities, bool advertised, uint8_t *value)
{
	/* Wire enumerations from eponoamd:0x41d3f8/0x41d62c. */
	static const struct { uint32_t mask, code; } modes[] = {
		{ADVERTISED_10baseT_Half, 0xe}, {ADVERTISED_10baseT_Full, 0x8e},
		{ADVERTISED_100baseT_Half, 0x19}, {ADVERTISED_100baseT_Full, 0xfc},
		{ADVERTISED_1000baseT_Full, 0x138}, {ADVERTISED_10000baseT_Full, 0x139},
		{ADVERTISED_1000baseT_Half, 0x192},
	};
	unsigned int count = 0, i;

	if (!advertised) {
		if (abilities & (SUPPORTED_10baseT_Half | SUPPORTED_10baseT_Full))
			put32(value + 4 * ++count, 0xe);
		if (abilities & (SUPPORTED_100baseT_Half | SUPPORTED_100baseT_Full))
			put32(value + 4 * ++count, 0x19);
		if (abilities & (SUPPORTED_1000baseT_Half | SUPPORTED_1000baseT_Full))
			put32(value + 4 * ++count, 0x28);
	} else {
		for (i = 0; i < sizeof(modes) / sizeof(modes[0]); i++)
			if (abilities & modes[i].mask)
				put32(value + 4 * ++count, modes[i].code);
	}
	put32(value, count);
	return 4 * (count + 1);
}

static int port_get(const struct pon_oam_request *request, uint8_t *value)
{
	struct ethtool_cmd settings = {.cmd = ETHTOOL_GSET};
	struct ifreq interface = {};
	int port = port_number(request), ret;

	if (port < 0)
		return port;
	if ((request->branch == 7 && request->leaf == 0x25) ||
	    (request->branch == 0xc7 && request->leaf == 0x11)) {
		ret = port_ioctl(port, SIOCGIFFLAGS, &interface);
		if (ret)
			return ret;
		if (request->branch == 7) {
			put32(value, interface.ifr_flags & IFF_UP ? 2 : 1);
			return 4;
		}
		value[0] = !!(interface.ifr_flags & IFF_RUNNING);
		return 1;
	}
	ret = port_ethtool(port, &settings);
	if (ret)
		return ret;
	if (request->branch == 7 && request->leaf == 0x4f) {
		put32(value, settings.autoneg == AUTONEG_ENABLE ? 2 : 1);
		return 4;
	}
	if (request->branch == 7 && request->leaf == 0x52)
		return technology(settings.supported, false, value);
	if (request->branch == 7 && request->leaf == 0x53)
		return technology(settings.advertising, true, value);
	if (request->branch == 0xc7 && request->leaf == 0x1e) {
		unsigned int speed = ethtool_cmd_speed(&settings);

		value[0] = speed == 1000 ? 5 : speed == 100 ?
			(settings.duplex == DUPLEX_FULL ? 4 : 3) : speed == 10 ?
			(settings.duplex == DUPLEX_FULL ? 2 : 1) : 0;
		return 1;
	}
	if (request->branch == 0xc7 && request->leaf == 0x12) {
		value[0] = !!(settings.advertising & ADVERTISED_Pause);
		return 1;
	}
	return -EOPNOTSUPP;
}

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

static int transceiver_get(struct pon_backend *backend, uint8_t *value)
{
	char path[544];
	FILE *file;
	int temperature, fields;
	unsigned int voltage, bias, tx_power, rx_power;
	uint16_t raw[5];

	snprintf(path, sizeof(path), "%s/transceiver", backend->device);
	file = fopen(path, "r");
	if (!file)
		return -errno;
	fields = fscanf(file, "temperature_raw=%d voltage_raw=%u bias_raw=%u tx_power_raw=%u rx_power_raw=%u",
		&temperature, &voltage, &bias, &tx_power, &rx_power);
	fclose(file);
	if (fields != 5)
		return -EIO;
	/* eponoamd:0x412838 reads SFF words3..7 without unit conversion;
	 * 0x41edd8 encodes them in temperature/voltage/bias/TX/RX order. */
	raw[0] = htons((uint16_t)temperature);
	raw[1] = htons(voltage);
	raw[2] = htons(bias);
	raw[3] = htons(tx_power);
	raw[4] = htons(rx_power);
	memcpy(value, raw, sizeof(raw));
	return sizeof(raw);
}

static int capabilities_get(struct pon_backend *backend, uint16_t leaf, uint8_t *value)
{
	char path[544];
	FILE *file;
	unsigned int port, gigabit = 0, fast = 0, llids;
	uint64_t gigabit_mask = 0, fast_mask = 0;
	int fields, ret;

	if (leaf == 0xc) {
		snprintf(path, sizeof(path), "%s/llid_count", backend->device);
		file = fopen(path, "r");
		if (!file)
			return -errno;
		fields = fscanf(file, "%u", &llids);
		fclose(file);
		if (fields != 1)
			return -EIO;
		value[0] = 1; /* IPv6 aware. */
		value[1] = 0; /* Transmitter control is local-only, not an OAM capability. */
		value[2] = llids;
		return 3;
	}
	for (port = 0; port < 4; port++) {
		struct ethtool_cmd settings = {.cmd = ETHTOOL_GSET};

		ret = port_ethtool(port, &settings);
		if (ret)
			return ret;
		if (settings.supported & (SUPPORTED_1000baseT_Full | SUPPORTED_1000baseT_Half)) {
			gigabit++;
			gigabit_mask |= UINT64_C(1) << port;
		} else if (settings.supported & (SUPPORTED_100baseT_Full | SUPPORTED_100baseT_Half)) {
			fast++;
			fast_mask |= UINT64_C(1) << port;
		}
	}
	if (leaf == 4) {
		memset(value, 0, 26);
		value[0] = (gigabit ? 1 : 0) | (fast ? 2 : 0);
		value[1] = gigabit;
		value[10] = fast;
		for (port = 0; port < 8; port++) {
			value[9 - port] = gigabit_mask >> (port * 8);
			value[18 - port] = fast_mask >> (port * 8);
		}
		/* Native EPON PUC/PDC provide eight CoS queues per port. */
		value[21] = value[22] = value[23] = value[24] = 8;
		return 26;
	}
	/* The factory HGU layout has five interface-type records. Report only
	 * interfaces with a native consumer; no fictitious voice service. */
	memset(value, 0, 40);
	value[4] = 1;
	value[6] = 1;
	value[8] = 5;
	value[14] = gigabit;
	value[18] = 1;
	value[20] = fast;
	value[24] = 2;
	value[30] = 7;
	value[36] = 6;
	return 40;
}

static int sla_get(struct pon_backend *backend, uint8_t *value)
{
	uint8_t raw[89];
	char path[544];
	FILE *file;
	int ret;

	snprintf(path, sizeof(path), "%s/epon_sla", backend->device);
	file = fopen(path, "rb");
	if (!file)
		return -errno;
	ret = fseek(file, backend->llid * sizeof(raw), SEEK_SET);
	if (!ret)
		ret = fread(raw, 1, sizeof(raw), file) == sizeof(raw) ? 0 : -EIO;
	fclose(file);
	if (ret)
		return ret;
	if (!raw[0] || raw[0] > 88)
		return -EPROTO;
	memcpy(value, raw + 1, raw[0]);
	return raw[0];
}

static int sla_set(struct pon_backend *backend, const uint8_t *value, size_t size)
{
	uint8_t raw[97];
	char path[544];
	FILE *file;
	int ret;

	if (!size || size > 88)
		return -EINVAL;
	raw[0] = backend->llid;
	for (unsigned int byte = 0; byte < 8; byte++)
		raw[1 + byte] = backend->epoch >> (56 - byte * 8);
	memcpy(raw + 9, value, size);
	snprintf(path, sizeof(path), "%s/epon_sla", backend->device);
	file = fopen(path, "wb");
	if (!file)
		return -errno;
	ret = fwrite(raw, 1, size + 9, file) == size + 9 ? 0 : -EIO;
	if (fclose(file) && !ret)
		ret = -errno;
	return ret;
}

static int holdover_get(struct pon_backend *backend, uint8_t *value)
{
	uint8_t raw[4];
	char path[544];
	FILE *file;
	int ret;

	snprintf(path, sizeof(path), "%s/epon_holdover", backend->device);
	file = fopen(path, "rb");
	if (!file)
		return -errno;
	ret = fseek(file, backend->llid * sizeof(raw), SEEK_SET);
	if (!ret)
		ret = fread(raw, 1, sizeof(raw), file) == sizeof(raw) ? 0 : -EIO;
	fclose(file);
	if (ret)
		return ret;
	put32(value, ((unsigned int)raw[0] << 8 | raw[1]) + 1);
	put32(value + 4, (unsigned int)raw[2] << 8 | raw[3]);
	return 8;
}

static int holdover_set(struct pon_backend *backend, const uint8_t *value, size_t size)
{
	uint8_t raw[12];
	uint32_t state, duration;
	char path[544];
	FILE *file;
	int ret;

	if (size != 8)
		return -EINVAL;
	state = get32(value);
	duration = get32(value + 4);
	if ((state != 1 && state != 2) || duration > 65535)
		return -EINVAL;
	raw[0] = backend->llid;
	for (unsigned int byte = 0; byte < 8; byte++)
		raw[1 + byte] = backend->epoch >> (56 - byte * 8);
	raw[9] = state - 1;
	raw[10] = duration >> 8;
	raw[11] = duration;
	snprintf(path, sizeof(path), "%s/epon_holdover", backend->device);
	file = fopen(path, "wb");
	if (!file)
		return -errno;
	ret = fwrite(raw, 1, sizeof(raw), file) == sizeof(raw) ? 0 : -EIO;
	if (fclose(file) && !ret)
		ret = -errno;
	return ret;
}

static int parameter_get(void *context, const struct pon_oam_request *request,
	uint8_t *value, size_t capacity)
{
	struct pon_backend *backend = context;
	int fec;

	uint8_t branch = request->branch;
	uint16_t leaf = request->leaf;
	if (branch == 0xc7 && leaf == 6)
		return sla_get(backend, value);
	if (branch == 0xc7 && leaf == 8)
		return holdover_get(backend, value);
	if (branch == 0xc7 && leaf == 5)
		return transceiver_get(backend, value);
	if (branch == 0xc7 && (leaf == 4 || leaf == 7 || leaf == 0xc))
		return capabilities_get(backend, leaf, value);
	if ((branch == 7 && (leaf == 0x25 || leaf == 0x4f || leaf == 0x52 || leaf == 0x53)) ||
	    (branch == 0xc7 && (leaf == 0x11 || leaf == 0x12 || leaf == 0x1e)))
		return port_get(request, value);
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

static uint8_t parameter_set(void *context, const struct pon_oam_request *request,
	const uint8_t *value, size_t size)
{
	struct pon_backend *backend = context;
	char path[544];
	FILE *file;
	int ret;

	uint8_t branch = request->branch;
	uint16_t leaf = request->leaf;
	if (branch == 0xc7 && leaf == 8) {
		ret = holdover_set(backend, value, size);
		backend->management_error = ret;
		return !ret ? 0x80 : ret == -EINVAL ? 0x86 : 0x87;
	}
	if (branch == 0xc7 && leaf == 6) {
		ret = sla_set(backend, value, size);
		backend->management_error = ret;
		return !ret ? 0x80 : ret == -EINVAL ? 0x86 : 0x87;
	}
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

static int churning_key(void *context, uint8_t index, uint8_t key[9], size_t length)
{
	struct pon_backend *backend = context;
	char path[544], mode[32];
	uint8_t data[19];
	FILE *file;
	unsigned int width;
	int fields, ret;

	snprintf(path, sizeof(path), "%s/serdes", backend->device);
	file = fopen(path, "r");
	if (!file)
		return -errno;
	fields = fscanf(file, "mode=%31s", mode);
	fclose(file);
	if (fields != 1)
		return -EIO;
	if (!strcmp(mode, "epon"))
		width = 3;
	else if (!strcmp(mode, "epon-d10g") || !strcmp(mode, "epon-bi10g"))
		width = 9;
	else
		return -EOPNOTSUPP;
	if (length && length != width)
		return -ESTALE;
	if (!length) {
		ret = getrandom(key, width, 0);
		if (ret != (int)width)
			return ret < 0 ? -errno : -EIO;
	}
	data[0] = backend->llid;
	data[1] = index;
	for (unsigned int byte = 0; byte < 8; byte++)
		data[2 + byte] = backend->epoch >> (56 - byte * 8);
	memcpy(data + 10, key, width);
	snprintf(path, sizeof(path), "%s/epon_key", backend->device);
	file = fopen(path, "wb");
	if (!file)
		return -errno;
	ret = fwrite(data, 1, width + 10, file) == width + 10 ? 0 : -EIO;
	if (fclose(file) && !ret)
		ret = -errno;
	return ret ? ret : (int)width;
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
	identity->churning_key = churning_key;
}
