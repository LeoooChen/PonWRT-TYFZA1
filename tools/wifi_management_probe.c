/* SPDX-License-Identifier: GPL-2.0-only */
/* Submit one real broadcast management frame through native mac80211 TX. */
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
	const char ssid[] = "PonWRT-TYFZA1-TX";
	struct ifreq interface = {};
	struct sockaddr_ll destination = { .sll_family = AF_PACKET, .sll_protocol = htons(ETH_P_ALL) };
	unsigned char packet[128] = { 0, 0, 8, 0, 0, 0, 0, 0 };
	unsigned char *frame = packet + 8;
	unsigned int length = 36, count = argc == 3 ? strtoul(argv[2], NULL, 0) : 1, i;
	int fd;

	if (argc != 2 && argc != 3) {
		fprintf(stderr, "usage: wifi_management_probe monitor-interface [count]\n");
		return 1;
	}
	fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (fd < 0) { perror("socket"); return 1; }
	strncpy(interface.ifr_name, argv[1], IFNAMSIZ - 1);
	if (ioctl(fd, SIOCGIFINDEX, &interface)) { perror("interface"); return 1; }
	destination.sll_ifindex = interface.ifr_ifindex;
	if (ioctl(fd, SIOCGIFHWADDR, &interface)) { perror("MAC"); return 1; }
	frame[0] = 0x80; /* Beacon; mac80211 sees raw management and no FCS. */
	memset(frame + 4, 0xff, 6);
	memcpy(frame + 10, interface.ifr_hwaddr.sa_data, 6);
	memcpy(frame + 16, interface.ifr_hwaddr.sa_data, 6);
	frame[22] = 0x10;
	frame[32] = 100;
	frame[34] = 1;
	frame[length++] = 0;
	frame[length++] = sizeof(ssid) - 1;
	memcpy(frame + length, ssid, sizeof(ssid) - 1);
	length += sizeof(ssid) - 1;
	frame[length++] = 1;
	frame[length++] = 1;
	frame[length++] = 0x8c; /* 6Mb/s basic rate for the 5GHz probe. */
	if (bind(fd, (void *)&destination, sizeof(destination))) { perror("bind"); return 1; }
	for (i = 0; i < count; i++) {
		frame[22] = ((i + 1) << 4) & 255;
		frame[23] = ((i + 1) >> 4) & 255;
		if (sendto(fd, packet, length + 8, 0, (void *)&destination, sizeof(destination)) != length + 8) {
			perror("sendto"); return 1;
		}
		usleep(10000);
	}
	printf("submitted_management_frames interface=%s count=%u bytes=%u\n", argv[1], count, length);
	close(fd);
	return 0;
}
