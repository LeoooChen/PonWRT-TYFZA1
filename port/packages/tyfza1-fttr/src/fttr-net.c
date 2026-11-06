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
static const uint32_t fttr_mcast_member_group = 0x46544d55;

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
	bool have_learning, learning;
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
		if (a->rta_type == IFLA_INFO_SLAVE_DATA) {
			struct rtattr *port;
			int remaining = RTA_PAYLOAD(a);

			for (port = RTA_DATA(a); RTA_OK(port, remaining); port = RTA_NEXT(port, remaining))
				if (port->rta_type == IFLA_BRPORT_LEARNING && RTA_PAYLOAD(port) == 1) {
					link->have_learning = true;
					link->learning = *(uint8_t *)RTA_DATA(port);
				}
		}
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

static int gem_ordinal(unsigned int onu, uint16_t gem)
{
	/* Original unicast GEM: 256 + hardware ONU + (ordinal - 1) * 64. */
	if (onu >= 64 || gem < 256 + onu || gem > 4090 || (gem - 256 - onu) % 64)
		return -ERANGE;
	return (gem - 256 - onu) / 64 + 1;
}

static void port_name(char name[IFNAMSIZ], unsigned int onu, unsigned int ordinal)
{
	if (ordinal == 1)
		snprintf(name, IFNAMSIZ, "fttr-u%u", onu);
	else
		snprintf(name, IFNAMSIZ, "fttr-u%u-%u", onu, ordinal);
}

int fttr_net_reset(struct fttr_net *net, unsigned int onu)
{
	char name[IFNAMSIZ], base[IFNAMSIZ], prefix[32];
	struct if_nameindex *interfaces, *entry;
	struct link link;
	unsigned int ordinal;
	int ret = 0;

	if (onu >= 64)
		return -ERANGE;
	interfaces = if_nameindex();
	if (!interfaces)
		return -errno;
	port_name(base, onu, 1);
	snprintf(prefix, sizeof(prefix), "tyfza1-fttr:%u:", onu);
	for (entry = interfaces; entry->if_index && !ret; entry++) {
		if (strncmp(entry->if_name, base, strlen(base))) continue;
		for (ordinal = 1; ordinal <= 60; ordinal++) {
			port_name(name, onu, ordinal);
			if (!strcmp(entry->if_name, name)) break;
		}
		if (ordinal > 60) continue;
		ret = get_link(net, name, 0, &link);
		if (ret == -ENODEV) { ret = 0; continue; }
		if (ret) break;
		/* Older controllers used the unsuffixed name for any one slot.
		 * New suffixed names must match the precise GEM ordinal. */
		if (link.parent != net->parent || strcmp(link.kind, "vlan") ||
		    (link.group != fttr_group && link.group != fttr_mcast_member_group) ||
		    link.protocol != htons(0x8100) || gem_ordinal(onu, link.vid) < 0 ||
		    (ordinal > 1 && gem_ordinal(onu, link.vid) != (int)ordinal) ||
		    (*link.alias && strncmp(link.alias, prefix, strlen(prefix))))
			ret = -EEXIST;
		else
			ret = delete_link(net, link.index);
	}
	if_freenameindex(interfaces);
	return ret;
}

