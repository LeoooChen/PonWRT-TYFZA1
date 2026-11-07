/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL9617C_FTTR_PE_H
#define RTL9617C_FTTR_PE_H

/* Native ARM/PE request ABI; the ten address pairs end at word27. */
#define RTL_FTTR_PE_REQUEST_LOW 28
#define RTL_FTTR_PE_REQUEST_HIGH 29
#define RTL_FTTR_PE_CAP_MAILBOX 212
#define RTL_FTTR_PE_CAPABILITY 0x46544732U
/* One PE-written, ARM-cleared word per ARM WP. No shared bitmap RMW. */
#define RTL_FTTR_PE_DISCARD 0x1e400
#define RTL_FTTR_PE_DRAIN 0x1e800
#define RTL_FTTR_PE_EPP_CONSUMERS 0x11c00

/* ARM writes only request. PE1 owns ACK, BID count and the eight captured
 * EPP boundaries. A new request is issued AFTER FE withdrawal, including
 * retries within the same closed generation. Sequence zero is inactive. */
struct rtl_fttr_pe_drain {
	unsigned int request, ack, pending, seen, epp_mask, target[8];
};

/* Coherent little-endian storage. ARM publishes odd low first, high next,
 * then the final low word. PE checks low/high/low without spinning. */
struct rtl_fttr_pe_generation {
	unsigned int low, high;
};

#endif
