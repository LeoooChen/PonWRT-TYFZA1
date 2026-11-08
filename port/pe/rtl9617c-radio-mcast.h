/* SPDX-License-Identifier: GPL-2.0-only */
#include <linux/rtl9617c-mcast-pe.h>

/* Metadata is read from a fresh NI buffer before any header editing. A
 * reserved POL cannot fall through to the normal STA catch-all route. */
static u32 ni_mcast_cookie(u32 buffer)
{
	const u32 *head = uncached(buffer + RTL_QM_HEADROOM);
	u32 pol = (__builtin_bswap32(head[0]) >> 18) & 511;
	return pol & 256 ? pol : 0;
}

static u32 ni_mcast_current(volatile u32 *mailbox, u32 cookie)
{
	u32 address = mailbox[RTL_MC_PE_TABLE_MAILBOX];
	if (!address || !(cookie & 255)) return 0;
	volatile struct rtl_mc_pe_table *table = uncached(address);
	u32 epoch = table->epoch;
	sync();
	u32 record_epoch = table->record[cookie & 255].epoch;
	sync();
	return epoch && epoch == record_epoch && epoch == table->epoch;
}

static u32 ni_bcmc_current(volatile u32 *mailbox,const volatile struct rtl_mc_pe_record *record)
{
 u8 *memory=(u8 *)mailbox-MAILBOX_OFFSET;
 const volatile struct rtl_mc_pe_bcmc *b=(void *)(memory+RTL_MC_PE_BCMC_BASE);
 const volatile struct rtl_seq_shared *sequence=(void *)(memory+RTL_SEQ_BASE);
 u32 generation=b->generation;
 sync();
 if(!generation || record->bcmc.generation!=generation || !sequence->generation ||
    record->bcmc.sequence!=sequence->generation || record->bcmc.sequence!=b->sequence ||
    record->bcmc.key_generation!=b->key_generation || record->bcmc.rate!=b->rate || record->key!=b->key ||
    record->qos || record->noack_map || (record->control&~(RTL_MC_PE_BCMC|0x3f0000U)) ||
    !(record->destination[0]&1) || record->flags!=(BIT(18)|BIT(13)|BIT(14)|(b->key_generation?BIT(15):0)))return 0;
 if(b->key_generation) {
  const volatile struct rtl_key_pn_shared *key=(void *)(memory+RTL_KEY_PN_BASE+((b->key>>16)&127)*sizeof(*key));
  if(key->generation!=b->key_generation)return 0;
 }
 for(u32 i=0;i<6;i++)if(record->peer[i]!=b->address[i] || record->peer[i]!=((volatile u8 *)mailbox)[64+i])return 0;
 sync();return b->generation==generation;
}

static u32 ni_mcast_peer(volatile u32 *mailbox, const volatile struct rtl_mc_pe_record *record)
{
	u32 macid = record->control & 127;
 if(record->control&RTL_MC_PE_BCMC)return ni_bcmc_current(mailbox,record)?RTL_MC_PE_BCMC_ROUTE:0;
	u8 *memory = (u8 *)mailbox - MAILBOX_OFFSET;
	const volatile u32 *peer = (void *)(memory + 0xd000 + macid * 32);
	if (!macid || !*(volatile u32 *)(memory + 0x11a00 + macid * 4) ||
	    (peer[4] & (BIT(16) | BIT(18) | BIT(23))) != record->flags ||
	    !(record->flags & BIT(18)) || (!(record->flags & BIT(16)) && record->key) ||
	    (peer[5] & 0xffff0000U) != record->key || record->qos > 1 ||
	    ((peer[2] >> 16) & 63) != (record->qos ? 26 : 24)) return 0;
#pragma clang loop unroll(disable)
	for (u32 i = 0; i < 6; i++)
		if (*(volatile u8 *)(memory + 0xe400 + macid * 8 + i) != record->peer[i]) return 0;
	return macid;
}

