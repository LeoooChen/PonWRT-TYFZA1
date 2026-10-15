/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RX_GEOMETRY_H
#define RTL_AX_AP_RX_GEOMETRY_H
#define RTL_AX_RX_ENTRIES 256
/* Full MPDU11966 plus RXBD/WD/shift/driver-info fits12288. */
#define RTL_AX_RXQ_BUFFER_BYTES 12288
/* Factory DFS threshold61:61*8+8 report bytes +20 RXBD/WD =516.
 * Use a1KiB slot in both hardware RPQ and the PE-to-ARM report ring. */
#define RTL_AX_RPQ_BUFFER_BYTES 1024
/* Two native PE mailbox words: physical RXQ/RPQ epoch boundaries. */
#define RTL_AX_PE_DFS_EPOCH 172

static inline unsigned int rtl_ax_rx_epoch_old(volatile unsigned int *epoch, unsigned int slot)
{
	unsigned int boundary = *epoch;

	if (!(boundary & 256)) return 0;
	if (slot == (boundary & 255)) {
		*epoch = 0;
		return 0;
	}
	return 1;
}
#endif
