/* SPDX-License-Identifier: GPL-2.0-only */
/* Submit a raw EPON OAM Information PDU through the native control netdev. */
#include <arpa/inet.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	struct ifreq interface = {};
	struct sockaddr_ll address = {.sll_family = AF_PACKET, .sll_protocol = htons(0x8809)};
	unsigned char frame[60] = {1, 0x80, 0xc2, 0, 0, 2};
	unsigned int count, llid;
	char llid_path[96];
	FILE *llid_file;
	int fd;

	if (argc != 3) {
		fprintf(stderr, "usage: pon_oam_probe interface count\n");
		return 1;
	}
	count = strtoul(argv[2], NULL, 0);
	fd = socket(AF_PACKET, SOCK_RAW, htons(0x8809));
	if (fd < 0) { perror("socket"); return 1; }
	strncpy(interface.ifr_name, argv[1], IFNAMSIZ - 1);
	if (ioctl(fd, SIOCGIFINDEX, &interface)) { perror("ifindex"); return 1; }
	address.sll_ifindex = interface.ifr_ifindex;
	snprintf(llid_path, sizeof(llid_path), "/sys/class/net/%s/llid_index", interface.ifr_name);
	llid_file = fopen(llid_path, "r");
	if (!llid_file) { perror("llid_index"); return 1; }
	if (fscanf(llid_file, "%u", &llid) != 1 || llid >= 32) {
		fprintf(stderr, "Interface has no valid driver LLID binding\n");
		fclose(llid_file); return 1;
	}
	fclose(llid_file);
	if (ioctl(fd, SIOCGIFHWADDR, &interface)) { perror("hwaddr"); return 1; }
	memcpy(frame + 6, interface.ifr_hwaddr.sa_data, 6);
	frame[12] = 0x88; frame[13] = 9; frame[14] = 3;
	/* Information opcode0 and a version1 Local Information TLV. */
	frame[18] = 1; frame[19] = 16; frame[20] = 1;
	frame[22] = 1; frame[24] = 1; frame[25] = 5; frame[26] = 0xee;
	if (bind(fd, (struct sockaddr *)&address, sizeof(address))) { perror("bind"); return 1; }
	for (unsigned int i = 0; i < count; i++)
		if (send(fd, frame, sizeof(frame), 0) != sizeof(frame)) { perror("send"); return 1; }
	printf("{\"submitted\":%u,\"frame_bytes\":60,\"LLID_index\":%u}\n", count, llid);
	close(fd);
	return 0;
}
