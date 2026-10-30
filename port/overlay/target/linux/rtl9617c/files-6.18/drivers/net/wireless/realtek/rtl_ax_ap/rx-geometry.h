/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RTL_AX_AP_RX_GEOMETRY_H
#define RTL_AX_AP_RX_GEOMETRY_H
#define RTL_AX_RX_ENTRIES 256
/* Factory ca_ni_init_tx_dma_lso: 1024 eight-byte NI descriptors. */
#define RTL_AX_NI_TX_DEPTH 10
#define RTL_AX_NI_TX_ENTRIES (1U << RTL_AX_NI_TX_DEPTH)
#define RTL_AX_NI_TX_MASK (RTL_AX_NI_TX_ENTRIES - 1)
#define RTL_AX_NI_TX_BYTES (RTL_AX_NI_TX_ENTRIES * 8)
/* RXBDs, an independent busy-payload ring, and the two NI tail buffers. */
#define RTL_AX_RX_BUFFERS (RTL_AX_RX_ENTRIES * 2 + 2)
#define RTL_AX_RX_WORK_STATUS 0xf000
#define RTL_AX_RX_ARM_RING 0xf400
#define RTL_AX_RX_SLOT_MAP 0x17400
#define RTL_AX_RX_NI_TAIL 0x17810
#define RTL_AX_RX_REORDER_SEARCH 0x17820
#define RTL_AX_RX_WORK_IDS 0x1a000
#define RTL_AX_RX_IDLE_IDS 0x1a400
#define RTL_AX_RX_WORK_WRITE 174
#define RTL_AX_RX_WORK_PROCESS 175
#define RTL_AX_RX_WORK_READ 176
#define RTL_AX_RX_IDLE_COUNT 177
#define RTL_AX_RX_RDU 178
#define RTL_AX_RPQ_RDU 179
#define RTL_AX_RX_ARM_BARRIER 180
#define RTL_AX_PE_TX_BYTES 181
#define RTL_AX_RX_HW_PRODUCER 182
/* RX mailbox used only by PE1 to flush PE0 events to the shared timer. */
#define RTL_AX_RX_EVENT_CONTEXT 188
#define RTL_AX_RX_CROSS_TID 192
/* PE1 retains selected MPDUs locally until TXBD credit permits publication. */
#define RTL_AX_TX_PUBLISH_WRITE 193
#define RTL_AX_TX_PUBLISH_READ 194
#define RTL_AX_NI_PS_NOW 195
#define RTL_AX_NI_PS_TIMEOUT 0x19800
#define RTL_AX_NI_PS_SCAN 196
/* Six PE1-owned words, one per published TX DMA channel. */
#define RTL_AX_TX_LAST_PACKET 197
#define RTL_AX_NI_PS_CANCEL 0x19a00
#define RTL_AX_NI_PS_VERSION 0x19e00
#define RTL_AX_NI_PS_WP_GENERATION 0x1b800
#define RTL_AX_NI_PS_DONE 0x1ba00
/* PE1 publishes per-peer/TID TX activity; ARM owns its observation cursors. */
#define RTL_AX_NI_TX_ACTIVITY 0x1c000
#define RTL_AX_NI_TX_NOTIFY_TIME 0x1d000
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
