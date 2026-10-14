/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Packet expectations follow G.988 9.3.13 tables and the TYFZA1 compiler trace. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rtl9617c-pon-path.h"

static void word(unsigned char *p, unsigned int value)
{
	p[0] = value >> 24; p[1] = value >> 16; p[2] = value >> 8; p[3] = value;
}
static unsigned int filter(unsigned int pri, unsigned int vid, unsigned int tpid)
{
	return pri << 28 | vid << 15 | tpid << 12;
}
static unsigned int treatment(unsigned int pri, unsigned int vid, unsigned int tpid)
{
	return pri << 16 | vid << 3 | tpid;
}
static struct rtl_evto_profile profile(unsigned int outer, unsigned int inner,
	unsigned int remove, unsigned int new_outer, unsigned int new_inner)
{
	struct rtl_evto_profile p = {.input_tpid = 0x8100, .output_tpid = 0x88a8, .count = 1};
	word(p.row[0], outer); word(p.row[0] + 4, inner);
	word(p.row[0] + 8, remove << 30 | new_outer); word(p.row[0] + 12, new_inner);
	assert(rtl_evto_valid(&p));
	return p;
}
static void tag(const struct rtl_evto_packet *p, unsigned int n, unsigned int tpid, unsigned int vid, unsigned int pri)
{
	assert(p->tag[n].tpid == tpid && (p->tag[n].tci & 4095) == vid && p->tag[n].tci >> 13 == pri);
}

