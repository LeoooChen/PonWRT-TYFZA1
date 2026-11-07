/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_MCAST_PE_H
#define RTL9617C_MCAST_PE_H

#include <linux/rtl9617c-fttr-pe.h>

/* ARM publishes a coherent, little-endian table; PE1 is read-only. Cookies
 * share the NI group's quarantined 1..255 namespace and are never reused.
 * Publish immutable records before the nonzero epoch. Close epoch first on
 * ANY change to membership, Linux QoS policy, peer/key identity or routing.
 * The owner must join PE before freeing this DMA allocation. A closed table
 * rejects late copies; it does not retire packets already handed to PCIe.
 */
#define RTL_MC_PE_TABLE_MAILBOX 210
#define RTL_MC_PE_CAP_MAILBOX 211
#define RTL_MC_PE_CAPABILITY 0x4d435032U
#define RTL_MC_PE_COOKIES 256
#define RTL_MC_PE_TID_SHIFT 8
#define RTL_MC_PE_MODE_SHIFT 11
#define RTL_MC_PE_TID_FIXED 0
#define RTL_MC_PE_TID_COS 1
#define RTL_MC_PE_TID_DSCP 2
#define RTL_MC_PE_SOURCE_SHIFT 16

struct rtl_mc_pe_record {
	unsigned int epoch;
	/* MACID0 is invalid. bits8..10=fixed TID,11..12=TID mode,
	 * bits16..21=original NI source. Other bits must be zero. */
	unsigned int control;
	unsigned char destination[6], noack_map, qos;
	/* Already resolved through current Linux WME/ACM/reserved-TID policy.
	 * COS mode uses entries0..7; DSCP mode uses all64 entries. */
	unsigned char dscp_tid[64];
	unsigned char peer[6], reserved[2];
	unsigned int key, flags;
};

struct rtl_mc_pe_table {
	unsigned int epoch;
	struct rtl_fttr_pe_generation fttr;
	unsigned int reserved;
	struct rtl_mc_pe_record record[RTL_MC_PE_COOKIES];
};

_Static_assert(sizeof(struct rtl_mc_pe_record) == 96, "multicast PE record ABI");
_Static_assert(sizeof(struct rtl_mc_pe_table) == 24592, "multicast PE table ABI");

#endif
