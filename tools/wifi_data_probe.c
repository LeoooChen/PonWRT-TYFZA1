/* SPDX-License-Identifier: GPL-2.0-only */
/* Submit raw QoS Data with a known payload through native mac80211. */
#include <arpa/inet.h>
#include <linux/if_packet.h>
#include <linux/if_ether.h>
#include <net/if.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct ifreq interface = {};
	struct sockaddr_ll destination = { .sll_family = AF_PACKET, .sll_protocol = htons(ETH_P_ALL) };
	unsigned int tid, bytes, count, index, offset;
	unsigned char *packet, *frame;
	int fd;

	if (argc != 5) {
		fprintf(stderr, "usage: wifi_data_probe monitor-interface tid bytes count\n");
		return 1;
	}
	tid = strtoul(argv[2], NULL, 0);
	bytes = strtoul(argv[3], NULL, 0);
	count = strtoul(argv[4], NULL, 0);
	if (tid > 7 || bytes < 34 || bytes > 11962)
		return 1;
	packet = calloc(1, bytes + 8);
	if (!packet)
		return 1;
	packet[2] = 8;
	frame = packet + 8;
	fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (fd < 0) { perror("socket"); return 1; }
	strncpy(interface.ifr_name, argv[1], IFNAMSIZ - 1);
	if (ioctl(fd, SIOCGIFINDEX, &interface)) { perror("interface"); return 1; }
	destination.sll_ifindex = interface.ifr_ifindex;
	if (ioctl(fd, SIOCGIFHWADDR, &interface)) { perror("MAC"); return 1; }
	frame[0] = 0x88;
	memset(frame + 4, 0xff, 6);
	memcpy(frame + 10, interface.ifr_hwaddr.sa_data, 6);
	memcpy(frame + 16, interface.ifr_hwaddr.sa_data, 6);
	frame[24] = tid;
	memcpy(frame + 26, "\xaa\xaa\x03\0\0\0\x88\xb5", 8);
	for (offset = 34; offset < bytes; offset++)
		frame[offset] = offset & 255;
	if (bind(fd, (void *)&destination, sizeof(destination))) { perror("bind"); return 1; }
	for (index = 0; index < count; index++) {
		frame[22] = ((index + 1) << 4) & 255;
		frame[23] = ((index + 1) >> 4) & 255;
		if (sendto(fd, packet, bytes + 8, 0, (void *)&destination, sizeof(destination)) != bytes + 8) {
			perror("sendto"); return 1;
		}
		usleep(10000);
	}
	printf("submitted_raw_QoS_data interface=%s tid=%u bytes=%u count=%u\n", argv[1], tid, bytes, count);
	close(fd);
	free(packet);
	return 0;
}
