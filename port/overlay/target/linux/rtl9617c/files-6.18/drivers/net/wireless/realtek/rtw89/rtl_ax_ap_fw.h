/* SPDX-License-Identifier: GPL-2.0-only */
/* Native format for this board's8852C/8192XB AP WCPU images. Field behavior
 * references Realtek's GPL fwdl.c and the original board firmware arrays. */
#ifndef RTL_AX_AP_FW_H
#define RTL_AX_AP_FW_H
#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/string.h>
#include <linux/types.h>
#else
#include <errno.h>
#include <stddef.h>
#include <string.h>
#endif

#define RTL_AX_AP_SECTIONS 6
#define RTL_AX_AP_CHUNK_BYTES 2020
struct rtl_ax_ap_section {
	unsigned int address, offset, bytes;
	unsigned char type, redownload;
};
struct rtl_ax_ap_image {
	unsigned int chip, commit, header_bytes, dynamic_bytes, section_count;
	unsigned char cut, version[4], command_version;
	struct rtl_ax_ap_section sections[RTL_AX_AP_SECTIONS];
};
static inline unsigned int rtl_ax_ap_read32(const unsigned char *data)
{
	return (unsigned int)data[0] | (unsigned int)data[1] << 8 |
	       (unsigned int)data[2] << 16 | (unsigned int)data[3] << 24;
}
static inline const char *rtl_ax_ap_name(unsigned int mac_chip_id, unsigned int cv)
{
	/* Original MAC enum2/3 selects family;cv0 selectsu1,otherwiseu2. */
	if (mac_chip_id == 2)
		return cv ? "realtek/tyfza1/rtl8852c-u2-ap.bin" : "realtek/tyfza1/rtl8852c-u1-ap.bin";
	if (mac_chip_id == 3)
		return cv ? "realtek/tyfza1/rtl8192xb-u2-ap.bin" : "realtek/tyfza1/rtl8192xb-u1-ap.bin";
	return NULL;
}
static inline int rtl_ax_ap_parse(const unsigned char *data, size_t bytes,
				struct rtl_ax_ap_image *image)
{
	unsigned int word, base_header, section, position;

	if (bytes < 32)
		return -EINVAL;
	memset(image, 0, sizeof(*image));
	word = rtl_ax_ap_read32(data);
	image->chip = word >> 8;
	image->cut = word & 255;
	memcpy(image->version, data + 4, 4);
	image->commit = rtl_ax_ap_read32(data + 8);
	image->header_bytes = data[14];
	image->section_count = (rtl_ax_ap_read32(data + 24) >> 8) & 255;
	image->command_version = data[31];
	if (!image->section_count || image->section_count > RTL_AX_AP_SECTIONS)
		return -EINVAL;
	base_header = 32 + image->section_count * 16;
	if (image->header_bytes < base_header || image->header_bytes > bytes)
		return -EINVAL;
	if (data[30] & 1) {
		image->dynamic_bytes = image->header_bytes - base_header;
		if (image->dynamic_bytes < 4 ||
		    rtl_ax_ap_read32(data + base_header) != image->dynamic_bytes)
			return -EINVAL;
	} else if (image->header_bytes != base_header) {
		return -EINVAL;
	}
	position = image->header_bytes;
	for (section = 0; section < image->section_count; section++) {
		const unsigned char *header = data + 32 + section * 16;
		struct rtl_ax_ap_section *output = &image->sections[section];

		word = rtl_ax_ap_read32(header + 4);
		output->address = rtl_ax_ap_read32(header); /* RISC-V full download address. */
		output->offset = position;
		output->bytes = (word & 0xffffff) + ((word & (1U << 28)) ? 8 : 0);
		output->type = (word >> 24) & 15;
		output->redownload = !!(word & (1U << 29));
		if (output->bytes > bytes - position)
			return -EINVAL;
		position += output->bytes;
	}
	return position == bytes ? 0 : -EINVAL;
}
static inline int rtl_ax_ap_download_header(const unsigned char *data,
	const struct rtl_ax_ap_image *image, unsigned char *output, size_t capacity)
{
	unsigned int bytes = image->header_bytes - image->dynamic_bytes;

	if (capacity < bytes)
		return -ENOSPC;
	memcpy(output, data, bytes);
	/* Firmware phase1 excludes dynamic metadata and advertises2020-byte
	 * section packets. Preserve the original flags and command version. */
	output[28] = RTL_AX_AP_CHUNK_BYTES & 255;
	output[29] = RTL_AX_AP_CHUNK_BYTES >> 8;
	return bytes;
}
static inline unsigned int rtl_ax_ap_chunk_bytes(unsigned int remaining)
{
	return remaining < RTL_AX_AP_CHUNK_BYTES ? remaining : RTL_AX_AP_CHUNK_BYTES;
}
#endif
