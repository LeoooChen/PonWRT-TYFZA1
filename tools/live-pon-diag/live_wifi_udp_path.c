// SPDX-License-Identifier: GPL-2.0-only
/* Finite local UDP measurements use real sockets at both Mac interfaces.
 * Each direction targets a non-local IP and DNATs to the receiving interface,
 * so the Mac cannot deliver the measurement through its loopback route.
 */
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/inet.h>
#include <linux/rtnetlink.h>
#include <linux/unaligned.h>
#include <net/flow_offload.h>
#include <net/net_namespace.h>

extern int rtl9617c_ni_fe_setup_tc(struct device *, struct net_device *, enum tc_setup_type, void *);

static char *lan = "eth1", *ap = "ap5";
static char *lan_ip = "192.168.1.172", *wifi_ip = "192.168.1.212";
static char *lan_mac = "02:00:00:00:00:01", *wifi_mac = "02:00:00:00:00:02";
module_param(lan, charp, 0400);
module_param(ap, charp, 0400);
module_param(lan_ip, charp, 0400);
module_param(wifi_ip, charp, 0400);
module_param(lan_mac, charp, 0400);
module_param(wifi_mac, charp, 0400);

struct udp_keys {
	struct flow_dissector_key_control control;
	struct flow_dissector_key_meta meta;
	struct flow_dissector_key_basic basic;
	struct flow_dissector_key_ipv4_addrs ipv4;
	struct flow_dissector_key_ports ports;
};
struct udp_path {
	struct net_device *input, *output;
	struct flow_block block;
	struct flow_block_cb *callback;
	struct flow_rule *rule;
	struct udp_keys key, mask;
	u64 packets, bytes;
};
static struct udp_path paths[2];
static struct net_device *wired, *wireless, *bridge;
static struct flow_dissector dissector;
static const struct flow_dissector_key keys[] = {
	{FLOW_DISSECTOR_KEY_CONTROL, offsetof(struct udp_keys, control)},
	{FLOW_DISSECTOR_KEY_META, offsetof(struct udp_keys, meta)},
	{FLOW_DISSECTOR_KEY_BASIC, offsetof(struct udp_keys, basic)},
	{FLOW_DISSECTOR_KEY_IPV4_ADDRS, offsetof(struct udp_keys, ipv4)},
	{FLOW_DISSECTOR_KEY_PORTS, offsetof(struct udp_keys, ports)},
};

static int path_command(struct udp_path *path, enum flow_cls_command command)
{
	struct flow_cls_offload cls = {.command = command, .cookie = (unsigned long)path,
		.rule = path->rule, .common = {.protocol = cpu_to_be16(ETH_P_IP), .skip_sw = true}};
	int ret = path->callback->cb(TC_SETUP_CLSFLOWER, &cls, path->callback->cb_priv);

	if (command == FLOW_CLS_STATS) {
		path->packets += cls.stats.pkts;
		path->bytes += cls.stats.bytes;
	}
	return ret;
}

static int stats_get(char *buffer, const struct kernel_param *parameter)
{
	unsigned int i, length = 0;

	(void)parameter;
	rtnl_lock();
	for (i = 0; i < 2; i++) {
		int ret = path_command(&paths[i], FLOW_CLS_STATS);
		length += scnprintf(buffer + length, PAGE_SIZE - length,
			"direction=%u error=%d packets=%llu bytes=%llu\n", i, ret, paths[i].packets, paths[i].bytes);
	}
	rtnl_unlock();
	return length;
}
static const struct kernel_param_ops stats_ops = {.get = stats_get};
module_param_cb(stats, &stats_ops, NULL, 0400);

static void path_unbind(struct udp_path *path)
{
	struct flow_block_offload off = {.command = FLOW_BLOCK_UNBIND,
		.binder_type = FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,
		.net = &init_net, .block = &path->block};

	INIT_LIST_HEAD(&off.cb_list);
	rtl9617c_ni_fe_setup_tc(wired->dev.parent, path->input, TC_SETUP_FT, &off);
	list_del(&path->callback->list);
	flow_block_cb_free(path->callback);
}

