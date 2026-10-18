// SPDX-License-Identifier: GPL-2.0-only
#define _DEFAULT_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <linux/if_link.h>
#include <linux/if_vlan.h>
#include <linux/rtnetlink.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "fttr-net.h"

/* rtnl_create_link applies GROUP but does not apply IFALIAS. This marks even
 * a down port whose subsequent alias/bridge setup was interrupted. */
static const uint32_t fttr_group = 0x46545452;

struct request {
	struct nlmsghdr header;
	struct ifinfomsg info;
	uint8_t attributes[512];
};

struct link {
	int index, parent, master;
	unsigned int flags;
	uint32_t mtu, group;
	uint16_t vid, protocol;
	char name[IFNAMSIZ], alias[64], kind[16];
};

/* Requests contain a fixed set of attributes; their total is below 512 bytes. */
static struct rtattr *attribute(struct request *r, int type, const void *data, size_t size)
{
	struct rtattr *a = (void *)((uint8_t *)r + NLMSG_ALIGN(r->header.nlmsg_len));

	a->rta_type = type;
	a->rta_len = RTA_LENGTH(size);
	if (size)
		memcpy(RTA_DATA(a), data, size);
	r->header.nlmsg_len = NLMSG_ALIGN(r->header.nlmsg_len) + RTA_ALIGN(a->rta_len);
	return a;
}

static void end_nested(struct request *r, struct rtattr *a)
{
	a->rta_len = (uint8_t *)r + r->header.nlmsg_len - (uint8_t *)a;
}

static void parse_vlan(struct rtattr *data, struct link *link)
{
	int size = RTA_PAYLOAD(data);
	struct rtattr *a;

	for (a = RTA_DATA(data); RTA_OK(a, size); a = RTA_NEXT(a, size)) {
		if (a->rta_type == IFLA_VLAN_ID)
			memcpy(&link->vid, RTA_DATA(a), sizeof(link->vid));
		if (a->rta_type == IFLA_VLAN_PROTOCOL)
			memcpy(&link->protocol, RTA_DATA(a), sizeof(link->protocol));
	}
}

static void parse_info(struct rtattr *info, struct link *link)
{
	int size = RTA_PAYLOAD(info);
	struct rtattr *a, *data = NULL;

	for (a = RTA_DATA(info); RTA_OK(a, size); a = RTA_NEXT(a, size)) {
		if (a->rta_type == IFLA_INFO_KIND)
			snprintf(link->kind, sizeof(link->kind), "%s", (char *)RTA_DATA(a));
		if (a->rta_type == IFLA_INFO_DATA)
			data = a;
	}
	if (data && !strcmp(link->kind, "vlan"))
		parse_vlan(data, link);
}

static void parse_link(struct nlmsghdr *header, struct link *link)
{
	struct ifinfomsg *info = NLMSG_DATA(header);
	int size = IFLA_PAYLOAD(header);
	struct rtattr *a;

	*link = (struct link) { .index = info->ifi_index, .flags = info->ifi_flags };
	for (a = IFLA_RTA(info); RTA_OK(a, size); a = RTA_NEXT(a, size)) {
		switch (a->rta_type) {
		case IFLA_IFNAME: snprintf(link->name, sizeof(link->name), "%s", (char *)RTA_DATA(a)); break;
		case IFLA_IFALIAS: snprintf(link->alias, sizeof(link->alias), "%s", (char *)RTA_DATA(a)); break;
		case IFLA_LINK: memcpy(&link->parent, RTA_DATA(a), sizeof(link->parent)); break;
		case IFLA_MASTER: memcpy(&link->master, RTA_DATA(a), sizeof(link->master)); break;
		case IFLA_MTU: memcpy(&link->mtu, RTA_DATA(a), sizeof(link->mtu)); break;
		case IFLA_GROUP: memcpy(&link->group, RTA_DATA(a), sizeof(link->group)); break;
		case IFLA_LINKINFO: parse_info(a, link); break;
		}
	}
}

