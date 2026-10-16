/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_PATH_H
#define RTL9617C_PON_PATH_H
#include "rtl9617c-pon-vlan.h"

#define RTL_ITU_PATH_MAX 248
#define RTL_ITU_PATH_WIRE_BYTES 50
#define RTL_ITU_SCHEDULE_WIRE_BYTES 12
#define RTL_ITU_CONFIG_WIRE_MAX (12 + RTL_ITU_PATH_MAX * RTL_ITU_PATH_WIRE_BYTES + \
	RTL_ITU_PATH_MAX * RTL_EVTO_STAGES * (32 + RTL_EVTO_ROWS * 16) + \
	31 * RTL_ITU_SCHEDULE_WIRE_BYTES)
#define RTL_ITU_VLAN_TAGGED 65533U
#define RTL_ITU_VLAN_UNTAGGED 65534U
#define RTL_ITU_VLAN_ANY 65535U

struct rtl_itu_path {
	unsigned short alloc_id, gem_id, vlan, tcont;
	unsigned char pbits, multicast, queue, encrypted, index, downstream_pbits;
	unsigned short vlan_profile[RTL_EVTO_STAGES];
	unsigned char vlan_exclude, unmarked_mode, default_priority, mapper_dscp[24];
};

struct rtl_itu_paths {
	unsigned int count, profile_count;
	unsigned long long generation;
	struct rtl_itu_path path[RTL_ITU_PATH_MAX];
	unsigned char weights[32][8];
	struct rtl_evto_profile profile[];
};

static inline unsigned long rtl_itu_paths_bytes(const struct rtl_itu_paths *paths)
{
	return sizeof(*paths) + paths->profile_count * sizeof(paths->profile[0]);
}

static inline int rtl_itu_path_vlan(const struct rtl_itu_paths *paths, const struct rtl_itu_path *path,
	struct rtl_evto_packet *packet, unsigned int begin, unsigned int end, int downstream)
{
	for (unsigned int n = begin; n < end; n++) {
		unsigned int i = downstream ? end - 1 - (n - begin) : n;
		unsigned int id = path->vlan_profile[i];
		if (id != RTL_EVTO_NONE && !rtl_evto_apply(&paths->profile[id], packet, downstream)) return 0;
	}
	return 1;
}

static inline int rtl_itu_path_matches(const struct rtl_itu_path *path,
		unsigned int vlan)
{
	if (path->vlan == RTL_ITU_VLAN_UNTAGGED)
		return vlan == RTL_ITU_VLAN_ANY;
	if (path->vlan == RTL_ITU_VLAN_TAGGED && vlan == RTL_ITU_VLAN_ANY)
		return 0;
	if (path->vlan < RTL_ITU_VLAN_TAGGED && path->vlan != vlan)
		return 0;
	return 1;
}

static inline const struct rtl_itu_path *rtl_itu_path_transmit_vlan(const struct rtl_itu_paths *paths,
	const struct rtl_evto_packet *input, struct rtl_evto_packet *output)
{
	const struct rtl_itu_path *selected = 0;
	unsigned int best = 0;
	int conflict = 0;
	for (unsigned int i = 0; i < paths->count; i++) {
		const struct rtl_itu_path *path = &paths->path[i];
		struct rtl_evto_packet packet = *input;
		unsigned int rank = path->vlan < RTL_ITU_VLAN_TAGGED ? 4 : path->vlan == RTL_ITU_VLAN_ANY ? 0 : 2;
		if (path->multicast || !rtl_itu_path_vlan(paths, path, &packet, 0, 2, 0) ||
		    (path->vlan_exclude & (1U << packet.priority)) ||
		    !rtl_itu_path_matches(path, packet.count ? packet.tag[0].tci & 4095 : RTL_ITU_VLAN_ANY)) continue;
		if (!rtl_itu_path_vlan(paths, path, &packet, 2, 3, 0)) continue;
		if (!packet.count && path->unmarked_mode)
			packet.priority = path->unmarked_mode == 1 ? rtl_itu_dscp_priority(path->mapper_dscp, packet.dscp) : path->default_priority;
		if (!(path->pbits & (1U << packet.priority))) continue;
		if (!selected || rank > best) { selected = path; *output = packet; best = rank; conflict = 0; }
		else if (rank == best && selected->index != path->index) conflict = 1;
	}
	return selected && !conflict && rtl_itu_path_vlan(paths, selected, output, 3, RTL_EVTO_STAGES, 0) ? selected : 0;
}

