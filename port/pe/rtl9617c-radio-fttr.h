/* SPDX-License-Identifier: GPL-2.0-only */
/* PE1 owns the EPP/queued/selected BID lifetime, including closed arrivals. */
static u64 ni_fttr_generation(volatile u32 *mailbox)
{
	u32 address = mailbox[RTL_MC_PE_TABLE_MAILBOX];
	if (!address) return 1;
	volatile struct rtl_mc_pe_table *table = uncached(address);
	u32 low = table->fttr.low;
	sync();
	u32 high = table->fttr.high;
	sync();
	if ((low & 1) || table->fttr.low != low || !(low | high)) return 1;
	return (u64)high << 32 | low;
}

static u32 ni_fttr_stale(volatile u32 *mailbox, const u32 *waiting)
{
	u64 generation = (u64)waiting[6] << 32 | waiting[5];
	return generation && ((generation & 1) || generation != ni_fttr_generation(mailbox));
}

static u32 ni_fttr_receive(volatile u32 *mailbox, u8 *memory, u32 buffer)
{
	const u32 *head = uncached(buffer + RTL_QM_HEADROOM);
	u32 *waiting = uncached(buffer);
	u64 generation = 0;
	if (((__builtin_bswap32(head[1]) >> 9) & 63) == 6) {
		generation = ni_fttr_generation(mailbox);
		struct rtl_fttr_pe_drain *drain = (void *)(memory + RTL_FTTR_PE_DRAIN);
		drain->pending++;
	}
	/* Words5/6 stay in BID headroom, before hardware HEADER_A at64. The
	 * nonzero closed value also accounts rejected arrivals until EQ return. */
	waiting[5] = generation; waiting[6] = generation >> 32;
	return !(generation & 1);
}

static void __attribute__((section(".rxq"), noinline)) ni_prefix_drain(
	volatile u32 *mailbox, u8 *memory, volatile struct rtl_fttr_pe_drain *drain)
{
	u32 request = drain->request;
	if (!request) return;
	sync();
	if (request != drain->seen) {
		volatile u32 *ni = (void *)0xb0000000;
		const u32 *consumers = (void *)(memory + RTL_FTTR_PE_EPP_CONSUMERS);
		u32 mask = 0;
		if (mailbox[24]) {
			u32 ring_mask = (1U << mailbox[167]) - 1;
			for (u32 cos = 0; cos < 8; cos++) {
				u32 target = ni[mailbox[114] / 4 + cos] & ring_mask;
				drain->target[cos] = target;
				if (target != consumers[cos]) mask |= BIT(cos);
			}
		}
		drain->epp_mask = mask;
		drain->seen = request;
	}
	/* NI disable is published only after its queued/WP/EPP owners drained. */
	if (!mailbox[24]) drain->epp_mask = 0;
	/* A source6 arrival after ACK is rejected while closed and still counted
	 * until returned. This captured prefix is not an upstream MCE fence. */
	if (!drain->epp_mask && !drain->pending) {
		sync();
		drain->ack = request;
	}
}

static void ni_fttr_drain(volatile u32 *mailbox,u8 *memory)
{
 ni_prefix_drain(mailbox,memory,(void *)(memory+RTL_FTTR_PE_DRAIN));
 ni_prefix_drain(mailbox,memory,(void *)(memory+RTL_MC_PE_DRAIN));
}
