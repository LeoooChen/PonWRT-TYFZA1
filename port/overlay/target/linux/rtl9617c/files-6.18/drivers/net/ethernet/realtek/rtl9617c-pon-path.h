/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef RTL9617C_PON_PATH_H
#define RTL9617C_PON_PATH_H

#define RTL_ITU_PATH_MAX 248
#define RTL_ITU_PATH_WIRE_BYTES 13
#define RTL_ITU_VLAN_TAGGED 65533U
#define RTL_ITU_VLAN_UNTAGGED 65534U
#define RTL_ITU_VLAN_ANY 65535U

struct rtl_itu_path {
	unsigned short alloc_id, gem_id, vlan, tcont;
	unsigned char pbits, multicast, queue, encrypted, index, downstream_pbits;
};

struct rtl_itu_paths {
	unsigned int count;
	struct rtl_itu_path path[RTL_ITU_PATH_MAX];
};

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

static inline const struct rtl_itu_path *rtl_itu_path_select(const struct rtl_itu_paths *paths,
		unsigned int vlan, unsigned int priority)
{
	const struct rtl_itu_path *selected = 0;
	unsigned int i, best = 0;

	for (i = 0; i < paths->count; i++) {
		const struct rtl_itu_path *path = &paths->path[i];
		unsigned int rank = (path->vlan < RTL_ITU_VLAN_TAGGED ? 4 :
			path->vlan == RTL_ITU_VLAN_ANY ? 0 : 2);

		if (path->multicast || !(path->pbits & (1U << priority)) || !rtl_itu_path_matches(path, vlan))
			continue;
		if (!selected || rank > best) {
			selected = path;
			best = rank;
		}
	}
	return selected;
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