int main(void)
{
	unsigned int absent = filter(15, 0, 0), any = filter(8, 4096, 0), none = treatment(15, 0, 0);
	struct rtl_evto_packet packet = {.protocol = 0x0800};
	struct rtl_evto_profile p = profile(absent, absent, 0, none, treatment(5, 100, 4));
	assert(rtl_evto_apply(&p, &packet, 0) && packet.count == 1); tag(&packet, 0, 0x8100, 100, 5);
	assert(rtl_evto_apply(&p, &packet, 1) && packet.count == 0);
	p = profile(absent, filter(8, 100, 4), 1, none, treatment(8, 200, 4));
	packet = (struct rtl_evto_packet){.count = 1, .protocol = 0x0800, .tag = {{0x8100, 3 << 13 | 100}}};
	assert(rtl_evto_apply(&p, &packet, 0)); tag(&packet, 0, 0x8100, 200, 3);
	assert(rtl_evto_apply(&p, &packet, 1)); tag(&packet, 0, 0x8100, 100, 3);
	p = profile(absent, filter(8, 100, 4), 1, none, none);
	assert(rtl_evto_apply(&p, &packet, 0) && packet.count == 0);
	assert(rtl_evto_apply(&p, &packet, 1)); tag(&packet, 0, 0x8100, 100, 0);
	p = profile(absent, any, 0, none, treatment(5, 200, 6));
	packet = (struct rtl_evto_packet){.count = 1, .protocol = 0x0800, .tag = {{0x8100, 3 << 13 | 100}}};
	assert(rtl_evto_apply(&p, &packet, 0) && packet.count == 2);
	tag(&packet, 0, 0x88a8, 200, 5); tag(&packet, 1, 0x8100, 100, 3);
	assert(rtl_evto_apply(&p, &packet, 1) && packet.count == 1); tag(&packet, 0, 0x8100, 100, 3);
	p = profile(filter(8, 200, 5), any, 1, none, none);
	packet = (struct rtl_evto_packet){.count = 2, .protocol = 0x0800,
		.tag = {{0x8100, 5 << 13 | 200}, {0x8100, 3 << 13 | 100}}};
	assert(rtl_evto_apply(&p, &packet, 0) && packet.count == 1);
	assert(rtl_evto_apply(&p, &packet, 1) && packet.count == 2);
	/* A removed unconstrained priority is not recoverable; inverse uses zero. */
	tag(&packet, 0, 0x8100, 200, 0); tag(&packet, 1, 0x8100, 100, 3);
	p = profile(filter(8, 200, 5), any, 1, none, treatment(9, 300, 6));
	packet.tag[0].tci = 5 << 13 | 200;
	assert(rtl_evto_apply(&p, &packet, 0)); tag(&packet, 0, 0x88a8, 300, 5);
	assert(rtl_evto_apply(&p, &packet, 1)); tag(&packet, 0, 0x8100, 200, 5);
	p = profile(absent, absent, 3, none, none);
	packet = (struct rtl_evto_packet){.protocol = 0x0800};
	assert(!rtl_evto_apply(&p, &packet, 0)); assert(rtl_evto_apply(&p, &packet, 1));

	for (unsigned int mode = 0; mode <= 8; mode++) {
		p = profile(absent, filter(8, 100, 4), 1, none, treatment(5, 200, 4));
		p.downstream = mode;
		packet = (struct rtl_evto_packet){.count = 1, .protocol = 0x0800, .tag = {{0x8100, 5 << 13 | 200}}};
		assert(rtl_evto_apply(&p, &packet, 1) == (mode != 8));
		if (mode == 8) continue;
		tag(&packet, 0, 0x8100, mode == 1 || mode == 4 || mode == 7 ? 200 : 100,
			mode == 1 || mode == 3 || mode == 6 ? 5 : 0);
		packet.tag[0].tci = 6 << 13 | 999;
		assert(rtl_evto_apply(&p, &packet, 1) == (mode < 5));
	}

	p = profile(absent, absent, 0, none, treatment(10, 20, 4));
	for (unsigned int d = 0; d < 64; d++) {
		unsigned int bit = d * 3;
		for (unsigned int b = 0; b < 3; b++)
			p.dscp[(bit + b) / 8] |= ((d % 8 >> (2 - b)) & 1) << (7 - (bit + b) % 8);
	}
	packet = (struct rtl_evto_packet){.protocol = 0x0800, .dscp = 46};
	assert(rtl_evto_apply(&p, &packet, 0)); tag(&packet, 0, 0x8100, 20, 6);
	word(p.row[0] + 4, absent | 5); /* EtherType code5 is EAPOL, not PPPoE. */
	packet = (struct rtl_evto_packet){.protocol = 0x8864}; assert(!rtl_evto_apply(&p, &packet, 0));
	packet.protocol = 0x888e; assert(rtl_evto_apply(&p, &packet, 0));

	/* Default rows sort before some specific keys, but execute as a last resort. */
	p = profile(filter(15, 4096, 0), filter(14, 4096, 0), 0, none, none);
	p.count = 2;
	word(p.row[1], absent); word(p.row[1] + 4, filter(8, 100, 4));
	word(p.row[1] + 8, 1U << 30 | none); word(p.row[1] + 12, treatment(8, 200, 4));
	packet = (struct rtl_evto_packet){.count = 1, .protocol = 0x0800, .tag = {{0x8100, 3 << 13 | 100}}};
	assert(rtl_evto_apply(&p, &packet, 0)); tag(&packet, 0, 0x8100, 200, 3);
	p = profile(absent, any, 1, treatment(8, 4096, 0), treatment(8, 4096, 0));
	p.downstream = 5;
	packet = (struct rtl_evto_packet){.count = 2, .protocol = 0x0800,
		.tag = {{0x8100, 3 << 13 | 100}, {0x8100, 3 << 13 | 200}}};
	assert(!rtl_evto_apply(&p, &packet, 1));
	packet.tag[1] = packet.tag[0];
	assert(rtl_evto_apply(&p, &packet, 1) && packet.count == 1); tag(&packet, 0, 0x8100, 100, 3);
	/* Downstream many-to-one reversal uses row-key order, including modified defaults. */
	p = profile(filter(14, 4096, 0), filter(14, 4096, 0), 2, none, treatment(5, 100, 4));
	p.count = 2;
	word(p.row[1], filter(15, 4096, 0)); word(p.row[1] + 4, filter(8, 20, 4));
	word(p.row[1] + 8, 1U << 30 | none); word(p.row[1] + 12, treatment(5, 100, 4));
	packet = (struct rtl_evto_packet){.count = 1, .protocol = 0x0800, .tag = {{0x8100, 5 << 13 | 100}}};
	assert(rtl_evto_apply(&p, &packet, 1) && packet.count == 2);
	struct rtl_itu_paths *paths = calloc(1, sizeof(*paths)); assert(paths); paths->count = 2;
	for (unsigned int i = 0; i < 2; i++) {
		paths->path[i] = (struct rtl_itu_path){.gem_id = 4000 + i, .index = 8 + i,
			.vlan = RTL_ITU_VLAN_ANY, .pbits = 1U << (i ? 5 : 0), .tcont = 1, .unmarked_mode = 1};
		memset(paths->path[i].vlan_profile, 255, sizeof(paths->path[i].vlan_profile));
		paths->path[i].mapper_dscp[17] = 5 << 3; /* DSCP46 -> implied PCP5. */
	}
	struct rtl_evto_packet output;
	packet = (struct rtl_evto_packet){.protocol = 0x0800, .dscp = 46};
	assert(rtl_itu_path_transmit_vlan(paths, &packet, &output)->gem_id == 4001 && !output.count);
	for (unsigned int i = 0; i < 2; i++) { paths->path[i].unmarked_mode = 2; paths->path[i].default_priority = 5; }
	packet.dscp = 0; assert(rtl_itu_path_transmit_vlan(paths, &packet, &output)->gem_id == 4001);
	packet.count = 1; packet.tag[0] = (struct rtl_evto_tag){0x8100, 100};
	assert(rtl_itu_path_transmit_vlan(paths, &packet, &output)->gem_id == 4000); free(paths);
	puts("EVTO packet rules: stock cases, inverse modes0..8, DSCP, EAPOL and default precedence passed");
}
