/* SPDX-License-Identifier: GPL-2.0-only */
/* Capture both directions of a single native OAM interface for wire diagnosis. */
#include <arpa/inet.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct sockaddr_ll address = {.sll_family = AF_PACKET, .sll_protocol = htons(0x0003)};
	struct timeval timeout = {.tv_sec = 1};
	uint32_t header[] = {0xa1b2c3d4, 0x00040002, 0, 0, 2048, 1};
	unsigned char frame[2048];
	time_t end;
	FILE *file;
	int fd;

	if (argc != 4)
		return 2;
	address.sll_ifindex = if_nametoindex(argv[1]);
	if (!address.sll_ifindex)
		return 1;
	fd = socket(AF_PACKET, SOCK_RAW, htons(0x0003));
	if (fd < 0 || bind(fd, (struct sockaddr *)&address, sizeof(address)))
		return 1;
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	file = fopen(argv[2], "wb");
	if (!file)
		return 1;
	fwrite(header, sizeof(header), 1, file);
	end = time(NULL) + strtoul(argv[3], NULL, 10);
	while (time(NULL) < end) {
		struct sockaddr_ll source;
		socklen_t size = sizeof(source);
		struct timeval now;
		uint32_t record[4];
		ssize_t length = recvfrom(fd, frame, sizeof(frame), 0, (struct sockaddr *)&source, &size);

		if (length < 15 || source.sll_ifindex != address.sll_ifindex ||
		    frame[12] != 0x88 || frame[13] != 9 || frame[14] != 3)
			continue;
		gettimeofday(&now, NULL);
		record[0] = now.tv_sec; record[1] = now.tv_usec;
		record[2] = record[3] = length;
		fwrite(record, sizeof(record), 1, file);
		fwrite(frame, length, 1, file);
	}
	fclose(file);
	close(fd);
	return 0;
}
