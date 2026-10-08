/* SPDX-License-Identifier: GPL-2.0-only */
/* Capture native PON control-interface Ethernet frames in pcap format. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct ifreq interface = {};
	struct sockaddr_ll address = {.sll_family = AF_PACKET, .sll_protocol = htons(0x8809)};
	struct timeval timeout = {.tv_sec = 1};
	uint32_t pcap[] = {0xa1b2c3d4, 0x00040002, 0, 0, 2048, 1};
	unsigned char frame[2048];
	unsigned int packets = 0, bytes = 0, seconds;
	int fd;
	FILE *output;
	struct timespec start, now;

	if (argc != 4) { fprintf(stderr, "usage: pon_oam_capture interface seconds output.pcap\n"); return 1; }
	seconds = strtoul(argv[2], NULL, 0);
	fd = socket(AF_PACKET, SOCK_RAW, htons(0x8809));
	if (fd < 0) { perror("socket"); return 1; }
	strncpy(interface.ifr_name, argv[1], IFNAMSIZ - 1);
	if (ioctl(fd, SIOCGIFINDEX, &interface)) { perror("ifindex"); return 1; }
	address.sll_ifindex = interface.ifr_ifindex;
	if (bind(fd, (struct sockaddr *)&address, sizeof(address))) { perror("bind"); return 1; }
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	output = fopen(argv[3], "wb");
	if (!output) { perror("output"); return 1; }
	fwrite(pcap, sizeof(pcap), 1, output);
	clock_gettime(CLOCK_MONOTONIC, &start);
	for (;;) {
		struct timeval stamp;
		uint32_t record[4];
		ssize_t length = recv(fd, frame, sizeof(frame), 0);

		if (length > 0) {
			gettimeofday(&stamp, NULL);
			record[0] = stamp.tv_sec; record[1] = stamp.tv_usec;
			record[2] = length; record[3] = length;
			fwrite(record, sizeof(record), 1, output);
			fwrite(frame, length, 1, output);
			fflush(output);
			packets++; bytes += length;
		}
		clock_gettime(CLOCK_MONOTONIC, &now);
		if (now.tv_sec - start.tv_sec >= seconds)
			break;
	}
	fclose(output); close(fd);
	printf("{\"packets\":%u,\"bytes\":%u,\"seconds\":%u}\n", packets, bytes, seconds);
	return 0;
}
