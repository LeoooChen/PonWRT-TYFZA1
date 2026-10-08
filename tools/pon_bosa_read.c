/* SPDX-License-Identifier: GPL-2.0-only */
/* Read the original BOSA bus: identity, diagnostics and Semtech device ID. */
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int read_register(int fd, unsigned int address, unsigned char reg,
			 unsigned char *data, unsigned int length)
{
	struct i2c_msg messages[] = {
		{.addr = address, .len = 1, .buf = &reg},
		{.addr = address, .flags = I2C_M_RD, .len = length, .buf = data},
	};
	struct i2c_rdwr_ioctl_data transfer = {.msgs = messages, .nmsgs = 2};

	return ioctl(fd, I2C_RDWR, &transfer) == 2 ? 0 : -1;
}

static int select_page(int fd, unsigned char page)
{
	unsigned char data[] = {0x7f, page};
	struct i2c_msg message = {.addr = 0x51, .len = 2, .buf = data};
	struct i2c_rdwr_ioctl_data transfer = {.msgs = &message, .nmsgs = 1};

	return ioctl(fd, I2C_RDWR, &transfer) == 1 ? 0 : -1;
}

static int dump(int fd, unsigned int address, unsigned char start, unsigned int length)
{
	unsigned char data[128];

	if (read_register(fd, address, start, data, length)) { perror("read register"); return 1; }
	printf("address=%02x reg=%02x data=", address, start);
	for (unsigned int i = 0; i < length; i++)
		printf("%02x", data[i]);
	puts("");
	return 0;
}

int main(int argc, char **argv)
{
	unsigned char page;
	int fd, ret;

	if (argc != 2 && (argc != 3 || strcmp(argv[2], "--a0"))) {
		fprintf(stderr, "usage: pon_bosa_read /dev/i2c-N [--a0]\n");
		return 1;
	}
	fd = open(argv[1], O_RDWR);
	if (fd < 0) { perror("open"); return 1; }
	/* Semtech MCU mode exposes A2 only; A0 is an explicit optional read. */
	if (argc == 3) {
		ret = dump(fd, 0x50, 0, 96);
		if (ret)
			return ret;
	}
	ret = dump(fd, 0x51, 0x60, 32);
	if (ret)
		return ret;
	if (read_register(fd, 0x51, 0x7f, &page, 1)) { perror("page"); return 1; }
	if (select_page(fd, 0xff)) { perror("select page FF"); return 1; }
	ret = dump(fd, 0x51, 0x80, 16);
	if (!ret) {
		if (select_page(fd, 2)) { perror("select page2"); ret = 1; }
		else ret = dump(fd, 0x51, 0xc0, 32);
	}
	if (select_page(fd, page)) { perror("restore page"); ret = 1; }
	close(fd);
	return ret;
}
