// SPDX-License-Identifier: GPL-2.0-only
#include <arpa/inet.h>
#include <errno.h>
#include <net/if.h>
#include <net/if_arp.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct arpreq req = {};
	struct sockaddr_in *ip = (struct sockaddr_in *)&req.arp_pa;
	unsigned int mac[6];
	char tail;
	int fd;

	if (argc != 3 || inet_pton(AF_INET, argv[1], &ip->sin_addr) != 1 ||
	    sscanf(argv[2], "%x:%x:%x:%x:%x:%x%c", &mac[0], &mac[1], &mac[2],
		   &mac[3], &mac[4], &mac[5], &tail) != 6)
		return 2;
	ip->sin_family = AF_INET;
	req.arp_ha.sa_family = ARPHRD_ETHER;
	for (int i = 0; i < 6; i++) {
		if (mac[i] > 255) return 2;
		req.arp_ha.sa_data[i] = mac[i];
	}
	req.arp_flags = ATF_COM | ATF_PERM;
	strcpy(req.arp_dev, "ponfeed0");
	fd = socket(AF_INET, SOCK_DGRAM, 0);
	if (fd < 0 || ioctl(fd, SIOCSARP, &req) < 0) {
		perror("SIOCSARP");
		if (fd >= 0) close(fd);
		return 1;
	}
	close(fd);
	return 0;
}