static int path_install(unsigned int direction, __be32 source, __be32 destination,
	const u8 peer[6])
{
	struct udp_path *path = &paths[direction];
	struct flow_block_offload off = {.command = FLOW_BLOCK_BIND,
		.binder_type = FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS,
		.net = &init_net, .block = &path->block};
	u8 ethernet[12];
	unsigned int i;
	int ret;

	path->input = direction ? wireless : wired;
	path->output = direction ? wired : wireless;
	INIT_LIST_HEAD(&path->block.cb_list);
	INIT_LIST_HEAD(&off.cb_list);
	ret = rtl9617c_ni_fe_setup_tc(wired->dev.parent, path->input, TC_SETUP_FT, &off);
	if (ret) return ret;
	path->callback = list_first_entry(&off.cb_list, struct flow_block_cb, list);
	list_splice_tail_init(&off.cb_list, &path->block.cb_list);
	path->rule = flow_rule_alloc(5);
	if (!path->rule) { ret = -ENOMEM; goto unbind; }
	path->rule->match.dissector = &dissector;
	path->rule->match.key = &path->key;
	path->rule->match.mask = &path->mask;
	path->key.meta.ingress_ifindex = path->input->ifindex;
	path->key.basic.n_proto = htons(ETH_P_IP);
	path->key.basic.ip_proto = IPPROTO_UDP;
	path->mask.basic.n_proto = htons(65535);
	path->mask.basic.ip_proto = 255;
	path->key.ipv4.src = source;
	path->key.ipv4.dst = htonl(direction ? 0xc6130002 : 0xc6120002);
	path->mask.ipv4.src = path->mask.ipv4.dst = htonl(~0U);
	path->key.ports.src = htons(55000 + direction * 2);
	path->key.ports.dst = htons(55001 + direction * 2);
	path->mask.ports.src = path->mask.ports.dst = htons(65535);
	memcpy(ethernet, peer, 6);
	memcpy(ethernet + 6, bridge->dev_addr, 6);
	for (i = 0; i < 3; i++)
		path->rule->action.entries[i] = (struct flow_action_entry){.id = FLOW_ACTION_MANGLE,
			.mangle = {.htype = FLOW_ACT_MANGLE_HDR_TYPE_ETH, .offset = i * 4,
				.mask = 0, .val = get_unaligned((u32 *)(ethernet + i * 4))}};
	path->rule->action.entries[3] = (struct flow_action_entry){.id = FLOW_ACTION_MANGLE,
		.mangle = {.htype = FLOW_ACT_MANGLE_HDR_TYPE_IP4, .offset = 16, .mask = 0, .val = destination}};
	path->rule->action.entries[4] = (struct flow_action_entry){.id = FLOW_ACTION_REDIRECT, .dev = path->output};
	ret = path_command(path, FLOW_CLS_REPLACE);
	if (!ret) return 0;
	kfree(path->rule);
unbind:
	path_unbind(path);
	return ret;
}

static int __init udp_path_init(void)
{
	__be32 address[2];
	u8 peer[2][6];
	int ret;

	if (!in4_pton(lan_ip, -1, (u8 *)&address[0], -1, NULL) ||
	    !in4_pton(wifi_ip, -1, (u8 *)&address[1], -1, NULL) ||
	    !mac_pton(lan_mac, peer[0]) || !mac_pton(wifi_mac, peer[1])) return -EINVAL;
	wired = dev_get_by_name(&init_net, lan);
	wireless = dev_get_by_name(&init_net, ap);
	bridge = dev_get_by_name(&init_net, "br-lan");
	if (!wired || !wireless || !bridge) { ret = -ENODEV; goto put; }
	skb_flow_dissector_init(&dissector, keys, ARRAY_SIZE(keys));
	rtnl_lock();
	ret = path_install(0, address[0], address[1], peer[1]);
	if (!ret) {
		ret = path_install(1, address[1], address[0], peer[0]);
		if (ret) {
			path_command(&paths[0], FLOW_CLS_DESTROY);
			path_unbind(&paths[0]);
			kfree(paths[0].rule);
		}
	}
	rtnl_unlock();
	if (!ret) {
		pr_info("TYF_UDP real socket paths installed: %s <-> %s\n", lan, ap);
		return 0;
	}
put:
	dev_put(bridge); dev_put(wireless); dev_put(wired);
	return ret;
}

static void __exit udp_path_exit(void)
{
	unsigned int i;

	rtnl_lock();
	for (i = 0; i < 2; i++) {
		path_command(&paths[i], FLOW_CLS_DESTROY);
		path_unbind(&paths[i]);
		kfree(paths[i].rule);
	}
	rtnl_unlock();
	dev_put(bridge); dev_put(wireless); dev_put(wired);
}
module_init(udp_path_init);
module_exit(udp_path_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TYFZA1 two physical interfaces and real UDP socket probe");