static int exchange(struct fttr_net *net, struct request *r, struct link *link)
{
	struct sockaddr_nl kernel = { .nl_family = AF_NETLINK };
	uint8_t buffer[16384];
	struct nlmsghdr *h;
	ssize_t size;

	r->header.nlmsg_seq = ++net->sequence;
	if (sendto(net->fd, r, r->header.nlmsg_len, 0,
		   (struct sockaddr *)&kernel, sizeof(kernel)) < 0)
		return -errno;
	for (;;) {
		size = recv(net->fd, buffer, sizeof(buffer), 0);
		if (size < 0) {
			if (errno == EINTR)
				continue;
			return -errno;
		}
		for (h = (void *)buffer; NLMSG_OK(h, size); h = NLMSG_NEXT(h, size)) {
			if (h->nlmsg_seq != net->sequence)
				continue;
			if (h->nlmsg_type == NLMSG_ERROR)
				return ((struct nlmsgerr *)NLMSG_DATA(h))->error;
			if (h->nlmsg_type == RTM_NEWLINK && link) {
				parse_link(h, link);
				return 0;
			}
		}
	}
}

static struct request request(int type, int flags, int index)
{
	return (struct request) {
		.header = { .nlmsg_len = NLMSG_LENGTH(sizeof(struct ifinfomsg)),
			.nlmsg_type = type, .nlmsg_flags = NLM_F_REQUEST | flags },
		.info = { .ifi_family = AF_UNSPEC, .ifi_index = index }
	};
}

static int get_link(struct fttr_net *net, const char *name, int index, struct link *link)
{
	struct request r = request(RTM_GETLINK, 0, index);

	if (name)
		attribute(&r, IFLA_IFNAME, name, strlen(name) + 1);
	return exchange(net, &r, link);
}

static int delete_link(struct fttr_net *net, int index)
{
	struct request r = request(RTM_DELLINK, NLM_F_ACK, index);

	return exchange(net, &r, NULL);
}

int fttr_net_open(struct fttr_net *net, const char *parent)
{
	struct sockaddr_nl address = { .nl_family = AF_NETLINK };
	struct link link;
	int ret;

	net->fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_ROUTE);
	if (net->fd < 0)
		return -errno;
	if (bind(net->fd, (struct sockaddr *)&address, sizeof(address))) {
		ret = -errno;
		goto failed;
	}
	ret = get_link(net, parent, 0, &link);
	if (ret)
		goto failed;
	net->parent = link.index;
	return 0;
failed:
	close(net->fd);
	net->fd = -1;
	return ret;
}

static void port_name(char name[IFNAMSIZ], unsigned int onu)
{
	snprintf(name, IFNAMSIZ, "fttr-u%u", onu);
}

int fttr_net_reset(struct fttr_net *net, unsigned int onu)
{
	char name[IFNAMSIZ], prefix[32];
	struct link link;
	int ret;

	port_name(name, onu);
	ret = get_link(net, name, 0, &link);
	if (ret == -ENODEV)
		return 0;
	if (ret)
		return ret;
	snprintf(prefix, sizeof(prefix), "tyfza1-fttr:%u:", onu);
	if (link.parent != net->parent || strcmp(link.kind, "vlan") ||
	    link.group != fttr_group || (*link.alias && strncmp(link.alias, prefix, strlen(prefix))))
		return -EEXIST;
	return delete_link(net, link.index);
}

static bool owned(const struct fttr_net *net, const struct fttr_net_port *port, const struct link *link)
{
	return link->parent == net->parent && !strcmp(link->kind, "vlan") &&
		link->group == fttr_group &&
		link->vid == port->gem && link->protocol == htons(0x8100) &&
		!strcmp(link->name, port->name) && (!*link->alias || !strcmp(link->alias, port->alias));
}

