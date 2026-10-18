/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef FTTR_NET_H
#define FTTR_NET_H

#include <stdbool.h>
#include <stdint.h>
#include <net/if.h>

struct fttr_net {
	int fd, parent;
	uint32_t sequence;
};

struct fttr_net_port {
	int index, master;
	uint32_t mtu;
	uint16_t gem;
	char name[IFNAMSIZ], bridge[IFNAMSIZ], alias[64];
	bool configured;
};

int fttr_net_open(struct fttr_net *net, const char *parent);
int fttr_net_reset(struct fttr_net *net, unsigned int onu);
int fttr_net_create(struct fttr_net *net, struct fttr_net_port *port,
		    unsigned int onu, const uint8_t serial[8], uint16_t gem, const char *bridge);
int fttr_net_verify(struct fttr_net *net, struct fttr_net_port *port);
int fttr_net_remove(struct fttr_net *net, struct fttr_net_port *port);

#endif
