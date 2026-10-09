/* SPDX-License-Identifier: GPL-2.0-only */
/* Native AP WCPU download packet, handshake and channel12 controls. */
#ifndef RTL_AX_AP_TRANSPORT_H
#define RTL_AX_AP_TRANSPORT_H
#include "download.h"
#include "fw.h"

#define RTL_AX_H2C_ENTRIES 256
#define RTL_AX_H2C_BUFFER_BYTES 2048
#define RTL_AX_H2C_RETAINED 8
/* Original DATA and LONG_DATA keep separate eight-entry work rings. One
 * additional slot per class is the currently available download packet. */
#define RTL_AX_H2C_CLASS_BUFFERS (RTL_AX_H2C_RETAINED + 1)
#define RTL_AX_H2C_POOL_BYTES (2 * RTL_AX_H2C_CLASS_BUFFERS * RTL_AX_H2C_BUFFER_BYTES)
#define RTL_AX_H2C_INDEX 0x1080
#define RTL_AX_WCPU_CONTROL 0x01e0

static inline void rtl_ax_store32(unsigned char *out, unsigned int value)
{
	out[0] = value;
	out[1] = value >> 8;
	out[2] = value >> 16;
	out[3] = value >> 24;
}

static inline int rtl_ax_fw_packet(unsigned char *out, size_t capacity,
	const unsigned char *data, unsigned int bytes, unsigned char sequence,
	int section)
{
	unsigned int prefix = section ? 16 : 24;
	unsigned int payload = section ? bytes : bytes + 8;

	if (bytes > RTL_AX_AP_CHUNK_BYTES || bytes + prefix > capacity)
		return -ENOSPC;
	memset(out, 0, prefix);
	/* Both chips use the short RX descriptor format for host FW packets. */
	rtl_ax_store32(out, payload | ((section ? 14U : 13U) << 24));
	if (!section) {
		/* H2C type0, MAC category1, download class3, firmware header func0.
		 * Header sequence is retained; this special command does not ack. */
		rtl_ax_store32(out + 16, 1 | (3U << 2) | (unsigned int)sequence << 24);
		rtl_ax_store32(out + 20, payload);
	}
	memcpy(out + prefix, data, bytes);
	return bytes + prefix;
}

static inline void rtl_ax_txbd(unsigned char *out, unsigned int dma_address,
			     unsigned int bytes)
{
	/* PCIe one-buffer descriptor, 32bit DMA, last segment bit30. */
	rtl_ax_store32(out, bytes | RTL_AX_BIT(30));
	rtl_ax_store32(out + 4, dma_address);
}

static inline int rtl_ax_h2c_ring_configure(const struct rtl_ax_io *io,
					 unsigned int dma_address)
{
	unsigned int last;
	int ret;

	rtl_ax_hci_pause(io);
	ret = io->poll32(io->ctx, 0x101c, RTL_AX_BIT(20) | RTL_AX_BIT(18), 0,
			 10, 100000, &last);
	if (ret)
		return ret;
	rtl_ax_update32(io, 0x1014, 0, RTL_AX_BIT(10));
	ret = io->poll32(io->ctx, RTL_AX_H2C_INDEX, 0x3ff03ff, 0, 10, 100000, &last);
	if (ret)
		return ret;
	io->write32(io->ctx, 0x1280, dma_address);
	io->write32(io->ctx, 0x1284, 0);
	rtl_ax_update32(io, 0x12e0, 0xffffff00, 0);
	rtl_ax_update16(io, 0x1038, 0x3ff, RTL_AX_H2C_ENTRIES);
	/* The original mac_bdram_tbl for both chips selects {60,4,1}. */
	rtl_ax_update32(io, 0x1328, 0x3f3f3f, 60 | (4U << 8) | (1U << 16));
	rtl_ax_update32(io, RTL_AX_HAXI_CONFIG, 0, RTL_AX_BIT(3));
	ret = io->poll32(io->ctx, RTL_AX_HAXI_CONFIG, RTL_AX_BIT(3), 0,
			 50, 500000, &last);
	return ret;
}

static inline void rtl_ax_h2c_start(const struct rtl_ax_io *io)
{
	/* Only firmware TX runs; no RX/normal-TX ring exists at this stage. */
	rtl_ax_update32(io, RTL_AX_HAXI_STOP1, RTL_AX_BIT(18), 0);
	rtl_ax_update32(io, RTL_AX_HAXI_CONFIG, RTL_AX_BIT(17) | RTL_AX_BIT(15),
			RTL_AX_BIT(7));
}

