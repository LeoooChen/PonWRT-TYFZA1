// SPDX-License-Identifier: GPL-2.0-or-later
/* Apply the selected ITU protocol's identity to its native MAC interface. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <uci.h>
#include "pon-config.h"

static int hex_digit(char value)
{
	if (value >= '0' && value <= '9')
		return value - '0';
	if (value >= 'a' && value <= 'f')
		return value - 'a' + 10;
	if (value >= 'A' && value <= 'F')
		return value - 'A' + 10;
	return -1;
}

static int serial_decode(const char *text, uint8_t output[8])
{
	size_t length = strlen(text), offset;
	unsigned int i;

	if (length != 12 && length != 16)
		return -EINVAL;
	offset = length == 12 ? 4 : 0;
	if (offset) {
		for (i = 0; i < offset; i++)
			if ((unsigned char)text[i] < 0x20 || (unsigned char)text[i] > 0x7e)
				return -EINVAL;
		memcpy(output, text, offset);
	}
	for (i = offset; i < 8; i++) {
		int high = hex_digit(text[offset + (i - offset) * 2]);
		int low = hex_digit(text[offset + (i - offset) * 2 + 1]);

		if (high < 0 || low < 0)
			return -EINVAL;
		output[i] = high * 16 + low;
	}
	return 0;
}

int main(int argc, char **argv)
{
	struct pon_config config = {};
	const char *serial, *password, *mode;
	uint8_t identity[44] = {};
	int fd, error, result = 1;
	size_t length;
	size_t password_size = 10;

	if (argc != 2) {
		fprintf(stderr, "Usage: pon-identity /sys/.../gpon_identity or xgpon_identity\n");
		return 2;
	}
	error = pon_config_load(&config, "/etc/config");
	if (error) {
		fprintf(stderr, "PON configuration load: %s\n", strerror(-error));
		goto out;
	}
	serial = pon_config_option(&config, "serial_number");
	mode = pon_config_option(&config, "mode");
	if (mode && (!strcmp(mode, "xgpon") || !strcmp(mode, "xgspon"))) {
		password_size = 36;
		password = pon_config_option(&config, "registration_id");
	} else {
		password = pon_config_option(&config, "ploam_password");
	}
	if (!serial || serial_decode(serial, identity)) {
		fprintf(stderr, "PON serial_number must be VVVVXXXXXXXX or 16 hex digits\n");
		goto out;
	}
	length = password ? strlen(password) : 0;
	if (length > password_size) {
		fprintf(stderr, "PON authentication value exceeds %zu bytes\n", password_size);
		goto out;
	}
	memcpy(identity + 8, password ? password : "", length);
	fd = open(argv[1], O_WRONLY | O_CLOEXEC);
	if (fd < 0) {
		perror("PON identity interface");
		goto out;
	}
	if (write(fd, identity, 8 + password_size) != (ssize_t)(8 + password_size))
		perror("PON identity write");
	else
		result = 0;
	close(fd);
out:
	uci_free_context(config.context);
	return result;
}
