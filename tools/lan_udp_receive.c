/* SPDX-License-Identifier: GPL-2.0-only */
/* Sequenced UDP ingress measurement for native TYFZA1 DMA validation. */
#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	unsigned char packet[2048];
	unsigned char *seen;
	struct sockaddr_in address = {.sin_family = AF_INET, .sin_addr.s_addr = INADDR_ANY};
	struct timeval timeout = {.tv_sec = 5};
	unsigned int count, received = 0, unique = 0, duplicate = 0, bad = 0, reordered = 0, last = 0;
	int fd = socket(AF_INET, SOCK_DGRAM, 0), size = 4 * 1024 * 1024;

	if (argc != 3) { fprintf(stderr, "usage: lan_udp_receive port count\n"); return 1; }
	address.sin_port = htons(atoi(argv[1]));
	count = atoi(argv[2]);
	seen = calloc(count, 1);
	setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &size, sizeof(size));
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	if (bind(fd, (struct sockaddr *)&address, sizeof(address))) { perror("bind"); return 1; }
	puts("UDP ready"); fflush(stdout);
	for (;;) {
		uint32_t sequence;
		ssize_t length = recv(fd, packet, sizeof(packet), 0);
		if (length < 0) break;
		received++;
		if (length != 1400) { bad++; continue; }
		sequence = (uint32_t)packet[0] << 24 | (uint32_t)packet[1] << 16 |
			(uint32_t)packet[2] << 8 | packet[3];
		if (sequence >= count) { bad++; continue; }
		for (unsigned int i = 4; i < 1400; i++)
			if (packet[i] != packet[i % 4]) { bad++; break; }
		if (sequence < last) reordered++;
		last = sequence;
		if (seen[sequence]) duplicate++;
		else { seen[sequence] = 1; unique++; }
	}
	printf("{\"expected\":%u,\"received\":%u,\"unique\":%u,\"missing\":%u,\"duplicate\":%u,\"bad\":%u,\"reordered\":%u}\n",
		count, received, unique, count - unique, duplicate, bad, reordered);
	free(seen); close(fd);
	return 0;
}
