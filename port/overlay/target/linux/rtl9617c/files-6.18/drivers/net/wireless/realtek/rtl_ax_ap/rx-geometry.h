/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RX_GEOMETRY_H
#define RTL_AX_AP_RX_GEOMETRY_H
#define RTL_AX_RX_ENTRIES 256
/* RXBDs, an independent busy-payload ring, and the two NI tail buffers. */
#define RTL_AX_RX_BUFFERS (RTL_AX_RX_ENTRIES * 2 + 2)
#define RTL_AX_RX_WORK_STATUS 0xf000
#define RTL_AX_RX_ARM_RING 0xf400
#define RTL_AX_RX_SLOT_MAP 0x17400
#define RTL_AX_RX_NI_TAIL 0x17810
#define RTL_AX_RX_WORK_IDS 0x1a000
#define RTL_AX_RX_IDLE_IDS 0x1a400
#define RTL_AX_RX_WORK_WRITE 174
#define RTL_AX_RX_WORK_PROCESS 175
#define RTL_AX_RX_WORK_READ 176
#define RTL_AX_RX_IDLE_COUNT 177
#define RTL_AX_RX_RDU 178
#define RTL_AX_RPQ_RDU 179
#define RTL_AX_RX_WORK_OLD (1U << 31)
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
