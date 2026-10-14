/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_NI_STATE_H
#define RTL_AX_NI_STATE_H
struct rtl_ax_tx;
struct rtl_ax_ni_state {
 struct rtl_ax_tx *tx;
 void __iomem *regs, *dma, *vp;
 void *test_memory, *uplink_memory;
 dma_addr_t test_dma, uplink_dma;
 u32 registers[14], saved[14], arb[8], epp_bases[8], epp_eq[8];
 u32 attrs[2][2], vp_saved[5];
 unsigned int cpu, eq, profile, vp_index;
 bool active, vp_active;
 struct net_device *route_dev;
 unsigned int route_macid;
 u32 route_tim[128];
 bool route_peers[128];
 struct work_struct ps_work;
};
#endif
