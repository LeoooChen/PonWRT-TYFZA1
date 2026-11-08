/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_TRAFFIC_FLOW_H
#define RTL_AX_TRAFFIC_FLOW_H
/* TXSC identifies an AP stream by Ethernet source and EtherType. Its eight
 * entries measure hits over four original HZ100 ticks (40ms). Each execution
 * owner keeps its own counters; the host combines equal keys at observation. */
struct rtl_ax_traffic_flow {
	unsigned int key[2], hits, last_hits, time, average, valid, reserved;
};
static inline void rtl_ax_traffic_flow_hit(struct rtl_ax_traffic_flow table[8],
	unsigned int low, unsigned int high, unsigned int now, unsigned int packets)
{
	struct rtl_ax_traffic_flow *flow = 0;
	unsigned int minimum = 400;
	for (unsigned int i = 0; i < 8; i++) {
		if (table[i].valid && table[i].key[0] == low && table[i].key[1] == high) {
			flow = &table[i]; break;
		}
	}
	if (!flow) {
		for (unsigned int i = 0; i < 8; i++) {
			if (!table[i].valid || (unsigned int)(now - table[i].time) > 80000) {
				flow = &table[i]; break;
			}
			if (table[i].average && table[i].average < minimum) {
				minimum = table[i].average; flow = &table[i];
			}
		}
		if (!flow) return;
		flow->key[0] = low; flow->key[1] = high;
		flow->hits = flow->last_hits = flow->average = 0;
		flow->time = now; flow->valid = 1;
	}
	flow->hits += packets;
	unsigned int elapsed = now - flow->time;
	if (elapsed >= 40000) {
		flow->average = flow->hits - flow->last_hits;
		if (elapsed >= 120000) flow->average = 1;
		else if (elapsed >= 80000) {
			flow->average >>= 1;
			if (!flow->average) flow->average = 1;
		}
		flow->last_hits = flow->hits; flow->time = now;
	}
}
#endif