static inline const struct rtl_itu_path *rtl_itu_path_receive_vlan(const struct rtl_itu_paths *paths,
	unsigned int index, const struct rtl_evto_packet *input, struct rtl_evto_packet *output)
{
	const struct rtl_itu_path *selected = 0;
	for (unsigned int i = 0; i < paths->count; i++) {
		const struct rtl_itu_path *path = &paths->path[i];
		struct rtl_evto_packet packet = *input;
		if ((index != RTL_EVTO_NONE && path->index != index) ||
		    !rtl_itu_path_vlan(paths, path, &packet, 2, RTL_EVTO_STAGES, 1) ||
		    !(path->downstream_pbits & (1U << packet.priority)) ||
		    !rtl_itu_path_matches(path, packet.count ? packet.tag[0].tci & 4095 : RTL_ITU_VLAN_ANY) ||
		    !rtl_itu_path_vlan(paths, path, &packet, 0, 2, 1)) continue;
		if (selected && (selected->index != path->index || packet.count != output->count)) return 0;
		if (selected) for (unsigned int n = 0; n < packet.count; n++)
			if (packet.tag[n].tci != output->tag[n].tci || packet.tag[n].tpid != output->tag[n].tpid) return 0;
		selected = path; *output = packet;
	}
	return selected;
}

static inline const struct rtl_itu_path *rtl_itu_path_select(const struct rtl_itu_paths *paths,
		unsigned int vlan, unsigned int priority)
{
	const struct rtl_itu_path *selected = 0;
	unsigned int i, best = 0;
	int conflict = 0;

	for (i = 0; i < paths->count; i++) {
		const struct rtl_itu_path *path = &paths->path[i];
		unsigned int rank = (path->vlan < RTL_ITU_VLAN_TAGGED ? 4 :
			path->vlan == RTL_ITU_VLAN_ANY ? 0 : 2);

		if (path->multicast || !(path->pbits & (1U << priority)) ||
		    (path->vlan_exclude & (1U << priority)) || !rtl_itu_path_matches(path, vlan))
			continue;
		if (!selected || rank > best) {
			selected = path;
			best = rank;
			conflict = 0;
		} else if (rank == best && selected->index != path->index) conflict = 1;
	}
	return conflict ? 0 : selected;
}

/* A Linux flowtable rule matches VID but carries no Ethernet PCP match. */
static inline const struct rtl_itu_path *rtl_itu_path_flow(const struct rtl_itu_paths *paths,
		unsigned int vlan)
{
	const struct rtl_itu_path *first = rtl_itu_path_select(paths, vlan, 0), *path;
	unsigned int priority;

	if (!first)
		return 0;
	for (priority = 1; priority < 8; priority++) {
		path = rtl_itu_path_select(paths, vlan, priority);
		if (!path || path->index != first->index || path->queue != first->queue)
			return 0;
	}
	return first;
}

/* Without receive GEM metadata, an offloaded ingress must resolve uniquely. */
static inline const struct rtl_itu_path *rtl_itu_path_downstream(const struct rtl_itu_paths *paths,
		unsigned int vlan, unsigned int pbits)
{
	const struct rtl_itu_path *first = 0;
	unsigned int i, covered = 0;

	for (i = 0; i < paths->count; i++) {
		const struct rtl_itu_path *path = &paths->path[i];

		if (path->multicast || !(path->downstream_pbits & pbits) || !rtl_itu_path_matches(path, vlan))
			continue;
		if (first && first->index != path->index)
			return 0;
		first = path;
		covered |= path->downstream_pbits;
	}
	return (covered & pbits) == pbits ? first : 0;
}
#endif
