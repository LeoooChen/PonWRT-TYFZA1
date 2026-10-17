// SPDX-License-Identifier: GPL-2.0-only
#include <arpa/inet.h>
#include <errno.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <sys/socket.h>
#include <unistd.h>

#include "omci-socket.h"

int fttr_omci_open(const char *interface, uint16_t ethertype)
{
	struct sockaddr_ll address = {
		.sll_family = AF_PACKET,
		.sll_protocol = htons(ethertype),
	};
	int fd, ret, ignore_outgoing = 1;

	address.sll_ifindex = if_nametoindex(interface);
	if (!address.sll_ifindex)
		return -errno;
	fd = socket(AF_PACKET, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, address.sll_protocol);
	if (fd < 0)
		return -errno;
	ret = setsockopt(fd, SOL_PACKET, PACKET_IGNORE_OUTGOING,
			 &ignore_outgoing, sizeof(ignore_outgoing));
	if (ret < 0)
		goto failed;
	ret = bind(fd, (struct sockaddr *)&address, sizeof(address));
	if (ret < 0)
		goto failed;
	return fd;

failed:
	ret = -errno;
	close(fd);
	return ret;
}

ssize_t fttr_omci_send(int fd, const struct fttr_omci_link *link, uint16_t onu,
		      const uint8_t *body, size_t length)
{
	/* fttr0's frame budget includes room for a possible FCS on receive. */
	uint8_t frame[1996];
	int size = fttr_omci_pack(link, onu, body, length, frame, sizeof(frame));
	ssize_t ret;

	if (size < 0)
		return size;
	ret = send(fd, frame, size, 0);
	return ret < 0 ? -errno : ret;
}

int fttr_omci_receive(int fd, uint16_t ethertype, uint8_t *frame, size_t capacity,
		      struct fttr_omci_message *message)
{
	ssize_t size = recv(fd, frame, capacity, MSG_TRUNC);

	if (size < 0)
		return -errno;
	if ((size_t)size > capacity)
		return -EMSGSIZE;
	return fttr_omci_unpack(frame, size, ethertype, message);
}