int fttr_net_verify(struct fttr_net *net, struct fttr_net_port *port)
{
	struct link link, master;
	int ret = get_link(net, NULL, port->index, &link);

	if (!ret && (!owned(net, port, &link) || strcmp(link.alias, port->alias) || link.master != port->master ||
	    link.mtu != port->mtu || !(link.flags & IFF_UP)))
		ret = -ESTALE;
	if (!ret)
		ret = get_link(net, NULL, port->master, &master);
	if (!ret && (strcmp(master.name, port->bridge) || !(master.flags & IFF_UP)))
		ret = -ENETDOWN;
	port->configured = !ret;
	return ret;
}

int fttr_net_remove(struct fttr_net *net, struct fttr_net_port *port)
{
	struct link link;
	int ret;

	port->configured = false;
	if (!port->index)
		return 0;
	ret = get_link(net, NULL, port->index, &link);
	if (!ret)
		ret = owned(net, port, &link) ? delete_link(net, port->index) : -ESTALE;
	if (ret == -ENODEV)
		ret = 0;
	if (!ret)
		port->index = 0;
	return ret;
}

int fttr_net_create(struct fttr_net *net, struct fttr_net_port *port,
		    unsigned int onu, const uint8_t serial[8], uint16_t gem, const char *bridge)
{
	struct link parent, master, child;
	struct rtattr *info, *data;
	struct request r;
	uint16_t protocol = htons(0x8100);
	int ret;

	port->gem = gem;
	port_name(port->name, onu);
	strcpy(port->bridge, bridge);
	snprintf(port->alias, sizeof(port->alias), "tyfza1-fttr:%u:%02x%02x%02x%02x%02x%02x%02x%02x:%u",
		onu, serial[0], serial[1], serial[2], serial[3], serial[4], serial[5], serial[6], serial[7], gem);
	ret = get_link(net, bridge, 0, &master);
	if (ret)
		return ret;
	if (strcmp(master.kind, "bridge") || !(master.flags & IFF_UP))
		return -EINVAL;
	ret = get_link(net, NULL, net->parent, &parent);
	if (ret)
		return ret;
	if (master.mtu + 4 > parent.mtu)
		return -EMSGSIZE;
	port->master = master.index;
	port->mtu = master.mtu;
	r = request(RTM_NEWLINK, NLM_F_ACK | NLM_F_CREATE | NLM_F_EXCL, 0);
	attribute(&r, IFLA_IFNAME, port->name, strlen(port->name) + 1);
	attribute(&r, IFLA_LINK, &net->parent, sizeof(net->parent));
	attribute(&r, IFLA_MTU, &port->mtu, sizeof(port->mtu));
	attribute(&r, IFLA_GROUP, &fttr_group, sizeof(fttr_group));
	info = attribute(&r, IFLA_LINKINFO, NULL, 0);
	attribute(&r, IFLA_INFO_KIND, "vlan", sizeof("vlan"));
	data = attribute(&r, IFLA_INFO_DATA, NULL, 0);
	attribute(&r, IFLA_VLAN_ID, &gem, sizeof(gem));
	attribute(&r, IFLA_VLAN_PROTOCOL, &protocol, sizeof(protocol));
	end_nested(&r, data);
	end_nested(&r, info);
	ret = exchange(net, &r, NULL);
	if (ret)
		return ret;
	ret = get_link(net, port->name, 0, &child);
	if (ret)
		return ret;
	port->index = child.index;
	r = request(RTM_NEWLINK, NLM_F_ACK, port->index);
	r.info.ifi_flags = IFF_UP;
	r.info.ifi_change = IFF_UP;
	attribute(&r, IFLA_IFALIAS, port->alias, strlen(port->alias));
	attribute(&r, IFLA_MASTER, &port->master, sizeof(port->master));
	ret = exchange(net, &r, NULL);
	return ret ? ret : fttr_net_verify(net, port);
}