static inline void rtl_ax_wcpu_stop(const struct rtl_ax_io *io)
{
	rtl_ax_update32(io, RTL_AX_PLATFORM, RTL_AX_BIT(1), 0);
	rtl_ax_update32(io, RTL_AX_WCPU_CONTROL, 7, 0);
	rtl_ax_update32(io, 0x0008, RTL_AX_BIT(14), 0);
	/* Both original chips enable chk_patch_pcie_hang: toggle PLATFORM_EN. */
	rtl_ax_update32(io, RTL_AX_PLATFORM, RTL_AX_BIT(0), 0);
	rtl_ax_update32(io, RTL_AX_PLATFORM, 0, RTL_AX_BIT(0));
}

static inline int rtl_ax_wcpu_enable(const struct rtl_ax_io *io)
{
	unsigned int last, queues, indices;
	int ret;

	io->write32(io->ctx, 0x01e8, 0);
	io->write32(io->ctx, 0x0160, 0);
	io->write32(io->ctx, 0x0164, 0);
	io->write32(io->ctx, 0x0168, 0);
	io->write32(io->ctx, 0x016c, 0);
	io->write32(io->ctx, 0x01a4, io->read32(io->ctx, 0x01a4));
	rtl_ax_update32(io, 0x0008, 0, RTL_AX_BIT(14));
	rtl_ax_update32(io, RTL_AX_WCPU_CONTROL, 0xe7, 1);
	rtl_ax_update16(io, 0x01e6, 7, 0); /* Power-on boot reason. */
	/* Original fwdl_precheck: PLE PAXIDMA queues0/1 empty and channel12
	 * producer/consumer equal. DFI target7 is the queue-empty bitmap. */
	io->write32(io->ctx, 0x9110, RTL_AX_BIT(31) | (7U << 16));
	ret = io->poll32(io->ctx, 0x9110, RTL_AX_BIT(31), 0, 1, 2000, &last);
	if (ret)
		return ret;
	queues = io->read32(io->ctx, 0x9114);
	indices = io->read32(io->ctx, RTL_AX_H2C_INDEX);
	if ((queues & 0x300) != 0x300 ||
	    ((indices >> 16) & 0x3ff) != (indices & 0x3ff))
		return -EBUSY;
	rtl_ax_update32(io, RTL_AX_PLATFORM, 0, RTL_AX_BIT(1));
	return io->poll32(io->ctx, RTL_AX_WCPU_CONTROL, RTL_AX_BIT(1), RTL_AX_BIT(1),
			 1, 400000, &last);
}

static inline int rtl_ax_wcpu_start(const struct rtl_ax_io *io)
{
	rtl_ax_wcpu_stop(io);
	return rtl_ax_wcpu_enable(io);
}

static inline int rtl_ax_fw_result(unsigned int control)
{
	switch ((control >> 5) & 7) {
	case 7:
		return 0;
	case 2:
		return -EBADMSG;
	case 3:
		return -EKEYREJECTED;
	case 4:
		return -ENOEXEC;
	default:
		return -EINPROGRESS;
	}
}

struct rtl_ax_fw_transport {
	const struct rtl_ax_io *io;
	void *ctx;
	/* send owns packet DMA/completion; input data remains caller-owned. */
	int (*send)(void *ctx, const unsigned char *data, unsigned int bytes,
		    int section);
	int (*wait_ready)(void *ctx, unsigned int *control);
	unsigned int packets, transferred_bytes, last_control;
};

static inline int rtl_ax_fw_download(struct rtl_ax_fw_transport *transport,
		const unsigned char *data, const struct rtl_ax_ap_image *image)
{
	unsigned char header[32 + RTL_AX_AP_SECTIONS * 16];
	const struct rtl_ax_io *io = transport->io;
	unsigned int last, section;
	int bytes, ret;

	bytes = rtl_ax_ap_download_header(data, image, header, sizeof(header));
	if (bytes < 0)
		return bytes;
	transport->packets = transport->transferred_bytes = 0;
	ret = transport->send(transport->ctx, header, bytes, 0);
	if (ret)
		return ret;
	transport->packets++;
	transport->transferred_bytes += bytes;
	ret = io->poll32(io->ctx, RTL_AX_WCPU_CONTROL, RTL_AX_BIT(2), RTL_AX_BIT(2),
			 1, 400000, &last);
	if (ret) {
		transport->last_control = last;
		return ret;
	}
	io->write32(io->ctx, 0x0160, 0);
	io->write32(io->ctx, 0x0164, 0);
	for (section = 0; section < image->section_count; section++) {
		const struct rtl_ax_ap_section *part = &image->sections[section];
		unsigned int offset = 0;

		while (offset < part->bytes) {
			unsigned int packet = rtl_ax_ap_chunk_bytes(part->bytes - offset);

			ret = transport->send(transport->ctx, data + part->offset + offset,
					      packet, 1);
			if (ret)
				return ret;
			transport->packets++;
			transport->transferred_bytes += packet;
			offset += packet;
		}
	}
	ret = transport->wait_ready(transport->ctx, &transport->last_control);
	if (ret)
		return ret;
	return rtl_ax_fw_result(transport->last_control);
}
#endif
