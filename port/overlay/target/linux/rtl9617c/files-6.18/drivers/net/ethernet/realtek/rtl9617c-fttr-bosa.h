/* SPDX-License-Identifier: GPL-2.0-only */
/* Optical calibration files and register sequences from TYFZA1 rtkoltbosa. */
#ifndef RTL9617C_FTTR_BOSA_H
#define RTL9617C_FTTR_BOSA_H

/* rtkoltbosa's four reachable optical operation tables. The address byte
 * belongs to this unit's calibration, not to the MiniOLT/LAN model.
 */
enum rtl_fttr_optical_model {
	RTL_FTTR_UX3326 = 1, RTL_FTTR_UX3326S = 2,
	RTL_FTTR_GN25L99 = 10, RTL_FTTR_GN25L81 = 20,
};

static inline const char *rtl_fttr_optical_name(unsigned int model)
{
	switch (model) {
	case RTL_FTTR_UX3326: return "UX3326";
	case RTL_FTTR_UX3326S: return "UX3326S";
	case RTL_FTTR_GN25L99: return "GN25L99";
	case RTL_FTTR_GN25L81: return "GN25L81";
	default: return "unknown";
	}
}

static inline int rtl_fttr_optical_validate(const unsigned char *cal, unsigned int size)
{
	unsigned int expected, i;
	unsigned char checksum = 0;

	if (size < 5)
		return -EMSGSIZE;
	switch (cal[3]) {
	case RTL_FTTR_UX3326: expected = 588; break;
	case RTL_FTTR_UX3326S: expected = 901; break;
	case RTL_FTTR_GN25L99: expected = 661; break;
	case RTL_FTTR_GN25L81: expected = 284; break;
	default: return -EOPNOTSUPP;
	}
	if (size != expected)
		return -EMSGSIZE;
	if (((unsigned int)cal[0] << 8 | cal[1]) != size || (cal[4] & 1) ||
	    cal[4] < 0x10 || cal[4] > 0xee)
		return -EINVAL;
	for (i = 0; i < size; i++)
		checksum ^= cal[i];
	return checksum ? -EBADMSG : 0;
}

static inline int rtl_fttr_ux3326s_unlock(void *context,
		int (*write)(void *, unsigned int, unsigned char))
{
	static const unsigned char key[] = { 0x66, 0x99, 0x55, 0xaa };
	unsigned int i;
	int ret;

	for (i = 0; i < 4; i++) {
		ret = write(context, 0x7b + i, key[i]);
		if (ret)
			return ret;
	}
	return 0;
}

static inline int rtl_fttr_optical_identity(void *context,
		int (*read)(void *, unsigned int), unsigned int model,
		unsigned int *marker)
{
	static const unsigned char ux_id[] = "UX3326S1";
	unsigned int i;
	int value;

	if (model == RTL_FTTR_UX3326S) {
		for (i = 0; i < 8; i++) {
			value = read(context, 0xc4 + i);
			if (value < 0)
				return value;
			if (!i)
				*marker = value;
			if (value != ux_id[i])
				return -ENODEV;
		}
		return 0;
	}
	value = read(context, model == RTL_FTTR_UX3326 ? 0 : 0x60);
	if (value < 0)
		return value;
	*marker = value;
	/* UX3326 has no original chip-ID predicate; file type/address and an
	 * acknowledged read select it. Never label that as silicon identification.
	 */
	if (model == RTL_FTTR_UX3326)
		return 0;
	return value == (model == RTL_FTTR_GN25L99 ? 0xb0 : 0xa1) ? 0 : -ENODEV;
}

static inline int rtl_fttr_optical_calibrate(void *context,
		int (*write)(void *, unsigned int, unsigned char), const unsigned char *cal)
{
	static const struct { unsigned int page, end, offset; } ux_pages[] = {
		{ 0x81, 0xe8, 0x85 }, { 0x82, 0xe8, 0x105 },
		{ 0x83, 0xb4, 0x185 }, { 0x84, 0xb4, 0x205 },
		{ 0x85, 0x9f, 0x285 }, { 0x80, 0xab, 5 },
	};
	unsigned int reg, page, count;
	int ret;

	if (cal[3] != RTL_FTTR_UX3326S) {
		count = cal[3] == RTL_FTTR_UX3326 ? 56 : cal[3] == RTL_FTTR_GN25L99 ? 128 : 256;
		for (reg = 0; reg < count; reg++) {
			ret = write(context, reg, cal[0x1c + reg]);
			if (ret)
				return ret;
		}
		return 0;
	}
	ret = rtl_fttr_ux3326s_unlock(context, write);
	if (ret)
		return ret;
	for (page = 0; page < sizeof(ux_pages) / sizeof(ux_pages[0]); page++) {
		ret = write(context, 0x7f, ux_pages[page].page);
		if (ret)
			return ret;
		for (reg = 0x80; reg <= ux_pages[page].end; reg++) {
			ret = write(context, reg, cal[reg + ux_pages[page].offset]);
			if (ret)
				return ret;
		}
	}
	for (reg = 0; reg <= 0x6c; reg++) {
		ret = write(context, reg, cal[reg + 5]);
		if (ret)
			return ret;
	}
	ret = write(context, 0x7f, 0x80);
	if (ret)
		return ret;
	ret = write(context, 0x90, cal[0x95] | 8);
	return ret ? ret : write(context, 0x90, cal[0x95] & ~8);
}

#endif