static u32 __attribute__((section(".rxq"), noinline)) ni_mcast_admit(
	volatile u32 *mailbox, u8 *memory, u32 buffer, u8 *ethernet,
	u32 length, u32 cookie, u32 *tid)
{
	if (!ni_mcast_current(mailbox, cookie)) return 0;
	volatile struct rtl_mc_pe_table *table = uncached(mailbox[RTL_MC_PE_TABLE_MAILBOX]);
	const volatile struct rtl_mc_pe_record *record = &table->record[cookie & 255];
	u32 control = record->control, macid = control & 127;
	u32 mode = (control >> RTL_MC_PE_MODE_SHIFT) & 3;
	const u32 *head = uncached(buffer + RTL_QM_HEADROOM);
	u32 route = __builtin_bswap32(head[1]);
	if(control&RTL_MC_PE_BCMC) {
  if(!ni_bcmc_current(mailbox,record) || ((route>>9)&63)!=(control>>16) ||
     ((route>>3)&63)!=16+(mailbox[114]-0x7000)/32 || record->reserved[0] || record->reserved[1] ||
     !ni_mcast_current(mailbox,cookie))return 0;
  for(u32 i=0;i<6;i++)if(ethernet[i]!=record->destination[i])return 0;
  *tid=0;return RTL_MC_PE_BCMC_ROUTE;
 }
	if (!ni_mcast_peer(mailbox, record) || (control & ~0x003f1f7fU) || mode > RTL_MC_PE_TID_DSCP ||
	    ((route >> 9) & 63) != (control >> RTL_MC_PE_SOURCE_SHIFT) ||
	    ((route >> 3) & 63) != 16 + (mailbox[114] - 0x7000) / 32 ||
	    !*(volatile u32 *)(memory + 0x11a00 + macid * 4)) return 0;
	u32 flags = *(volatile u32 *)(memory + 0xd000 + macid * 32 + 16);
	/* STA group Ethernet frames still use the associated AP as unicast RA
	 * with its pairwise key. AP BCMC needs a different DTIM/GTK contract. */
	if ((record->destination[0] & 1) && !(flags & BIT(23))) return 0;
	u32 nonzero = 0;
	const u8 *peer = memory + 0xe400 + macid * 8;
#pragma clang loop unroll(disable)
	for (u32 i = 0; i < 6; i++) {
		nonzero |= record->destination[i];
		if (!(flags & BIT(23)) && peer[i] != record->destination[i]) return 0;
	}
	if (!nonzero || record->reserved[0] || record->reserved[1]) return 0;
	u32 priority = (control >> RTL_MC_PE_TID_SHIFT) & 7;
	if (mode == RTL_MC_PE_TID_COS) priority = record->dscp_tid[*tid & 7];
	if (mode == RTL_MC_PE_TID_DSCP) {
		u32 protocol = ethernet[12] << 8 | ethernet[13], dscp;
		/* VLAN QoS is compiled as fixed/header-COS by the publisher. */
		if (protocol == 0x800 && length >= 34 && ethernet[14] == 0x45)
			dscp = ethernet[15] >> 2;
		else if (protocol == 0x86dd && length >= 54 && ethernet[14] >> 4 == 6)
			dscp = ((ethernet[14] & 15) << 4 | ethernet[15] >> 4) >> 2;
		else return 0;
		priority = record->dscp_tid[dscp];
	}
	if (priority > 7 || !ni_mcast_current(mailbox, cookie) || !ni_mcast_peer(mailbox, record)) return 0;
#pragma clang loop unroll(disable)
	for (u32 i = 0; i < 6; i++) ethernet[i] = record->destination[i];
	*tid = priority;
	return macid;
}

static u32 ni_mcast_stale(volatile u32 *mailbox, const u32 *waiting)
{
	u32 cookie = waiting[4];
	if (!cookie) return 0;
	if (!ni_mcast_current(mailbox, cookie)) return 1;
	const volatile struct rtl_mc_pe_table *table = uncached(mailbox[RTL_MC_PE_TABLE_MAILBOX]);
	return !ni_mcast_peer(mailbox, &table->record[cookie & 255]);
}

static u32 ni_mcast_noack(volatile u32 *mailbox, const u32 *waiting, u32 tid)
{
	if (!waiting[4]) return 0;
	const volatile struct rtl_mc_pe_table *table = uncached(mailbox[RTL_MC_PE_TABLE_MAILBOX]);
	return (table->record[waiting[4] & 255].noack_map >> tid) & 1;
}
