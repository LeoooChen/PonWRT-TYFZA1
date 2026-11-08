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
#define RTL_MC_PE_CAPABILITY 0x4d435038U
/* Request/recipe key: GCMP family bit27, 256-bit key bit28. */
#define RTL_MC_PE_KEY_GCMP (1U << 27)
#define RTL_MC_PE_KEY_256 (1U << 28)
/* Independent direct-cookie BID count and post-close EPP-prefix ACK. */
#define RTL_MC_PE_DRAIN 0x1e840
_Static_assert(RTL_FTTR_PE_DRAIN + sizeof(struct rtl_fttr_pe_drain) <= RTL_MC_PE_DRAIN, "drain separation");
_Static_assert(RTL_MC_PE_DRAIN + sizeof(struct rtl_fttr_pe_drain) <= 0x1e900, "drain before GTK PN");
/* Immutable stamp for each of the256 ARM software WP IDs, outside requests. */
#define RTL_MC_PE_CPU_STAMPS 0x68000
#define RTL_MC_PE_COOKIES 256
#define RTL_MC_PE_BCMC (1U << 15)
#define RTL_MC_PE_BCMC_ROUTE 128
#define RTL_MC_PE_BCMC_BASE 0x1fd40

/* Single AP interface, published generation last; zero closes admission. */
struct rtl_mc_pe_bcmc {
 unsigned int generation, sequence, key_generation, rate, key;
 unsigned char address[6], reserved[2];
};
_Static_assert(sizeof(struct rtl_mc_pe_bcmc) == 28, "BCMC template ABI");
_Static_assert(RTL_MC_PE_BCMC_BASE + sizeof(struct rtl_mc_pe_bcmc) <= 0x20000, "BCMC context bounds");
#define RTL_MC_PE_TID_SHIFT 8
#define RTL_MC_PE_MODE_SHIFT 11
#define RTL_MC_PE_TID_FIXED 0
#define RTL_MC_PE_TID_COS 1
#define RTL_MC_PE_TID_DSCP 2
#define RTL_MC_PE_SOURCE_SHIFT 16

struct rtl_mc_pe_record {
	unsigned int epoch;
	/* MACID0 requires BCMC bit15. bits8..10=fixed TID,11..12=TID mode,
	 * bits16..21=original NI source. Other bits must be zero. BCMC has no TID/mode bits. */
	unsigned int control;
	unsigned char destination[6], noack_map, qos;
	/* Already resolved through current Linux WME/ACM/reserved-TID policy.
	 * COS mode uses entries0..7; DSCP mode uses all64 entries. */
	union {
  unsigned char dscp_tid[64];
  struct {unsigned int generation, sequence, key_generation, rate;} bcmc;
 };
	unsigned char peer[6], reserved[2];
	unsigned int key, flags;
};

struct rtl_mc_pe_table {
	unsigned int epoch;
	struct rtl_fttr_pe_generation fttr;
	unsigned int cpu_generation; /* built-in core owns publication, zero closes */
	struct rtl_mc_pe_record record[RTL_MC_PE_COOKIES];
};

_Static_assert(sizeof(struct rtl_mc_pe_record) == 96, "multicast PE record ABI");
_Static_assert(sizeof(struct rtl_mc_pe_table) == 24592, "multicast PE table ABI");

#endif
