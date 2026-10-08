/* SPDX-License-Identifier: GPL-2.0-only */
/* Capture native LAN ingress before TCP/GRO for DMA validation. */
#include <arpa/inet.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;
static void stop(int signal_number) { (void)signal_number; running = 0; }

int main(int argc, char **argv)
{
	uint32_t global[] = {0xa1b2c3d4, 0x00040002, 0, 0, 2048, 1};
	unsigned char packet[2048];
	struct sockaddr_ll bind_address = {.sll_family = AF_PACKET,
		.sll_protocol = htons(ETH_P_ALL)};
	struct timeval timeout = {.tv_sec = 1};
	int fd;
	FILE *out;

	if (argc != 3) { fprintf(stderr, "usage: lan_capture interface output.pcap\n"); return 1; }
	bind_address.sll_ifindex = if_nametoindex(argv[1]);
	if (!bind_address.sll_ifindex) { perror("if_nametoindex"); return 1; }
	fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (fd < 0) { perror("socket"); return 1; }
	if (bind(fd, (struct sockaddr *)&bind_address, sizeof(bind_address))) { perror("bind"); return 1; }
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
	out = fopen(argv[2], "wb");
	if (!out) { perror("fopen"); return 1; }
	fwrite(global, sizeof(global), 1, out);
	signal(SIGTERM, stop);
	signal(SIGINT, stop);
	while (running) {
		struct sockaddr_ll source;
		socklen_t source_length = sizeof(source);
		struct timeval now;
		uint32_t record[4];
		ssize_t length = recvfrom(fd, packet, sizeof(packet), 0,
			(struct sockaddr *)&source, &source_length);
		if (length < 0 || source.sll_pkttype == PACKET_OUTGOING ||
		    source.sll_ifindex != bind_address.sll_ifindex) continue;
		gettimeofday(&now, NULL);
		record[0] = now.tv_sec; record[1] = now.tv_usec;
		record[2] = record[3] = length;
		fwrite(record, sizeof(record), 1, out);
		fwrite(packet, length, 1, out);
	}
	fclose(out);
	close(fd);
	return 0;
}