static bool owned(const struct fttr_net *net, const struct fttr_net_port *port, const struct link *link)
{
	return link->parent == net->parent && !strcmp(link->kind, "vlan") &&
		link->group == (port->multicast ? fttr_mcast_member_group : fttr_group) &&
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
	int ret, ordinal = gem_ordinal(onu, gem);

	if (ordinal < 1 || ordinal > 60)
		return -ERANGE;
	port->gem = gem;
	port_name(port->name, onu, ordinal);
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
	attribute(&r, IFLA_GROUP, port->multicast ? &fttr_mcast_member_group : &fttr_group, sizeof(fttr_group));
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

static bool mcast_owned(const struct fttr_net *net, const struct link *link)
{
	return link->parent == net->parent && link->group == 0x46544d43 &&
		!strcmp(link->name, "fttr-mc") &&
		(!*link->alias || !strcmp(link->alias, "tyfza1-fttr:multicast"));
}

static int mcast_close(struct fttr_net *net, const struct link *link)
{
	struct request r = request(RTM_NEWLINK, NLM_F_ACK, link->index);
	struct link check;
	int master = 0, ret;

	if (!mcast_owned(net, link))
		return -ESTALE;
	/* Stop retires the upper qdisc, shared-NAPI reports and parent qdisc
	 * before detaching it from the bridge. The parent remains available.
	 */
	r.info.ifi_change = IFF_UP;
	ret = exchange(net, &r, NULL);
	if (ret)
		return ret;
	r = request(RTM_NEWLINK, NLM_F_ACK, link->index);
	attribute(&r, IFLA_MASTER, &master, sizeof(master));
	ret = exchange(net, &r, NULL);
	if (!ret)
		ret = get_link(net, NULL, link->index, &check);
	if (!ret && (!mcast_owned(net, &check) || check.master || (check.flags & IFF_UP)))
		ret = -EUCLEAN;
	return ret;
}

int fttr_net_mcast_reset(struct fttr_net *net)
{
	struct link link;
	int ret = get_link(net, "fttr-mc", 0, &link);

	return ret == -ENODEV ? 0 : ret ? ret : mcast_close(net, &link);
}

int fttr_net_mcast_remove(struct fttr_net *net, struct fttr_net_port *port)
{
	struct link link;
	int ret;

	port->configured = false;
	if (!port->index)
		return 0;
	ret = get_link(net, NULL, port->index, &link);
	if (!ret)
		ret = mcast_close(net, &link);
	if (ret == -ENODEV)
		ret = 0;
	if (!ret)
		port->index = 0;
	return ret;
}

int fttr_net_mcast_verify(struct fttr_net *net, struct fttr_net_port *port)
{
	struct link link, master;
	int ret = get_link(net, NULL, port->index, &link);

	if (!ret && (!mcast_owned(net, &link) || strcmp(link.alias, port->alias) ||
	    link.master != port->master || link.mtu != port->mtu || !(link.flags & IFF_UP) ||
	    !link.have_learning || link.learning))
		ret = -ESTALE;
	if (!ret)
		ret = get_link(net, NULL, port->master, &master);
	if (!ret && (strcmp(master.kind, "bridge") || strcmp(master.name, port->bridge) || !(master.flags & IFF_UP)))
		ret = -ENETDOWN;
	port->configured = !ret;
	return ret;
}

int fttr_net_mcast_create(struct fttr_net *net, struct fttr_net_port *port, const char *bridge)
{
	struct link link, parent, master;
	struct request r;
	struct rtattr *settings;
	uint8_t learning = 0;
	int ret = get_link(net, "fttr-mc", 0, &link);

	if (ret)
		return ret;
	if (!mcast_owned(net, &link) || link.master || (link.flags & IFF_UP))
		return -EEXIST;
	ret = get_link(net, bridge, 0, &master);
	if (!ret)
		ret = get_link(net, NULL, net->parent, &parent);
	if (ret)
		return ret;
	if (strcmp(master.kind, "bridge") || !(master.flags & IFF_UP) || !(parent.flags & IFF_UP))
		return -ENETDOWN;
	if (master.mtu + 4 > parent.mtu)
		return -EMSGSIZE;
	*port = (struct fttr_net_port) { .index = link.index, .master = master.index, .mtu = master.mtu, .gem = 4095 };
	strcpy(port->name, "fttr-mc");
	strcpy(port->alias, "tyfza1-fttr:multicast");
	strcpy(port->bridge, bridge);
	r = request(RTM_NEWLINK, NLM_F_ACK, link.index);
	attribute(&r, IFLA_MTU, &port->mtu, sizeof(port->mtu));
	attribute(&r, IFLA_IFALIAS, port->alias, strlen(port->alias) + 1);
	attribute(&r, IFLA_MASTER, &port->master, sizeof(port->master));
	ret = exchange(net, &r, NULL);
	if (ret)
		return ret;
	/* Reports keep their ONU source MAC. Learning that MAC on the shared
	 * multicast port would divert later unicast away from its service GEM.
	 */
	r = request(RTM_SETLINK, NLM_F_ACK, link.index);
	r.info.ifi_family = AF_BRIDGE;
	settings = attribute(&r, IFLA_PROTINFO | NLA_F_NESTED, NULL, 0);
	attribute(&r, IFLA_BRPORT_LEARNING, &learning, sizeof(learning));
	end_nested(&r, settings);
	ret = exchange(net, &r, NULL);
	if (ret)
		return ret;
	r = request(RTM_NEWLINK, NLM_F_ACK, link.index);
	r.info.ifi_flags = r.info.ifi_change = IFF_UP;
	ret = exchange(net, &r, NULL);
	return ret ? ret : fttr_net_mcast_verify(net, port);
}
