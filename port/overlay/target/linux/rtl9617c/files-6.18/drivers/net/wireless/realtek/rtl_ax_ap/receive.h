/* SPDX-License-Identifier: GPL-2.0-only */
/* Original TYFZA1 packet-mode RXBD and short/long RX WD framing. */
#ifndef RTL_AX_AP_RECEIVE_H
#define RTL_AX_AP_RECEIVE_H
#include "transport.h"

#define RTL_AX_RX_ENTRIES 256
/* Full MPDU11966 plus RXBD/WD/shift/driver-info fits12288;14-bit BD size.
 * RPQ121 four-byte reports plus RXBD/short WD fit512. */
#define RTL_AX_RXQ_BUFFER_BYTES 12288
#define RTL_AX_RPQ_BUFFER_BYTES 512

struct rtl_ax_rx_packet {
	const unsigned char *descriptor, *driver_info, *payload;
	unsigned int bytes, driver_info_bytes;
	unsigned char type, long_descriptor;
	unsigned char crc_error, icv_error;
};

static inline unsigned int rtl_ax_rx_next_tag(unsigned int tag)
{
	return tag == 8191 ? 1 : tag + 1;
}

static inline void rtl_ax_rxbd(unsigned char *out, unsigned int address,
			       unsigned int buffer_bytes)
{
	rtl_ax_store32(out, buffer_bytes);
	rtl_ax_store32(out + 4, address);
}

static inline int rtl_ax_receive_packet(const unsigned char *buffer,
				       unsigned int capacity, struct rtl_ax_rx_packet *packet)
{
	unsigned int info = rtl_ax_ap_read32(buffer);
	unsigned int written = info & 0x3fff, word, descriptor_bytes, offset;

	/* hal_handle_rxbd_info_Ce/XBe accepts FS=LS=1 only. No invented
	 * scatter reassembly;A-MSDU cut is a separate higher-level contract. */
	if ((info & 0xc000) != 0xc000 || written < 20 || written > capacity)
		return -EINVAL;
	packet->descriptor = buffer + 4;
	word = rtl_ax_ap_read32(packet->descriptor);
	packet->bytes = word & 0x3fff;
	packet->type = (word >> 24) & 15;
	packet->long_descriptor = word >> 31;
	/* Actual C/XB RX WD parsing: DWORD3 bits9/10 are CRC32/ICV errors. */
	packet->crc_error = (rtl_ax_ap_read32(packet->descriptor + 12) >> 9) & 1;
	packet->icv_error = (rtl_ax_ap_read32(packet->descriptor + 12) >> 10) & 1;
	descriptor_bytes = packet->long_descriptor ? 32 : 16;
	packet->driver_info_bytes = ((word >> 28) & 7) * 16;
	offset = 4 + descriptor_bytes + ((word >> 14) & 3) * 2 + packet->driver_info_bytes;
	if ((word & RTL_AX_BIT(23)) && packet->type != 1)
		offset += 4;
	/* hal_handle_rx_buffer checks pkt pointer+WD pktlen against DMA size. */
	if (offset > written || packet->bytes > written - offset)
		return -EINVAL;
	packet->driver_info = packet->driver_info_bytes ? buffer + 4 + descriptor_bytes : NULL;
	packet->payload = buffer + offset;
	return 0;
}

struct rtl_ax_c2h {
	unsigned char category, class, function, sequence;
	unsigned int bytes;
	const unsigned char *payload;
};

static inline int rtl_ax_c2h_parse(const unsigned char *data, unsigned int bytes,
				 struct rtl_ax_c2h *message)
{
	unsigned int word, length;

	if (bytes < 8)
		return -EINVAL;
	word = rtl_ax_ap_read32(data);
	length = rtl_ax_ap_read32(data + 4) & 0x3fff;
	if (((word >> 16) & 15) != 1 || length < 8 || length > bytes)
		return -EINVAL;
	message->category = word & 3;
	message->class = (word >> 2) & 63;
	message->function = (word >> 8) & 255;
	message->sequence = word >> 24;
	message->bytes = length - 8;
	message->payload = data + 8;
	return 0;
}

static inline int rtl_ax_rx_configure(const struct rtl_ax_io *io,
				     unsigned int rxq_dma, unsigned int rpq_dma)
{
	unsigned int last, queue, addresses[] = {rxq_dma, rpq_dma};
	int ret;

	rtl_ax_hci_pause(io);
	ret = io->poll32(io->ctx, 0x1208, 3, 0, 10, 100000, &last);
	if (ret)
		return ret;
	/* Original clr_idx_ch RXQ/RPQ,then NUM/DESA from host ring resource. */
	io->write32(io->ctx, 0x1200, 3);
	for (queue = 0; queue < 2; queue++) {
		ret = io->poll32(io->ctx, 0x1218 + queue * 4, 0x3ff03ff, 0,
				 10, 100000, &last);
		if (ret)
			return ret;
		rtl_ax_update16(io, 0x1210 + queue * 2, 0x3ff, RTL_AX_RX_ENTRIES);
		io->write32(io->ctx, 0x1220 + queue * 8, addresses[queue]);
		io->write32(io->ctx, 0x1224 + queue * 8, 0);
	}
	return 0;
}
#endif
